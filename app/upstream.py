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
        input_messages = [{"type": "message", "role": item.get("role", "user"), "content": self._message_content(item.get("content", ""))} for item in messages if isinstance(item, dict)]
        response_payload = {"model": payload.get("model") or self.settings.codex_model, "instructions": payload.get("instructions") or "You are a helpful assistant.", "input": input_messages, "store": False, "stream": True}
        events = await self._responses(response_payload)
        text = self._extract_text(events)
        completed = next((event.get("response") for event in reversed(events) if isinstance(event.get("response"), dict)), {})
        return {"id": f"chatcmpl-{completed.get('id', uuid.uuid4().hex)}", "object": "chat.completion", "created": completed.get("created_at"), "model": response_payload["model"], "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}]}

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
    def _extract_text(events: list[dict[str, Any]]) -> str:
        deltas = [event["delta"] for event in events if event.get("type") == "response.output_text.delta" and isinstance(event.get("delta"), str)]
        if deltas:
            return "".join(deltas)
        for event in reversed(events):
            candidate = event.get("response") if isinstance(event.get("response"), dict) else event
            if isinstance(candidate.get("output_text"), str) and candidate["output_text"].strip():
                return candidate["output_text"]
        raise RuntimeError("Codex Responses API returned no text output")

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
