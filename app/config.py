from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping


class ConfigurationError(RuntimeError):
    """Raised when Codex credential configuration is unusable."""


def load_access_token(env: Mapping[str, str], auth_file: Path) -> str:
    value = env.get("CODEX_ACCESS_TOKEN", "").strip()
    if value:
        return value
    if "CODEX_ACCESS_TOKEN" in env:
        raise ConfigurationError("CODEX_ACCESS_TOKEN is empty")
    try:
        payload = json.loads(auth_file.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigurationError(f"auth file not found: {auth_file}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"auth file is not valid JSON: {auth_file}") from exc
    tokens = payload.get("tokens") if isinstance(payload, dict) else None
    value = tokens.get("access_token") if isinstance(tokens, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError("auth file does not contain a usable tokens.access_token")
    return value.strip()


@dataclass(frozen=True)
class Settings:
    auth_file: Path = field(default_factory=lambda: Path(os.getenv("AUTH_FILE", "/run/secrets/codex-auth.json")))
    access_token: str | None = None
    upstream_base_url: str = field(default_factory=lambda: os.getenv("UPSTREAM_BASE_URL", "https://chatgpt.com/backend-api/codex"))
    request_timeout: float = field(default_factory=lambda: float(os.getenv("REQUEST_TIMEOUT", "120")))
    codex_model: str = field(default_factory=lambda: os.getenv("CODEX_MODEL", "gpt-5.6-sol"))
    default_tool_choice: str = field(default_factory=lambda: os.getenv("DEFAULT_TOOL_CHOICE", "auto"))
    image_host_model: str = field(default_factory=lambda: os.getenv("CODEX_IMAGE_HOST_MODEL", "gpt-5.5"))
    image_model: str = field(default_factory=lambda: os.getenv("CODEX_IMAGE_MODEL", "gpt-image-2"))
    models: list[str] = field(default_factory=lambda: ["gpt-5.6-sol", "gpt-image-2"])

    def bearer_token(self) -> str:
        return self.access_token or load_access_token(os.environ, self.auth_file)
