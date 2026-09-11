"""`llm/diagnostics.py` + the conversation-role diagnostic branch of
`llm/gemini_provider.py`. Pure fixture payloads; the one provider test uses
`@respx.mock`. No network (CLAUDE.md §28).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from pydantic import SecretStr

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import LlmPayloadError
from vyaparsarathi.llm.diagnostics import (
    GeminiResponseDiagnostic,
    diagnose_gemini_response,
    dig_text,
    redacted_excerpt,
    summarize,
)
from vyaparsarathi.llm.gemini_provider import GeminiLlmProvider
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole

_URL = "https://generativelanguage.googleapis.com/v1beta/models/test-model:generateContent"


def _text_payload(text: str) -> dict:
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


# --- diagnose_gemini_response -------------------------------------------------


def test_max_tokens_with_thinking_budget_is_explained() -> None:
    payload = {
        "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
        "usageMetadata": {
            "promptTokenCount": 812,
            "candidatesTokenCount": 0,
            "thoughtsTokenCount": 1024,
            "totalTokenCount": 1836,
        },
    }
    diag = diagnose_gemini_response(payload, http_status=200)
    assert diag.finish_reason == "MAX_TOKENS"
    assert diag.text_present is False
    assert diag.thoughts_token_count == 1024
    assert diag.prompt_token_count == 812
    assert "thinking tokens consumed the budget" in diag.note
    line = summarize(diag)
    assert "MAX_TOKENS" in line and "thoughts=1024" in line


def test_safety_block_is_explained() -> None:
    payload = {"promptFeedback": {"blockReason": "SAFETY"}, "candidates": []}
    diag = diagnose_gemini_response(payload, http_status=200)
    assert diag.block_reason == "SAFETY"
    assert "safety filter" in diag.note
    assert diag.text_present is False


def test_prose_response_is_flagged_as_non_json() -> None:
    payload = _text_payload("Sure! Here is what I think about your grocery shop idea...")
    diag = diagnose_gemini_response(payload, http_status=200)
    assert diag.text_present is True
    assert diag.text_length > 0
    assert "not a parseable JSON object" in diag.note
    assert "grocery shop idea" in diag.redacted_excerpt


def test_unknown_shape_degrades_without_raising() -> None:
    diag = diagnose_gemini_response({"weird": "shape"}, http_status=200)
    assert diag.finish_reason == ""
    assert diag.text_present is False
    assert diag.prompt_token_count is None
    # tolerant of a non-dict too
    assert diagnose_gemini_response([], http_status=503).http_status == 503


def test_redacted_excerpt_strips_control_chars_and_caps_length() -> None:
    raw = "line one\nline\ttwo\x00\x07 " + "x" * 500
    out = redacted_excerpt(raw, limit=40)
    assert "\n" not in out and "\t" not in out and "\x00" not in out
    assert len(out) <= 41  # 40 + the ellipsis
    assert out.endswith("…")


def test_dig_text_returns_none_on_any_missing_hop() -> None:
    assert dig_text(_text_payload("hi")) == "hi"
    assert dig_text({"candidates": []}) is None
    assert dig_text({"candidates": [{"content": {"parts": []}}]}) is None
    assert dig_text({}) is None
    assert dig_text("nope") is None


def test_diagnostic_never_carries_a_bool_as_a_token_count() -> None:
    payload = {"usageMetadata": {"promptTokenCount": True}, "candidates": []}
    assert diagnose_gemini_response(payload, http_status=200).prompt_token_count is None


# --- GeminiLlmProvider conversation-role branch -----------------------------


def _conv_settings() -> Settings:
    return Settings(
        llm_enabled=True,
        llm_base_url="",
        llm_api_key=SecretStr("conversation-secret"),
        llm_model="test-model",
        llm_timeout_s=5.0,
        llm_max_retries=1,
        llm_backoff_base_s=0.0,
    )


def _request() -> LlmRequest:
    return LlmRequest(
        messages=(LlmMessage(role=LlmRole.USER, content="hi"),),
        max_output_tokens=64,
        temperature=0.0,
        prompt_id="extraction",
    )


@respx.mock
def test_conversation_no_text_raises_payload_error_with_a_sanitized_reason(
    no_sleep: list[float],
) -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"finishReason": "MAX_TOKENS", "content": {"parts": []}}],
                "usageMetadata": {"thoughtsTokenCount": 1024, "promptTokenCount": 800},
            },
        )
    )
    with (
        GeminiLlmProvider(_conv_settings(), role="conversation", sleep=no_sleep.append) as provider,
        pytest.raises(LlmPayloadError) as excinfo,
    ):
        provider.complete(_request())
    msg = str(excinfo.value)
    assert "MAX_TOKENS" in msg and "thoughts=1024" in msg


@respx.mock
def test_response_sink_receives_a_diagnostic_even_on_a_good_response(
    no_sleep: list[float],
) -> None:
    respx.post(_URL).mock(
        return_value=httpx.Response(200, json=_text_payload('{"intent":"unclear"}'))
    )
    seen: list[GeminiResponseDiagnostic] = []
    with GeminiLlmProvider(
        _conv_settings(), role="conversation", sleep=no_sleep.append, response_sink=seen.append
    ) as provider:
        result = provider.complete(_request())
    assert result.text == '{"intent":"unclear"}'
    assert len(seen) == 1
    assert seen[0].text_present is True


@respx.mock
def test_extractor_role_is_untouched_by_the_diagnostic_branch(no_sleep: list[float]) -> None:
    """A malformed extractor response still raises `LlmPayloadError` the old
    way — no diagnostic summary, no `response_sink` path."""
    url = "https://generativelanguage.googleapis.com/v1beta/models/x-model:generateContent"
    respx.post(url).mock(return_value=httpx.Response(200, json={"unexpected": "shape"}))
    s = Settings(
        gemini_extractor_api_key=SecretStr("extractor-secret"),
        gemini_extractor_model="x-model",
        gemini_backoff_base_s=0.0,
    )
    with (
        GeminiLlmProvider(s, role="extractor", sleep=no_sleep.append) as provider,
        pytest.raises(LlmPayloadError),
    ):
        provider.complete(_request())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
