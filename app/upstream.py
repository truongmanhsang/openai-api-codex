from __future__ import annotations

import base64
import json
import uuid
from typing import Any

import httpx

from .config import Settings


class UpstreamHTTPError(RuntimeError):
    def __init__(self, status_code: int, body: Any):
        self.status_code = status_code
        self.body = body


class UpstreamOutputError(RuntimeError):
    """Raised when a successful Responses request contains no usable chat output."""


class OpenAIUpstream:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self.transport = transport

    def _headers(self, token: str) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "User-Agent": "codex_cli_rs/0.0.0 (Unlimited AI)",
            "originator": "unlimited-ai",
        }
        account_id = self._account_id_from_token(token)
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        return headers

    @staticmethod
    def _account_id_from_token(token: str) -> str | None:
        try:
            part = token.split(".")[1]
            part += "=" * (-len(part) % 4)
            payload = json.loads(base64.urlsafe_b64decode(part))
            claims = payload.get("https://api.openai.com/auth", {})
            account_id = claims.get("chatgpt_account_id")
            return account_id if isinstance(account_id, str) and account_id else None
        except (IndexError, ValueError, TypeError, json.JSONDecodeError, base64.binascii.Error):
            return None

    async def _responses(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        token = self.settings.bearer_token()
        async with httpx.AsyncClient(base_url=self.settings.upstream_base_url.rstrip("/"), timeout=self.settings.request_timeout, transport=self.transport) as client:
            response = await client.post("/responses", headers=self._headers(token), json=payload)
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                body = response.json()
            except ValueError:
                body = {"error": {"message": "Codex backend returned invalid JSON", "type": "upstream_error"}}
            raise UpstreamHTTPError(response.status_code, body) from exc
        events: list[dict[str, Any]] = []
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                events.append(event)
        if events:
            return events
        try:
            body = response.json()
        except ValueError as exc:
            raise RuntimeError("Codex backend returned no usable response events") from exc
        return [body] if isinstance(body, dict) else []

    async def chat_completions(self, payload: dict[str, Any]) -> dict[str, Any]:
        messages = payload.get("messages", [])
        input_messages = self._chat_messages_to_responses(messages)
        response_payload = {"model": payload.get("model") or self.settings.codex_model, "instructions": payload.get("instructions") or "You are a helpful assistant.", "input": input_messages, "store": False, "stream": True}
        if isinstance(payload.get("tools"), list):
            response_payload["tools"] = self._responses_tools(payload["tools"])
        if "tool_choice" in payload:
            response_payload["tool_choice"] = self._responses_tool_choice(payload["tool_choice"])
        elif response_payload.get("tools"):
            # Tool-driven callers such as Hindsight Reflect need a tool result
            # each turn; Codex may otherwise choose to answer in plain text.
            response_payload["tool_choice"] = self.settings.default_tool_choice
        if "parallel_tool_calls" in payload:
            response_payload["parallel_tool_calls"] = payload["parallel_tool_calls"]
        if isinstance(payload.get("reasoning_effort"), str):
            response_payload["reasoning"] = {"effort": payload["reasoning_effort"]}
        if "max_completion_tokens" in payload:
            response_payload["max_output_tokens"] = payload["max_completion_tokens"]
        events = await self._responses(response_payload)
        completed = next((event.get("response") for event in reversed(events) if isinstance(event.get("response"), dict)), {})
        tool_calls = self._extract_function_calls(events)
        text = self._extract_text(events, required=False)
        if not tool_calls and text is None:
            raise UpstreamOutputError(self._output_diagnostic(events))
        message: dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        return {"id": f"chatcmpl-{completed.get('id', uuid.uuid4().hex)}", "object": "chat.completion", "created": completed.get("created_at"), "model": response_payload["model"], "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tool_calls else "stop"}]}

    async def image_generations(self, payload: dict[str, Any]) -> dict[str, Any]:
        tool = {"type": "image_generation", "model": self.settings.image_model, "size": payload.get("size", "1024x1024"), "quality": payload.get("quality", "medium"), "output_format": payload.get("output_format", "png"), "background": payload.get("background", "opaque"), "partial_images": 0}
        response_payload = {"model": self.settings.image_host_model, "store": False, "instructions": "You are an assistant that must fulfill image generation and image editing requests by using the image_generation tool when provided.", "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": payload.get("prompt", "")}]}], "tools": [tool], "stream": True}
        events = await self._responses(response_payload)
        image = self._extract_image(events)
        if not image:
            raise RuntimeError("Codex image response contained no image_generation_call result")
        return {"created": int(next((event.get("created_at") for event in events if event.get("created_at")), 0)), "data": [{"b64_json": image}]}

    @staticmethod
    def _message_content(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "input_text", "text": content}]
        return content if isinstance(content, list) else [{"type": "input_text", "text": str(content)}]

    @staticmethod
    def _chat_messages_to_responses(messages: Any) -> list[dict[str, Any]]:
        input_items: list[dict[str, Any]] = []
        if not isinstance(messages, list):
            return input_items

        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role", "user")
            content = message.get("content")

            if role == "tool":
                call_id = message.get("tool_call_id")
                if isinstance(call_id, str) and call_id:
                    input_items.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": OpenAIUpstream._function_output(content),
                    })
                continue

            if role == "assistant":
                if content is not None and content != "":
                    input_items.append({
                        "type": "message",
                        "role": "assistant",
                        "content": OpenAIUpstream._message_content(content),
                    })
                tool_calls = message.get("tool_calls")
                if isinstance(tool_calls, list):
                    for tool_call in tool_calls:
                        if not isinstance(tool_call, dict) or tool_call.get("type", "function") != "function":
                            continue
                        function = tool_call.get("function")
                        call_id = tool_call.get("id")
                        if not isinstance(function, dict) or not isinstance(call_id, str) or not call_id:
                            continue
                        arguments = function.get("arguments", "{}")
                        if not isinstance(arguments, str):
                            arguments = json.dumps(arguments, ensure_ascii=False)
                        input_items.append({
                            "type": "function_call",
                            "call_id": call_id,
                            "name": function.get("name", ""),
                            "arguments": arguments,
                        })
                continue

            input_items.append({
                "type": "message",
                "role": "developer" if role == "system" else role,
                "content": OpenAIUpstream._message_content(content if content is not None else ""),
            })
        return input_items

    @staticmethod
    def _function_output(content: Any) -> str | list[dict[str, Any]]:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            output: list[dict[str, Any]] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    output.append({"type": "input_text", "text": str(item.get("text", ""))})
                elif isinstance(item, dict):
                    output.append(item)
            return output
        if content is None:
            return ""
        return json.dumps(content, ensure_ascii=False)

    @staticmethod
    def _responses_tools(tools: list[Any]) -> list[dict[str, Any]]:
        responses_tools: list[dict[str, Any]] = []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
                function = tool["function"]
                converted: dict[str, Any] = {"type": "function", "name": function.get("name", "")}
                for key in ("description", "parameters"):
                    if key in function:
                        converted[key] = function[key]
                # Chat Completions defaults strict to false; make that explicit
                # because Responses defaults it differently.
                strict = function.get("strict", False)
                converted["strict"] = strict if isinstance(strict, bool) else False
                responses_tools.append(converted)
            else:
                # Keep Responses-native and provider-specific tools intact.
                responses_tools.append(tool)
        return responses_tools

    @staticmethod
    def _responses_tool_choice(tool_choice: Any) -> Any:
        if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
            function = tool_choice.get("function")
            if isinstance(function, dict) and isinstance(function.get("name"), str):
                return {"type": "function", "name": function["name"]}
        return tool_choice

    @staticmethod
    def _extract_text(events: list[dict[str, Any]], required: bool = True) -> str | None:
        deltas = [event["delta"] for event in events if event.get("type") == "response.output_text.delta" and isinstance(event.get("delta"), str) and event["delta"]]
        if deltas:
            return "".join(deltas)
        refusal_deltas = [event["delta"] for event in events if event.get("type") == "response.refusal.delta" and isinstance(event.get("delta"), str) and event["delta"]]
        if refusal_deltas:
            return "".join(refusal_deltas)
        for event in events:
            if event.get("type") == "response.output_text.done" and isinstance(event.get("text"), str) and event["text"].strip():
                return event["text"]
            if event.get("type") == "response.refusal.done" and isinstance(event.get("refusal"), str) and event["refusal"].strip():
                return event["refusal"]
        for event in reversed(events):
            candidate = event.get("response") if isinstance(event.get("response"), dict) else event
            if isinstance(candidate.get("output_text"), str) and candidate["output_text"].strip():
                return candidate["output_text"]
            output = candidate.get("output")
            if isinstance(output, list):
                text_parts: list[str] = []
                for item in output:
                    if not isinstance(item, dict) or item.get("type") != "message":
                        continue
                    content = item.get("content")
                    if not isinstance(content, list):
                        continue
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                            text_parts.append(part["text"])
                        elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                            text_parts.append(part["refusal"])
                if text_parts:
                    return "".join(text_parts)
        if required:
            raise RuntimeError("Codex Responses API returned no text output")
        return None

    @staticmethod
    def _output_diagnostic(events: list[dict[str, Any]]) -> str:
        event_types: list[str] = []
        output_types: list[str] = []
        response_status: str | None = None
        incomplete_reason: str | None = None
        error_types: list[str] = []

        for event in events:
            event_type = event.get("type")
            if isinstance(event_type, str) and event_type not in event_types:
                event_types.append(event_type)
            item = event.get("item")
            if isinstance(item, dict) and isinstance(item.get("type"), str) and item["type"] not in output_types:
                output_types.append(item["type"])
            response = event.get("response") if isinstance(event.get("response"), dict) else event
            status = response.get("status")
            if isinstance(status, str):
                response_status = status
            details = response.get("incomplete_details")
            if isinstance(details, dict) and isinstance(details.get("reason"), str):
                incomplete_reason = details["reason"]
            output = response.get("output")
            if isinstance(output, list):
                for output_item in output:
                    if isinstance(output_item, dict) and isinstance(output_item.get("type"), str) and output_item["type"] not in output_types:
                        output_types.append(output_item["type"])
            error = response.get("error")
            if isinstance(error, dict):
                # Include only non-sensitive error classifications, never messages.
                for key in ("type", "code"):
                    value = error.get(key)
                    if isinstance(value, str) and value not in error_types:
                        error_types.append(value)

        parts = ["Codex Responses API returned neither text nor a function call"]
        if response_status:
            parts.append(f"status={response_status}")
        if incomplete_reason:
            parts.append(f"incomplete_reason={incomplete_reason}")
        if event_types:
            parts.append(f"events={','.join(event_types[:12])}")
        if output_types:
            parts.append(f"output_types={','.join(output_types[:12])}")
        if error_types:
            parts.append(f"error_classification={','.join(error_types[:4])}")
        return "; ".join(parts)

    @staticmethod
    def _extract_function_calls(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        by_call_id: dict[str, dict[str, Any]] = {}
        item_to_call_id: dict[str, str] = {}

        def add_item(item: Any) -> None:
            if not isinstance(item, dict) or item.get("type") != "function_call":
                return
            item_id = item.get("id")
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                call_id = item_to_call_id.get(item_id) if isinstance(item_id, str) else None
            if not isinstance(call_id, str) or not call_id:
                return
            record = by_call_id.get(call_id)
            if record is None:
                record = {"id": call_id, "type": "function", "function": {}}
                records.append(record)
                by_call_id[call_id] = record
            if isinstance(item_id, str) and item_id:
                item_to_call_id[item_id] = call_id
            function = record["function"]
            if isinstance(item.get("name"), str):
                function["name"] = item["name"]
            if isinstance(item.get("arguments"), str):
                function["arguments"] = item["arguments"]

        for event in events:
            event_type = event.get("type")
            if event_type in {"response.output_item.added", "response.output_item.done"}:
                add_item(event.get("item"))
            elif event_type == "response.function_call_arguments.done":
                item_id = event.get("item_id")
                call_id = item_to_call_id.get(item_id) if isinstance(item_id, str) else None
                if not call_id and isinstance(event.get("call_id"), str):
                    call_id = event["call_id"]
                record = by_call_id.get(call_id) if isinstance(call_id, str) else None
                if record is None and isinstance(call_id, str):
                    record = {"id": call_id, "type": "function", "function": {}}
                    records.append(record)
                    by_call_id[call_id] = record
                if record is not None:
                    if isinstance(event.get("name"), str):
                        record["function"]["name"] = event["name"]
                    if isinstance(event.get("arguments"), str):
                        record["function"]["arguments"] = event["arguments"]
            elif event_type == "response.completed" and isinstance(event.get("response"), dict):
                output = event["response"].get("output")
                if isinstance(output, list):
                    for item in output:
                        add_item(item)
            elif event_type == "function_call":
                add_item(event)
            elif isinstance(event.get("output"), list):
                for item in event["output"]:
                    add_item(item)

        return [
            {
                "id": record["id"],
                "type": "function",
                "function": {
                    "name": record["function"].get("name", ""),
                    "arguments": record["function"].get("arguments", "{}"),
                },
            }
            for record in records
            if record["function"].get("name")
        ]

    @staticmethod
    def _extract_image(events: list[dict[str, Any]]) -> str | None:
        def walk(value: Any) -> str | None:
            if isinstance(value, dict):
                if value.get("type") == "image_generation_call" and isinstance(value.get("result"), str):
                    return value["result"]
                for child in value.values():
                    found = walk(child)
                    if found:
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = walk(child)
                    if found:
                        return found
            return None
        return walk(events)
