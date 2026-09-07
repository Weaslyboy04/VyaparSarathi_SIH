"""`Settings.llm_api_key` secret handling (CLAUDE.md §24, §25 Phase 6)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import SecretStr

import vyaparsarathi as pkg
from vyaparsarathi.config.settings import Settings
from vyaparsarathi.errors import ConfigError

_SRC_DIR = Path(pkg.__file__).parent
_ALLOWED_IN = {"provider.py"}


def test_only_provider_calls_get_secret_value() -> None:
    offenders: list[str] = []
    for py in sorted(_SRC_DIR.rglob("*.py")):
        if py.name in _ALLOWED_IN:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get_secret_value":
                    offenders.append(py.name)
    assert not offenders, f"only llm/provider.py may call .get_secret_value(): {offenders}"


def test_settings_repr_does_not_leak_the_key() -> None:
    settings = Settings(llm_api_key=SecretStr("super-secret-key"))
    assert "super-secret-key" not in repr(settings)
    assert "super-secret-key" not in str(settings)


def test_settings_json_dump_does_not_leak_the_key() -> None:
    settings = Settings(llm_api_key=SecretStr("super-secret-key"))
    dumped = settings.model_dump_json()
    assert "super-secret-key" not in dumped


def test_credential_in_base_url_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings(llm_base_url="https://llm.example.com/v1?key=abc123")
    with pytest.raises(ConfigError):
        Settings(llm_base_url="https://llm.example.com/v1?token=abc123")


def test_plain_base_url_is_accepted() -> None:
    Settings(llm_base_url="https://llm.example.com/v1/complete")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
