# OpenAI-Compatible Codex Auth Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a local FastAPI service that exposes OpenAI-compatible chat, image, model, and health endpoints while using the official OpenAI API and a read-only mounted Codex auth file.

**Architecture:** Keep configuration/auth loading, upstream HTTP transport, response/error translation, and FastAPI routing in focused modules. The service forwards non-streaming chat and image requests to `https://api.openai.com/v1`, serves a configured local model catalog, and does not persist request data.

**Tech Stack:** Python 3.12, FastAPI, Uvicorn, httpx, Pydantic Settings, pytest, pytest-asyncio, Docker Compose.

## Global Constraints

- First release supports `/health`, `/v1/models`, `/v1/chat/completions`, and `/v1/images/generations`.
- Text generation is non-streaming; Responses API and streaming/SSE are out of scope.
- The proxy is unauthenticated and defaults to host binding `127.0.0.1:8000`.
- Compose mounts `/Users/truongmanhsang/.codex/auth.json` read-only at `/run/secrets/codex-auth.json`.
- Never print, persist, return, or bake credentials into the image.
- Do not automatically retry image-generation requests.
- Tests must use mocked upstream HTTP calls and must not require live credentials or network access.

## Files and responsibilities

- Create `app/config.py`: typed environment settings and auth-file loading.
- Create `app/upstream.py`: async OpenAI HTTP client and safe upstream error mapping.
- Create `app/main.py`: FastAPI routes and OpenAI-compatible response envelopes.
- Create `tests/test_config.py`: auth/configuration tests.
- Create `tests/test_api.py`: route and upstream behavior tests.
- Create `pyproject.toml`: dependencies and pytest configuration.
- Create `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, and `README.md`.

### Task 1: Create the Python service skeleton

**Files:** Create `pyproject.toml`, `app/__init__.py`, `app/config.py`, `app/upstream.py`, `app/main.py`, `tests/__init__.py`.

**Interfaces:** `Settings` exposes `auth_file`, `openai_api_key`, `upstream_base_url`, `request_timeout`, and `models`. `OpenAIUpstream` exposes async `chat_completions(payload)` and `image_generations(payload)`. FastAPI exports `app`.

- [ ] Add runtime dependencies: `fastapi`, `uvicorn[standard]`, `httpx`, `pydantic-settings`; test dependencies: `pytest`, `pytest-asyncio`.
- [ ] Add a minimal `Settings` class that reads `OPENAI_API_KEY` first and otherwise loads only the `OPENAI_API_KEY` JSON field from `AUTH_FILE`.
- [ ] Add initial route wiring and an upstream client constructor with dependency injection for tests.
- [ ] Run `python -m pytest -q`; expect collection to pass with no tests yet.

### Task 2: Implement configuration and auth loading using TDD

**Files:** Modify `app/config.py`; create `tests/test_config.py`.

**Interfaces:** `load_api_key(env: Mapping[str, str], auth_file: Path) -> str` raises `ConfigurationError` with secret-free messages.

- [ ] Write tests for environment override, JSON-file loading, missing file, malformed JSON, missing key, and empty key.
- [ ] Run `pytest tests/test_config.py -q`; expect the new tests to fail before implementation.
- [ ] Implement exact precedence: non-empty `OPENAI_API_KEY`, then JSON `OPENAI_API_KEY`; reject all other shapes.
- [ ] Ensure exception strings contain paths/status context but never key values.
- [ ] Run `pytest tests/test_config.py -q`; expect all tests to pass.

### Task 3: Implement upstream transport and API routes using TDD

**Files:** Modify `app/upstream.py` and `app/main.py`; create `tests/test_api.py`.

**Interfaces:** `OpenAIUpstream` sends bearer-authenticated JSON to `/chat/completions` and `/images/generations`, using `httpx.AsyncClient` and a finite timeout. Routes return upstream JSON unchanged on success. `GET /v1/models` returns `{ "object": "list", "data": [...] }` from settings.

- [ ] Write mocked-transport tests for chat forwarding, image forwarding, model listing, health, upstream `4xx`/`5xx`, connect errors, timeout errors, missing auth, and secret-free errors.
- [ ] Run `pytest tests/test_api.py -q`; expect failures for missing routes/client behavior.
- [ ] Implement request pass-through for supported JSON fields, reject `stream=true` with a clear `400`, and avoid logging request bodies or authorization headers.
- [ ] Map upstream HTTP errors to OpenAI-style `{ "error": { "message": ..., "type": ..., "code": ... } }`; map connect errors to `502` and timeouts to `504`.
- [ ] Make `/health` return `{"status":"ok"}` only when configuration can load; do not include the API key or full auth file.
- [ ] Run `pytest -q`; expect the complete suite to pass.

### Task 4: Add container packaging and operator documentation

**Files:** Create `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, `README.md`.

- [ ] Build a slim Python image, install the project dependencies declared in `pyproject.toml`, create/use a non-root runtime user, and run `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- [ ] Mount `${CODEX_AUTH_FILE:-/Users/truongmanhsang/.codex/auth.json}` to `/run/secrets/codex-auth.json:ro`, set `AUTH_FILE`, and bind `${HOST_BIND:-127.0.0.1}:8000:8000`.
- [ ] Document curl examples for chat, image generation, models, and health; document that image calls may return base64 JSON and that the proxy is intentionally unauthenticated.
- [ ] Document the security boundary: trusted local/private network only, read-only auth mount, and no credential in logs.
- [ ] Run `docker compose config`; expect valid rendered Compose configuration without printing secret contents.

### Task 5: Verify the finished service

**Files:** Modify only files needed by verification fixes.

- [ ] Run `pytest -q`; expect all tests to pass.
- [ ] Run `python -m compileall app`; expect no syntax errors.
- [ ] Run `docker build -t openai-api-codex .`; expect a successful image build.
- [ ] Start Compose with a temporary test auth JSON, call `/health` and `/v1/models`, then stop Compose; verify the mounted source auth file checksum is unchanged.
- [ ] Inspect `git diff --check` and scan source/docs for accidental credential literals before handoff.
