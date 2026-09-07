"""`llm/provider.py::HttpLlmProvider` (CLAUDE.md §4.1, §24, §25 Phase 6).
`@respx.mock` on `llm.test`; no test in this file touches the network."""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole
from vyaparsarathi.llm.provider import HttpLlmProvider

ENDPOINT = "https://llm.test/v1/complete"


def _request() -> LlmRequest:
    return LlmRequest(
        messages=(LlmMessage(role=LlmRole.USER, content="hello"),),
        max_output_tokens=64,
        temperature=0.2,
        prompt_id="extraction",
    )


@respx.mock
def test_successful_completion(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"text": "hi there"}))
    with HttpLlmProvider(settings, sleep=no_sleep.append) as provider:
        result = provider.complete(_request())
    assert result.text == "hi there"
    assert result.prompt_id == "extraction"


@respx.mock
def test_retries_429_then_succeeds(settings: Settings, no_sleep: list[float]) -> None:
    route = respx.post(ENDPOINT)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"text": "ok"}),
    ]
    with HttpLlmProvider(settings, sleep=no_sleep.append) as provider:
        result = provider.complete(_request())
    assert result.text == "ok"
    assert no_sleep  # backoff was invoked (Retry-After honoured)


@respx.mock
def test_retries_503_then_raises_llm_unavailable(settings: Settings, no_sleep: list[float]) -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    with (
        HttpLlmProvider(settings, sleep=no_sleep.append) as provider,
        pytest.raises(LlmUnavailableError),
    ):
        provider.complete(_request())


@respx.mock
def test_malformed_response_raises_llm_payload_error(
    settings: Settings, no_sleep: list[float]
) -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    with (
        HttpLlmProvider(settings, sleep=no_sleep.append) as provider,
        pytest.raises(LlmPayloadError),
    ):
        provider.complete(_request())


def test_no_base_url_raises_llm_unavailable(settings: Settings) -> None:
    s = settings.model_copy(update={"llm_base_url": ""})
    provider = HttpLlmProvider(s)
    with pytest.raises(LlmUnavailableError):
        provider.complete(_request())
    provider.close()


def test_borrowed_client_is_not_closed(settings: Settings) -> None:
    client = httpx.Client()
    provider = HttpLlmProvider(settings, client=client)
    provider.close()
    assert not client.is_closed
    client.close()


@respx.mock
def test_api_key_reaches_authorization_header_and_never_the_url(
    settings: Settings, no_sleep: list[float]
) -> None:
    s = settings.model_copy(update={"llm_api_key": SecretStr("super-secret-key")})
    captured: dict[str, str] = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("Authorization", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"text": "ok"})

    respx.post(ENDPOINT).mock(side_effect=_capture)
    with HttpLlmProvider(s, sleep=no_sleep.append) as provider:
        provider.complete(_request())
    assert captured["auth"] == "Bearer super-secret-key"
    assert "super-secret-key" not in captured["url"]


@respx.mock
def test_api_key_never_appears_in_captured_log_records(
    settings: Settings, no_sleep: list[float], caplog: pytest.LogCaptureFixture
) -> None:
    s = settings.model_copy(update={"llm_api_key": SecretStr("super-secret-key")})
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503))
    with caplog.at_level("DEBUG"):
        with (
            HttpLlmProvider(s, sleep=no_sleep.append) as provider,
            pytest.raises(LlmUnavailableError),
        ):
            provider.complete(_request())
    for record in caplog.records:
        assert "super-secret-key" not in record.getMessage()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
