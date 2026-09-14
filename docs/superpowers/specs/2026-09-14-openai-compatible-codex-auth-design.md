# OpenAI-Compatible API Adapter Using Codex Auth

## Goal

Build a small local FastAPI service that exposes a useful OpenAI-compatible HTTP surface while using the official OpenAI API upstream. The service must read the existing Codex credential configuration at runtime and must never copy, print, persist, or return the credential.

## Scope

The first release supports:

- `GET /health`
- `GET /v1/models`
- `POST /v1/chat/completions`
- `POST /v1/images/generations`

Text generation is non-streaming in the first release. Image generation returns the upstream JSON response, including base64 image data when requested by the upstream API. Prompt and image data are not persisted by the service.

The proxy itself is unauthenticated because it is intended for a trusted local/private network. The deployment defaults to binding only to `127.0.0.1` on host port `8000`.

## Authentication and configuration

Docker Compose mounts `/Users/truongmanhsang/.codex/auth.json` read-only into the container at `/run/secrets/codex-auth.json` and sets `AUTH_FILE` to that path. The application reads the `OPENAI_API_KEY` field from the JSON. An environment-variable override is supported for portability and tests, but the Compose setup uses the mounted file.

The upstream base URL, auth file path, host bind address, port, request timeout, and model catalog are configuration values. Secrets are excluded from logs and error messages. The container runs as a non-root user where practical.

## Architecture and data flow

FastAPI validates the incoming JSON and delegates to a small async upstream client. The client forwards compatible request fields to `https://api.openai.com/v1/chat/completions` or `https://api.openai.com/v1/images/generations`, adding the internal upstream bearer token. Responses and safe upstream errors are translated back to OpenAI-style JSON. `/v1/models` uses a configurable local catalog rather than requiring a network call, while `/health` reports process/configuration health without revealing credentials.

The implementation should keep HTTP routing, configuration/auth loading, upstream transport, and response/error translation in separate focused modules so each can be tested independently.

## Error handling

- Missing, malformed, or unusable auth configuration produces a clear configuration error without exposing secret values.
- Invalid request JSON and unsupported input produce OpenAI-style `4xx` responses.
- Safe upstream HTTP errors preserve the relevant status and structured error body when possible.
- Connection and timeout failures map to `502` and `504` respectively.
- Requests use a finite timeout.
- No automatic retries are performed for image generation.

## Docker and local operation

Provide a `Dockerfile`, `docker-compose.yml`, `.dockerignore`, `.env.example`, and concise README instructions. Compose must not bake the auth file into the image; it must use a read-only bind mount. The default host binding is local-only and can be changed explicitly for private-network deployments.

## Testing and acceptance criteria

Use mocked upstream HTTP calls; tests must not require a live OpenAI credential or network access. Cover:

- auth loading from the environment and JSON file;
- chat completion forwarding and response shape;
- image generation forwarding and response shape;
- model catalog and health endpoints;
- malformed/missing auth;
- upstream HTTP, connection, and timeout failures;
- absence of secret values in error responses and logs where practical.

Acceptance requires the service to start with Docker Compose when a valid auth file is mounted, answer the documented endpoints, pass the test suite, and leave the host auth file unchanged.

## Out of scope

Chat streaming/SSE, the Responses API, embeddings, audio, files, assistants, persistent storage, rate limiting, multi-user authentication, and automatic model discovery are deferred until a separate requirement justifies them.

## Related

- [[entities/openai-api-codex-auth-adapter|OpenAI API Codex Auth Adapter]]
- [[decisions/openai-compatible-chat-and-images-scope|OpenAI-Compatible Chat and Images Scope]]
