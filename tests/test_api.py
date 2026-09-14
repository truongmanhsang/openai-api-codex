import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.upstream import OpenAIUpstream


def make_client(handler, tmp_path: Path) -> TestClient:
    settings = Settings(
        auth_file=tmp_path / "auth.json",
        access_token="test-token",
        upstream_base_url="https://chatgpt.com/backend-api/codex",
        request_timeout=3.0,
        models=["gpt-5.6-sol", "gpt-image-2"],
        codex_model="gpt-5.6-sol",
        image_host_model="gpt-5.5",
        image_model="gpt-image-2",
    )
    transport = httpx.MockTransport(handler)
    upstream = OpenAIUpstream(settings, transport=transport)
    return TestClient(create_app(settings=settings, upstream=upstream))


def test_chat_completion_forwards_payload_and_auth(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/backend-api/codex/responses"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["originator"] == "unlimited-ai"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.6-sol"
        assert payload["store"] is False
        assert payload["stream"] is True
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b'data: {"type":"response.output_text.delta","delta":"hello"}\n\ndata: {"type":"response.completed","response":{"id":"resp-1"}}\n\ndata: [DONE]\n\n')

    with make_client(handler, tmp_path) as client:
        response = client.post("/v1/chat/completions", json={"model": "gpt-5.6-sol", "messages": [{"role": "user", "content": "Hi"}]})

    assert response.status_code == 200
    assert response.json()["object"] == "chat.completion"
    assert response.json()["choices"][0]["message"]["content"] == "hello"


def test_streaming_is_rejected(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("upstream must not be called")

    with make_client(handler, tmp_path) as client:
        response = client.post("/v1/chat/completions", json={"stream": True})

    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"


def test_image_generation_forwards_payload(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/backend-api/codex/responses"
        payload = json.loads(request.content)
        assert payload["model"] == "gpt-5.5"
        assert payload["tools"][0]["model"] == "gpt-image-2"
        assert payload["tools"][0]["size"] == "1024x1024"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=b'data: {"type":"image_generation_call","result":"abc"}\n\ndata: [DONE]\n\n')

    with make_client(handler, tmp_path) as client:
        response = client.post("/v1/images/generations", json={"prompt": "a red kite", "model": "gpt-image-2"})

    assert response.status_code == 200
    assert response.json()["data"][0]["b64_json"] == "abc"


def test_models_and_health(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("catalog endpoints must not call upstream")

    with make_client(handler, tmp_path) as client:
        assert client.get("/health").json() == {"status": "ok"}
        models = client.get("/v1/models")

    assert models.json()["object"] == "list"
    assert [item["id"] for item in models.json()["data"]] == ["gpt-5.6-sol", "gpt-image-2"]


def test_upstream_http_error_is_openai_shaped(tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": {"message": "slow down", "type": "rate_limit_error"}})

    with make_client(handler, tmp_path) as client:
        response = client.post("/v1/images/generations", json={"prompt": "x"})

    assert response.status_code == 429
    assert response.json()["error"]["message"] == "slow down"
    assert "test-token" not in response.text


@pytest.mark.parametrize("exc, status", [(httpx.ConnectError("down"), 502), (httpx.ReadTimeout("slow"), 504)])
def test_upstream_transport_errors_are_mapped(exc: Exception, status: int, tmp_path: Path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    with make_client(handler, tmp_path) as client:
        response = client.post("/v1/images/generations", json={"prompt": "x"})

    assert response.status_code == status
    assert "test-token" not in response.text
