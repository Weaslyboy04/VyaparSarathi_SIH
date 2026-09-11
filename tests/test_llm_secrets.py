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
# Full relative path, not bare filename — a generically-named "client.py" in
# some other source adapter must not slip through just by sharing a name
# with an already-trusted file (CLAUDE.md §24: secret-unwrapping stays
# auditable to a short, explicit list).
_ALLOWED_IN = {
    "llm/provider.py",
    "llm/gemini_provider.py",
    # AGMARKNET's own API key (unrelated to the LLM secrets above) is
    # unwrapped only at its one outbound-request call site, exactly like the
    # two LLM providers above unwrap theirs.
    "sources/agmarknet/client.py",
}


def test_only_provider_calls_get_secret_value() -> None:
    offenders: list[str] = []
    for py in sorted(_SRC_DIR.rglob("*.py")):
        rel = py.relative_to(_SRC_DIR).as_posix()
        if rel in _ALLOWED_IN:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get_secret_value":
                    offenders.append(rel)
    assert not offenders, f"only {sorted(_ALLOWED_IN)} may call .get_secret_value(): {offenders}"


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


def test_settings_repr_does_not_leak_the_gemini_keys() -> None:
    settings = Settings(
        gemini_extractor_api_key=SecretStr("extractor-secret"),
        gemini_verifier_api_key=SecretStr("verifier-secret"),
    )
    assert "extractor-secret" not in repr(settings)
    assert "extractor-secret" not in str(settings)
    assert "verifier-secret" not in repr(settings)
    assert "verifier-secret" not in str(settings)


def test_settings_json_dump_does_not_leak_the_gemini_keys() -> None:
    settings = Settings(
        gemini_extractor_api_key=SecretStr("extractor-secret"),
        gemini_verifier_api_key=SecretStr("verifier-secret"),
    )
    dumped = settings.model_dump_json()
    assert "extractor-secret" not in dumped
    assert "verifier-secret" not in dumped


def test_settings_repr_does_not_leak_the_agmarknet_key() -> None:
    settings = Settings(agmarknet_api_key=SecretStr("agmarknet-secret"))
    assert "agmarknet-secret" not in repr(settings)
    assert "agmarknet-secret" not in str(settings)


def test_settings_json_dump_does_not_leak_the_agmarknet_key() -> None:
    settings = Settings(agmarknet_api_key=SecretStr("agmarknet-secret"))
    assert "agmarknet-secret" not in settings.model_dump_json()


def test_agmarknet_credential_in_base_url_is_rejected() -> None:
    with pytest.raises(ConfigError):
        Settings(agmarknet_base_url="https://api.data.gov.in/resource/x?api-key=abc123")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
