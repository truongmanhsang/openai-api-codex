from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .config import ConfigurationError, Settings
from .upstream import OpenAIUpstream, UpstreamHTTPError


def error_response(message: str, error_type: str, status_code: int, code: str | None = None) -> JSONResponse:
    error: dict[str, str] = {"message": message, "type": error_type}
    if code:
        error["code"] = code
    return JSONResponse({"error": error}, status_code=status_code)


def create_app(settings: Settings | None = None, upstream: OpenAIUpstream | None = None) -> FastAPI:
    settings = settings or Settings()
    upstream = upstream or OpenAIUpstream(settings)
    app = FastAPI(title="OpenAI-compatible Codex adapter", version="0.1.0")

    @app.get("/health", response_model=None)
    async def health() -> dict[str, str] | JSONResponse:
        try:
            settings.bearer_token()
        except ConfigurationError as exc:
            return error_response(str(exc), "configuration_error", 500)
        return {"status": "ok"}

    @app.get("/v1/models")
    async def models() -> dict[str, Any]:
        return {
            "object": "list",
            "data": [{"id": model, "object": "model", "owned_by": "openai"} for model in settings.models],
        }

    async def forward(request: Request, operation: str) -> JSONResponse:
        try:
            payload = await request.json()
        except ValueError:
            return error_response("Request body must be valid JSON", "invalid_request_error", 400)
        if not isinstance(payload, dict):
            return error_response("Request body must be a JSON object", "invalid_request_error", 400)
        if operation == "chat" and payload.get("stream") is True:
            return error_response("Streaming is not supported", "invalid_request_error", 400, "unsupported_parameter")
        try:
            body = await (upstream.chat_completions(payload) if operation == "chat" else upstream.image_generations(payload))
            return JSONResponse(body)
        except ConfigurationError as exc:
            return error_response(str(exc), "configuration_error", 500)
        except UpstreamHTTPError as exc:
            body = exc.body if isinstance(exc.body, dict) and "error" in exc.body else {"error": {"message": "Upstream request failed", "type": "upstream_error"}}
            return JSONResponse(body, status_code=exc.status_code)
        except httpx.ReadTimeout:
            return error_response("Upstream request timed out", "upstream_timeout", 504)
        except httpx.ConnectError:
            return error_response("Could not connect to upstream", "upstream_connection_error", 502)

    @app.post("/v1/chat/completions")
    async def chat(request: Request) -> JSONResponse:
        return await forward(request, "chat")

    @app.post("/v1/images/generations")
    async def images(request: Request) -> JSONResponse:
        return await forward(request, "image")

    return app


app = create_app()
