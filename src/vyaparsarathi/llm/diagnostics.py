"""Diagnostic-only description of an unusable Gemini conversation response
(CLAUDE.md §25 Phase 6/7 follow-up). PURE — no I/O, no network.

This exists to explain, in a log line, *why* the first conversational
extraction response could not be parsed — most often `finishReason=MAX_TOKENS`
with the whole budget spent on `thoughtsTokenCount`, or a `promptFeedback`
safety block, or a 200 that simply carries no text part. It never changes
behaviour: `GeminiLlmProvider` still raises `LlmPayloadError` and
`llm/structured.py`'s one repair attempt still runs.

Nothing here raises the token limit or changes the model — those are separate,
approval-gated decisions.
"""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict

_EXCERPT_LIMIT = 240


class GeminiResponseDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    http_status: int
    finish_reason: str = ""
    block_reason: str = ""
    prompt_token_count: int | None = None
    candidates_token_count: int | None = None
    thoughts_token_count: int | None = None
    total_token_count: int | None = None
    text_present: bool = False
    text_length: int = 0
    redacted_excerpt: str = ""
    note: str = ""


def dig_text(payload: object) -> str | None:
    """`candidates[0].content.parts[0].text` or `None` on any missing/typed
    hop — the single place that knows the Gemini success shape."""
    try:
        candidate = payload["candidates"][0]  # type: ignore[index]
        part = candidate["content"]["parts"][0]
        text = part["text"]
    except (KeyError, IndexError, TypeError):
        return None
    return text if isinstance(text, str) else None


def redacted_excerpt(text: str, *, limit: int = _EXCERPT_LIMIT) -> str:
    """A short, single-line, printable-only slice of model output — safe to
    put in a log or an exception message. Model output never contains our API
    key (the key is a request header only), so this only guards against
    control characters, newlines, and length."""
    printable = "".join(ch for ch in text if ch.isprintable() or ch.isspace())
    joined = " ".join(printable.split())
    if len(joined) <= limit:
        return joined
    return joined[:limit].rstrip() + "…"


def diagnose_gemini_response(
    payload: Mapping[str, object] | object, *, http_status: int, limit: int = _EXCERPT_LIMIT
) -> GeminiResponseDiagnostic:
    """Build a sanitized diagnostic from a decoded Gemini `generateContent`
    body. Tolerant of any shape — a missing field simply stays `None`/`""`."""
    get = payload.get if isinstance(payload, Mapping) else (lambda _k, _d=None: _d)

    candidates = get("candidates", None)
    candidate0 = candidates[0] if isinstance(candidates, list) and candidates else {}
    finish_reason = _str(candidate0.get("finishReason") if isinstance(candidate0, Mapping) else "")

    feedback = get("promptFeedback", None)
    block_reason = _str(feedback.get("blockReason") if isinstance(feedback, Mapping) else "")

    usage = get("usageMetadata", None)
    usage = usage if isinstance(usage, Mapping) else {}
    prompt_tokens = _int(usage.get("promptTokenCount"))
    candidates_tokens = _int(usage.get("candidatesTokenCount"))
    thoughts_tokens = _int(usage.get("thoughtsTokenCount"))
    total_tokens = _int(usage.get("totalTokenCount"))

    text = dig_text(payload) or ""
    text_present = bool(text)

    return GeminiResponseDiagnostic(
        http_status=http_status,
        finish_reason=finish_reason,
        block_reason=block_reason,
        prompt_token_count=prompt_tokens,
        candidates_token_count=candidates_tokens,
        thoughts_token_count=thoughts_tokens,
        total_token_count=total_tokens,
        text_present=text_present,
        text_length=len(text),
        redacted_excerpt=redacted_excerpt(text, limit=limit),
        note=_explain(
            finish_reason=finish_reason,
            block_reason=block_reason,
            text_present=text_present,
            thoughts_tokens=thoughts_tokens,
            candidates_tokens=candidates_tokens,
        ),
    )


def summarize(diag: GeminiResponseDiagnostic) -> str:
    """One sanitized line for a WARNING log / exception message."""
    tokens = (
        f"prompt={diag.prompt_token_count} candidates={diag.candidates_token_count} "
        f"thoughts={diag.thoughts_token_count} total={diag.total_token_count}"
    )
    return (
        f"http={diag.http_status} finish={diag.finish_reason or '?'} "
        f"block={diag.block_reason or '-'} tokens({tokens}) "
        f"text_present={diag.text_present} text_len={diag.text_length} "
        f"note={diag.note!r} excerpt={diag.redacted_excerpt!r}"
    )


def _explain(
    *,
    finish_reason: str,
    block_reason: str,
    text_present: bool,
    thoughts_tokens: int | None,
    candidates_tokens: int | None,
) -> str:
    if block_reason:
        return f"prompt blocked by a Gemini safety filter (blockReason={block_reason})"
    if finish_reason == "SAFETY":
        return "response stopped by a Gemini safety filter before any text"
    if finish_reason == "MAX_TOKENS" and not text_present:
        base = "hit the output-token cap before emitting any text"
        if thoughts_tokens:
            return (
                f"{base}; {thoughts_tokens} thinking tokens consumed the budget "
                f"(candidates={candidates_tokens or 0})"
            )
        return base
    if not text_present:
        return f"HTTP 200 but no text part (finishReason={finish_reason or 'absent'})"
    return (
        f"text returned but not a parseable JSON object (finishReason={finish_reason or 'absent'})"
    )


def _str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


__all__ = [
    "GeminiResponseDiagnostic",
    "diagnose_gemini_response",
    "dig_text",
    "redacted_excerpt",
    "summarize",
]
