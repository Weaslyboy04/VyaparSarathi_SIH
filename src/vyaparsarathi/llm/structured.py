"""Turn an LLM's raw text response into a `TurnUnderstanding`, with one
bounded repair attempt and a deterministic give-up (CLAUDE.md §25 Phase 6).

The conversation never stalls on a bad response: after `max_repair_attempts`
failures, extraction returns `TurnUnderstanding(intent=UNCLEAR)` — the
planner then asks its own clarifying question, exactly as if the user's
message had genuinely been unclear.
"""

from __future__ import annotations

import json

from pydantic import ValidationError

from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.session_models import SlotName
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole
from vyaparsarathi.llm.prompts import (
    ALLOWED_SLOTS_PREFIX,
    EXTRACTION_INSTRUCTIONS,
    REPAIR_INSTRUCTIONS,
    SYSTEM_PROMPT,
)
from vyaparsarathi.llm.provider import LlmProvider
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_ALLOWED_SLOTS_LINE = ALLOWED_SLOTS_PREFIX + ", ".join(sorted(s.value for s in SlotName))


def _balanced_json_object(text: str) -> str | None:
    """The first balanced `{...}` span in `text` — tolerant of prose or a
    markdown code fence around the JSON object (not a strict `json.loads`
    from position 0)."""
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
    blob = _balanced_json_object(response_text)
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


def _extraction_request(user_message: str, settings: Settings, *, prompt_id: str) -> LlmRequest:
    user_content = (
        f"{EXTRACTION_INSTRUCTIONS}\n{_ALLOWED_SLOTS_LINE}\n\nUser message: {user_message}"
    )
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
    max_repair_attempts: int = 1,
) -> tuple[TurnUnderstanding, bool]:
    """`(understanding, llm_used)`. Never raises: on total failure returns
    `(TurnUnderstanding(intent=UNCLEAR, raw_message=user_message), False)` —
    a supported outcome, not an error state."""
    request = _extraction_request(user_message, settings, prompt_id="extraction")
    last_error = ""
    for attempt in range(max_repair_attempts + 1):
        try:
            response = provider.complete(request)
        except LlmUnavailableError as exc:
            logger.warning("LLM extraction unavailable (attempt %d): %s", attempt + 1, exc)
            break  # no point retrying an unreachable provider via "repair"
        try:
            return parse_turn_understanding(response.text, user_message), True
        except LlmPayloadError as exc:
            last_error = str(exc)
            logger.warning("LLM extraction unparseable (attempt %d): %s", attempt + 1, exc)
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
    return TurnUnderstanding(intent=Intent.UNCLEAR, raw_message=user_message), False


__all__ = ["extract_understanding", "parse_turn_understanding"]
