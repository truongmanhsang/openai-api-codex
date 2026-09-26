# OpenAI-Compatible Codex Auth Adapter

# openai-api-codex

Local FastAPI service that exposes a small OpenAI-compatible API and forwards requests to the ChatGPT Codex backend using the OAuth access token stored in Codex auth configuration.

## Start

The default Compose setup mounts `/Users/truongmanhsang/.codex/auth.json` read-only and binds the service to `127.0.0.1:8000`.

```sh
docker compose up --build
```

Use `CODEX_AUTH_FILE=/path/to/auth.json docker compose up --build` for another machine. The auth file must contain a non-empty `tokens.access_token` field. The proxy has no client authentication, so keep it on a trusted local/private network.

When a chat request includes tools but omits `tool_choice`, the Compose setup defaults to `required` so tool-driven clients such as Hindsight Reflect receive a tool call. Set `DEFAULT_TOOL_CHOICE=auto` to keep the OpenAI-compatible optional-tool default.

## Endpoints

```sh
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/v1/models

curl http://127.0.0.1:8000/v1/chat/completions \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-5","messages":[{"role":"user","content":"Say hello."}]}'

curl http://127.0.0.1:8000/v1/images/generations \
  -H 'content-type: application/json' \
  -d '{"model":"gpt-image-1","prompt":"A red kite over a green field","size":"1024x1024"}'
```

Image responses use the upstream JSON shape. Depending on the request, image data may be returned as base64 JSON. Requests are not persisted, and streaming/SSE plus the Responses API are not supported in this first release.

## Development

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

The tests mock upstream HTTP calls and do not need a live API key or network access.
