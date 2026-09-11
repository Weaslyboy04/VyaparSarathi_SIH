"""One-shot LIVE diagnostic for the conversational Gemini first-response
failure (CLAUDE.md §25 Phase 6/7 follow-up).

Makes **exactly one** `generateContent` call with the real extraction prompt,
then prints a sanitized description of the response: HTTP status, finish
reason, usage-metadata token counts (including `thoughtsTokenCount`), any
safety block, whether the text was a parseable JSON object, and a
length-capped / control-char-stripped excerpt. It never raises the token
limit or changes the model — those are separate, approval-gated decisions.

Secrets safety: the API key lives only in a request header (never printed);
the system+user prompt is not printed (only its length); the response is only
ever shown via `llm/diagnostics.py::redacted_excerpt`.

Run (only after the run is approved):

    ./.venv/Scripts/python.exe scripts/phase6_gemini_diag.py
"""

from __future__ import annotations

import sys
import time

from vyaparsarathi.config import get_settings
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.diagnostics import GeminiResponseDiagnostic, redacted_excerpt, summarize
from vyaparsarathi.llm.gemini_provider import GeminiLlmProvider
from vyaparsarathi.llm.structured import (
    _extraction_request,  # reuse the exact prompt; do not duplicate it here
    extract_balanced_json_object,
    parse_turn_understanding,
)

_SAMPLE_MESSAGE = "I have 6.5 lakh and want to open a grocery shop in Bhagwanpur, Bihar."


def main() -> int:
    settings = get_settings()
    if not settings.llm_enabled:
        print("VYAPAR_LLM_ENABLED is not true — nothing to diagnose.", file=sys.stderr)
        return 2
    if settings.llm_base_url:
        print(
            "VYAPAR_LLM_BASE_URL is set — this diagnostic only covers the Gemini-native "
            "conversational path (no base URL). Nothing to do.",
            file=sys.stderr,
        )
        return 2
    if settings.llm_api_key is None:
        print("VYAPAR_LLM_API_KEY is not set — cannot make a live call.", file=sys.stderr)
        return 2

    request = _extraction_request(_SAMPLE_MESSAGE, settings, prompt_id="extraction")
    prompt_chars = sum(len(m.content) for m in request.messages)

    captured: list[GeminiResponseDiagnostic] = []
    print(
        f"model={settings.llm_model}  prompt_chars={prompt_chars}  "
        f"max_output_tokens={request.max_output_tokens}  temperature={request.temperature}"
    )
    print("making exactly ONE live generateContent call ...")

    started = time.monotonic()
    text: str | None = None
    error: str | None = None
    with GeminiLlmProvider(
        settings, role="conversation", response_sink=captured.append
    ) as provider:
        try:
            text = provider.complete(request).text
        except (LlmPayloadError, LlmUnavailableError) as exc:
            error = str(exc)
    latency_s = round(time.monotonic() - started, 2)

    print(f"\nlatency: {latency_s}s")
    if captured:
        print(f"diagnostic: {summarize(captured[-1])}")
    if error is not None:
        print(f"outcome: call raised -> {error}")
        return 1

    assert text is not None
    blob = extract_balanced_json_object(text)
    if blob is None:
        print("outcome: 200 with text, but NO balanced JSON object was found (prose reply)")
        print(f"response excerpt: {redacted_excerpt(text)}")
        return 1
    try:
        parse_turn_understanding(text, _SAMPLE_MESSAGE)
    except LlmPayloadError as exc:
        print(f"outcome: JSON object found but did not validate as TurnUnderstanding -> {exc}")
        print(f"response excerpt: {redacted_excerpt(text)}")
        return 1

    print("outcome: first response parsed cleanly on the first try — no repair needed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
