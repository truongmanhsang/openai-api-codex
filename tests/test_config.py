import json
from pathlib import Path

import pytest

from app.config import ConfigurationError, load_access_token


def test_environment_token_takes_precedence(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "file-token"}}))

    assert load_access_token({"CODEX_ACCESS_TOKEN": "env-token"}, auth_file) == "env-token"


def test_loads_token_from_auth_json(tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "file-token"}}))

    assert load_access_token({}, auth_file) == "file-token"


@pytest.mark.parametrize("payload", ["{", "[]", "{}", '{"tokens": {"access_token": ""}}'])
def test_rejects_invalid_auth_file(payload: str, tmp_path: Path) -> None:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(payload)

    with pytest.raises(ConfigurationError) as exc_info:
        load_access_token({}, auth_file)

    assert "file-token" not in str(exc_info.value)


def test_rejects_missing_auth_file(tmp_path: Path) -> None:
    auth_file = tmp_path / "missing.json"

    with pytest.raises(ConfigurationError, match="not found"):
        load_access_token({}, auth_file)


def test_rejects_empty_environment_token(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="CODEX_ACCESS_TOKEN"):
        load_access_token({"CODEX_ACCESS_TOKEN": "   "}, tmp_path / "missing.json")
