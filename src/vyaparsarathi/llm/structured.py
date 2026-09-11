"""Turn an LLM's raw text response into a `TurnUnderstanding`, with one
bounded repair attempt and a deterministic give-up (CLAUDE.md §25 Phase 6).

The conversation never stalls on a bad response: after `max_repair_attempts`
failures, extraction returns `TurnUnderstanding(intent=UNCLEAR)` — the
planner then asks its own clarifying question, exactly as if the user's
message had genuinely been unclear.
"""

from __future__ import annotations

import json
from enum import StrEnum

from pydantic import ValidationError

from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.session_models import SlotName
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.diagnostics import redacted_excerpt
from vyaparsarathi.llm.extraction_context import ExtractionContext
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole
from vyaparsarathi.llm.prompts import (
    ALLOWED_SLOTS_PREFIX,
    EXTRACTION_INSTRUCTIONS,
    REPAIR_INSTRUCTIONS,
    SYSTEM_PROMPT,
)
from vyaparsarathi.llm.provider import LlmProvider
from vyaparsarathi.models.profile import AssetKind
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

class ExtractionOutcome(StrEnum):
    """Why `extract_understanding` returned what it did — CLAUDE.md §25
    Phase 6 Priority 6's "distinguish 'I could not understand because the
    service is unavailable' from 'your answer was unclear'". Both failure
    states still return the identical, safe `TurnUnderstanding(intent=
    UNCLEAR)` (the conversation never stalls either way) — this is purely an
    additional signal for the caller to phrase its reply honestly, never a
    second code path through the extraction/validation logic itself."""

    SUCCEEDED = "succeeded"
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # the provider itself could not be reached
    UNPARSEABLE = "unparseable"  # reachable, but never returned usable JSON (repair exhausted)


_ALLOWED_SLOTS_LINE = ALLOWED_SLOTS_PREFIX + ", ".join(sorted(s.value for s in SlotName))
# Derived the same way `_ALLOWED_SLOTS_LINE` derives slot names from the
# enum, never hardcoded — a taxonomy/asset-kind addition needs no prompt edit.
_ALLOWED_ASSET_KINDS_LINE = "Allowed asset kinds: " + ", ".join(sorted(a.value for a in AssetKind))
_ALLOWED_CATEGORIES_LINE = "Allowed business categories: " + ", ".join(
    sorted(c.value for c in BusinessCategory)
)


