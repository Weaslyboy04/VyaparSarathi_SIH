"""`llm/gemini_provider.py::GeminiLlmProvider` with `role="conversation"` —
the Phase 6 conversational adapter reading the `Settings.llm_*` block
(`VYAPAR_LLM_API_KEY` / `VYAPAR_LLM_MODEL` / ...), NOT the Phase 5
extractor/verifier `gemini_*` block. `@respx.mock` on the fixed Gemini
endpoint; no test in this file touches the network (CLAUDE.md §28).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.gemini_provider import GeminiLlmProvider
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole

_URL = "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"


def _conv_settings(settings: Settings) -> Settings:
    """Explicit overrides for every field the conversation role reads — never
    relies on `.env`'s real, git-ignored key material or model choice."""
    return settings.model_copy(
        update={
            "llm_enabled": True,
            "llm_base_url": "",  # forces the Gemini-native path in app/runtime
            "llm_api_key": SecretStr("conversation-secret"),
            "llm_model": "test-model",
            "llm_timeout_s": 5.0,
            "llm_max_retries": 2,
            "llm_backoff_base_s": 0.0,
        }
    )


def _request(*roles_and_content: tuple[LlmRole, str]) -> LlmRequest:
    if not roles_and_content:
        roles_and_content = ((LlmRole.USER, "hello"),)
    return LlmRequest(
        messages=tuple(LlmMessage(role=r, content=c) for r, c in roles_and_content),
        max_output_tokens=64,
        temperature=0.0,
        prompt_id="extraction",
    )


@respx.mock
def test_conversation_role_completes_using_the_llm_block(
    settings: Settings, no_sleep: list[float]
) -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "hi there"}]}}]}
        )
    )
    with GeminiLlmProvider(
        _conv_settings(settings), role="conversation", sleep=no_sleep.append
    ) as provider:
        result = provider.complete(_request())
    assert result.text == "hi there"
    assert result.prompt_id == "extraction"


def test_no_conversation_key_raises_and_names_the_llm_env_var(settings: Settings) -> None:
    s = _conv_settings(settings).model_copy(update={"llm_api_key": None})
    provider = GeminiLlmProvider(s, role="conversation")
    with pytest.raises(LlmUnavailableError) as excinfo:
        provider.complete(_request())
    provider.close()
    assert "VYAPAR_LLM_API_KEY" in str(excinfo.value)


@respx.mock
def test_conversation_key_reaches_x_goog_api_key_header_and_never_the_url(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["header"] = request.headers.get("x-goog-api-key", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_URL).mock(side_effect=_capture)
    with GeminiLlmProvider(
        _conv_settings(settings), role="conversation", sleep=no_sleep.append
    ) as provider:
        provider.complete(_request())
    assert captured["header"] == "conversation-secret"
    assert "conversation-secret" not in captured["url"]


@respx.mock
def test_conversation_role_does_not_read_the_extractor_or_verifier_keys(
    settings: Settings, no_sleep: list[float]
) -> None:
    """The conversational credential is isolated from the offline Phase 5
    extraction gate: a run with all three keys set must send the `llm_*` one."""
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["header"] = request.headers.get("x-goog-api-key", "")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    s = _conv_settings(settings).model_copy(
        update={
            "gemini_extractor_api_key": SecretStr("extractor-secret"),
            "gemini_verifier_api_key": SecretStr("verifier-secret"),
        }
    )
    respx.post(_URL).mock(side_effect=_capture)
    with GeminiLlmProvider(s, role="conversation", sleep=no_sleep.append) as provider:
        provider.complete(_request())
    assert captured["header"] == "conversation-secret"


@respx.mock
def test_system_message_becomes_system_instruction(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_URL).mock(side_effect=_capture)
    request = _request((LlmRole.SYSTEM, "be careful"), (LlmRole.USER, "hello"))
    with GeminiLlmProvider(
        _conv_settings(settings), role="conversation", sleep=no_sleep.append
    ) as provider:
        provider.complete(request)

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["systemInstruction"]["parts"][0]["text"] == "be careful"
    assert [c["role"] for c in body["contents"]] == ["user"]


@respx.mock
def test_conversation_role_forces_json_response_mime_type(
    settings: Settings, no_sleep: list[float]
) -> None:
    """The conversational prompts always want a lone JSON object, so the
    request pins `responseMimeType` — this is what lets `llm/structured.py`
    parse on the first try instead of a repair round-trip."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})

    respx.post(_URL).mock(side_effect=_capture)
    with GeminiLlmProvider(
        _conv_settings(settings), role="conversation", sleep=no_sleep.append
    ) as provider:
        provider.complete(_request())

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["generationConfig"]["responseMimeType"] == "application/json"


@respx.mock
def test_extractor_role_does_not_force_json_response_mime_type(
    settings: Settings, no_sleep: list[float]
) -> None:
    """The Phase 5 extractor/verifier request contract is left exactly as it
    was — no `responseMimeType` key added by this change."""
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    url = "https://generativelanguage.googleapis.com/v1beta/models/test-extractor:generateContent"
    respx.post(url).mock(side_effect=_capture)
    s = settings.model_copy(
        update={
            "gemini_extractor_api_key": SecretStr("extractor-secret"),
            "gemini_extractor_model": "test-extractor",
            "gemini_backoff_base_s": 0.0,
        }
    )
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        provider.complete(_request())

    body = captured["body"]
    assert isinstance(body, dict)
    assert "responseMimeType" not in body["generationConfig"]


@respx.mock
def test_retries_429_then_succeeds(settings: Settings, no_sleep: list[float]) -> None:
    route = respx.post(_URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
    ]
    with GeminiLlmProvider(
        _conv_settings(settings), role="conversation", sleep=no_sleep.append
    ) as provider:
        result = provider.complete(_request())
    assert result.text == "ok"
    assert no_sleep  # backoff invoked


@respx.mock
def test_503_raises_llm_unavailable(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(503))
    with (
        GeminiLlmProvider(
            _conv_settings(settings), role="conversation", sleep=no_sleep.append
        ) as provider,
        pytest.raises(LlmUnavailableError),
    ):
        provider.complete(_request())


@respx.mock
def test_malformed_response_raises_llm_payload_error(
    settings: Settings, no_sleep: list[float]
) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    with (
        GeminiLlmProvider(
            _conv_settings(settings), role="conversation", sleep=no_sleep.append
        ) as provider,
        pytest.raises(LlmPayloadError),
    ):
        provider.complete(_request())


def test_borrowed_client_is_not_closed(settings: Settings) -> None:
    client = httpx.Client()
    provider = GeminiLlmProvider(_conv_settings(settings), role="conversation", client=client)
    provider.close()
    assert not client.is_closed
    client.close()


@respx.mock
def test_key_never_appears_in_captured_log_records(
    settings: Settings, no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(_URL).mock(return_value=httpx.Response(503))
    with caplog.at_level("DEBUG"):
        with (
            GeminiLlmProvider(
                _conv_settings(settings), role="conversation", sleep=no_sleep.append
            ) as provider,
            pytest.raises(LlmUnavailableError),
        ):
            provider.complete(_request())
    for record in caplog.records:
        assert "conversation-secret" not in record.getMessage()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
