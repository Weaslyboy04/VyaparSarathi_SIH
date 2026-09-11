"""`llm/gemini_provider.py::GeminiLlmProvider` (CLAUDE.md §4.1, §18, §24, §30).
`@respx.mock` on the real, fixed Gemini endpoint; no test in this file
touches the network. Mirrors `tests/test_llm_provider.py`'s structure for
`HttpLlmProvider`."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.gemini_provider import GeminiLlmProvider
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole

_EXTRACTOR_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/test-extractor-model:generateContent"
)
_VERIFIER_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/test-verifier-model:generateContent"
)


def _gemini_settings(settings: Settings) -> Settings:
    """Explicit overrides for every gemini_* field this test file touches —
    never relies on `.env`'s (real, git-ignored) key material or model
    choice, so this file's behaviour is identical on every machine."""
    return settings.model_copy(
        update={
            "gemini_extractor_api_key": SecretStr("extractor-secret"),
            "gemini_extractor_model": "test-extractor-model",
            "gemini_verifier_api_key": SecretStr("verifier-secret"),
            "gemini_verifier_model": "test-verifier-model",
            "gemini_timeout_s": 5.0,
            "gemini_max_retries": 2,
            "gemini_backoff_base_s": 0.0,
        }
    )


def _request(*roles_and_content: tuple[LlmRole, str]) -> LlmRequest:
    if not roles_and_content:
        roles_and_content = ((LlmRole.USER, "hello"),)
    return LlmRequest(
        messages=tuple(LlmMessage(role=r, content=c) for r, c in roles_and_content),
        max_output_tokens=64,
        temperature=0.0,
        prompt_id="extract:doc-a#s1",
    )


@respx.mock
def test_successful_completion_extractor_role(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(_EXTRACTOR_URL).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "hi there"}]}}]}
        )
    )
    s = _gemini_settings(settings)
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        result = provider.complete(_request())
    assert result.text == "hi there"
    assert result.prompt_id == "extract:doc-a#s1"


@respx.mock
def test_successful_completion_verifier_role(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(_VERIFIER_URL).mock(
        return_value=httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}
        )
    )
    s = _gemini_settings(settings)
    with GeminiLlmProvider(s, role="verifier", sleep=no_sleep.append) as provider:
        result = provider.complete(_request())
    assert result.text == "ok"


@respx.mock
def test_retries_429_then_succeeds(settings: Settings, no_sleep: list[float]) -> None:
    route = respx.post(_EXTRACTOR_URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}),
    ]
    s = _gemini_settings(settings)
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        result = provider.complete(_request())
    assert result.text == "ok"
    assert no_sleep  # backoff was invoked (Retry-After honoured)


@respx.mock
def test_retries_503_then_raises_llm_unavailable(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(_EXTRACTOR_URL).mock(return_value=httpx.Response(503))
    s = _gemini_settings(settings)
    with (
        GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider,
        pytest.raises(LlmUnavailableError),
    ):
        provider.complete(_request())


@respx.mock
def test_malformed_response_raises_llm_payload_error(
    settings: Settings, no_sleep: list[float]
) -> None:
    respx.post(_EXTRACTOR_URL).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    s = _gemini_settings(settings)
    with (
        GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider,
        pytest.raises(LlmPayloadError),
    ):
        provider.complete(_request())


def test_no_api_key_raises_llm_unavailable(settings: Settings) -> None:
    s = _gemini_settings(settings).model_copy(update={"gemini_extractor_api_key": None})
    provider = GeminiLlmProvider(s, role="extractor")
    with pytest.raises(LlmUnavailableError):
        provider.complete(_request())
    provider.close()


def test_borrowed_client_is_not_closed(settings: Settings) -> None:
    client = httpx.Client()
    provider = GeminiLlmProvider(_gemini_settings(settings), role="extractor", client=client)
    provider.close()
    assert not client.is_closed
    client.close()


@respx.mock
def test_api_key_reaches_x_goog_api_key_header_and_never_the_url(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["header"] = request.headers.get("x-goog-api-key", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_EXTRACTOR_URL).mock(side_effect=_capture)
    s = _gemini_settings(settings)
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        provider.complete(_request())
    assert captured["header"] == "extractor-secret"
    assert "extractor-secret" not in captured["url"]


@respx.mock
def test_extractor_and_verifier_roles_use_different_keys(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["header"] = request.headers.get("x-goog-api-key", "")
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_VERIFIER_URL).mock(side_effect=_capture)
    s = _gemini_settings(settings)
    with GeminiLlmProvider(s, role="verifier", sleep=no_sleep.append) as provider:
        provider.complete(_request())
    assert captured["header"] == "verifier-secret"


@respx.mock
def test_system_message_becomes_system_instruction_not_contents(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_EXTRACTOR_URL).mock(side_effect=_capture)
    s = _gemini_settings(settings)
    request = _request((LlmRole.SYSTEM, "be careful"), (LlmRole.USER, "hello"))
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        provider.complete(request)

    body = captured["body"]
    assert isinstance(body, dict)
    assert body["systemInstruction"]["parts"][0]["text"] == "be careful"
    assert len(body["contents"]) == 1
    assert body["contents"][0]["role"] == "user"
    assert body["contents"][0]["parts"][0]["text"] == "hello"


@respx.mock
def test_assistant_role_maps_to_model_not_assistant(
    settings: Settings, no_sleep: list[float]
) -> None:
    captured: dict[str, object] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json as _json

        captured["body"] = _json.loads(request.content)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "ok"}]}}]})

    respx.post(_EXTRACTOR_URL).mock(side_effect=_capture)
    s = _gemini_settings(settings)
    request = _request((LlmRole.USER, "hello"), (LlmRole.ASSISTANT, "hi"))
    with GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider:
        provider.complete(request)

    body = captured["body"]
    assert isinstance(body, dict)
    roles = [c["role"] for c in body["contents"]]
    assert roles == ["user", "model"]  # Gemini has no "assistant" role


@respx.mock
def test_api_key_never_appears_in_captured_log_records(
    settings: Settings, no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    respx.post(_EXTRACTOR_URL).mock(return_value=httpx.Response(503))
    s = _gemini_settings(settings)
    with caplog.at_level("DEBUG"):
        with (
            GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider,
            pytest.raises(LlmUnavailableError),
        ):
            provider.complete(_request())
    for record in caplog.records:
        assert "extractor-secret" not in record.getMessage()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