def extract_balanced_json_object(text: str) -> str | None:
    """The first balanced `{...}` span in `text` — tolerant of prose or a
    markdown code fence around the JSON object (not a strict `json.loads`
    from position 0). Public/reused by `scripts/build_parameter_registry.py`'s
    extractor/verifier response parsing, so both LLM-response scanners share
    one implementation."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        char = text[i]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_turn_understanding(response_text: str, user_message: str) -> TurnUnderstanding:
    """Raises `LlmPayloadError` on any parse/validation failure — the caller
    (`extract_understanding`) decides whether to repair or give up."""
    blob = extract_balanced_json_object(response_text)
    if blob is None:
        raise LlmPayloadError("no JSON object found in the LLM response")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise LlmPayloadError(f"malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmPayloadError("the parsed JSON was not an object")
    data = {**data, "raw_message": user_message}
    try:
        return TurnUnderstanding.model_validate(data)
    except ValidationError as exc:
        raise LlmPayloadError(f"response did not match TurnUnderstanding: {exc}") from exc


def _context_block(context: ExtractionContext | None) -> str:
    """Render `ExtractionContext` as plain, clearly-labelled text — never a
    hidden instruction, never a raw engine payload. Empty string when there
    is nothing pending and nothing collected yet (the common first turn)."""
    if context is None:
        return ""
    lines: list[str] = []
    if context.pending_question:
        lines.append(f"The system is currently waiting for: {context.pending_question}")
    if context.pending_options:
        lines.append(
            "Valid numbered choices right now (pick ONE by number if the message answers this):"
        )
        lines.extend(
            f"{i}. {option}" for i, option in enumerate(context.pending_options, start=1)
        )
    if context.collected:
        facts = "; ".join(f'{f.label} = "{f.value_text}"' for f in context.collected)
        lines.append(f"Already collected this session: {facts}")
    if not lines:
        return ""
    return "Current conversation state:\n" + "\n".join(lines)


def _extraction_request(
    user_message: str,
    settings: Settings,
    *,
    prompt_id: str,
    context: ExtractionContext | None = None,
) -> LlmRequest:
    header = (
        f"{EXTRACTION_INSTRUCTIONS}\n{_ALLOWED_SLOTS_LINE}\n{_ALLOWED_ASSET_KINDS_LINE}\n"
        f"{_ALLOWED_CATEGORIES_LINE}"
    )
    block = _context_block(context)
    tail = f"User message: {user_message}"
    user_content = f"{header}\n\n{block}\n\n{tail}" if block else f"{header}\n\n{tail}"
    return LlmRequest(
        messages=(
            LlmMessage(role=LlmRole.SYSTEM, content=SYSTEM_PROMPT),
            LlmMessage(role=LlmRole.USER, content=user_content),
        ),
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
        prompt_id=prompt_id,
    )


def extract_understanding(
    user_message: str,
    provider: LlmProvider,
    settings: Settings,
    *,
    context: ExtractionContext | None = None,
    max_repair_attempts: int = 1,
) -> tuple[TurnUnderstanding, bool, ExtractionOutcome]:
    """`(understanding, llm_used, outcome)`. Never raises: on total failure
    returns `(TurnUnderstanding(intent=UNCLEAR, raw_message=user_message),
    False, outcome)` — a supported outcome, not an error state, either way.
    `outcome` only ever changes what the CALLER says to the user about why
    (CLAUDE.md §25 Phase 6 Priority 6) — the conversation itself behaves
    identically regardless.

    `context` (see `llm/extraction_context.py`) is purely a hint to help the
    model recognise a natural reply to its own pending question or a
    correction against an already-collected fact — the backend
    (`conversation/deltas.py::apply_understanding`) always re-validates any
    `selected_choice` against the session's actual live options regardless
    of what this context said, so a stale or omitted context can never let
    the model bypass validation."""
    request = _extraction_request(user_message, settings, prompt_id="extraction", context=context)
    last_error = ""
    outcome = ExtractionOutcome.UNPARSEABLE
    for attempt in range(max_repair_attempts + 1):
        try:
            response = provider.complete(request)
        except LlmUnavailableError as exc:
            logger.warning("LLM extraction unavailable (attempt %d): %s", attempt + 1, exc)
            outcome = ExtractionOutcome.PROVIDER_UNAVAILABLE
            break  # no point retrying an unreachable provider via "repair"
        try:
            understanding = parse_turn_understanding(response.text, user_message)
            return understanding, True, ExtractionOutcome.SUCCEEDED
        except LlmPayloadError as exc:
            last_error = str(exc)
            logger.warning(
                "LLM extraction unparseable (attempt %d): %s | response excerpt: %s",
                attempt + 1,
                exc,
                redacted_excerpt(response.text),
            )
            request = LlmRequest(
                messages=(
                    *request.messages,
                    LlmMessage(role=LlmRole.ASSISTANT, content=response.text),
                    LlmMessage(
                        role=LlmRole.USER, content=f"{REPAIR_INSTRUCTIONS}\nError: {last_error}"
                    ),
                ),
                max_output_tokens=settings.llm_max_output_tokens,
                temperature=settings.llm_temperature,
                prompt_id="repair",
            )
    return TurnUnderstanding(intent=Intent.UNCLEAR, raw_message=user_message), False, outcome


__all__ = [
    "ExtractionOutcome",
    "extract_balanced_json_object",
    "extract_understanding",
    "parse_turn_understanding",
]
