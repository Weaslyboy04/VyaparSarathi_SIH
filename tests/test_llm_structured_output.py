"""`llm/structured.py` — fenced/prose JSON parsing, one repair, deterministic
give-up (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import pytest

from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.understanding import Intent
from vyaparsarathi.errors import LlmPayloadError
from vyaparsarathi.llm.fake import ScriptedLlmProvider
from vyaparsarathi.llm.llm_models import LlmResponse
from vyaparsarathi.llm.structured import extract_understanding, parse_turn_understanding


def test_parses_a_bare_json_object() -> None:
    text = (
        '{"intent": "provide_info", "slot_updates": [{"slot": "liquid_cash_inr", '
        '"raw_text": "I have 6.5 lakh", "value_token": "6.5 lakh", '
        '"normalization": "lakh_to_inr"}]}'
    )
    understanding = parse_turn_understanding(text, "I have 6.5 lakh")
    assert understanding.intent is Intent.PROVIDE_INFO
    assert understanding.slot_updates[0].value_token == "6.5 lakh"


def test_parses_json_wrapped_in_prose_and_markdown_fencing() -> None:
    text = 'Sure, here you go:\n```json\n{"intent": "unclear"}\n```\nHope that helps!'
    understanding = parse_turn_understanding(text, "hmm")
    assert understanding.intent is Intent.UNCLEAR


def test_no_json_object_raises_llm_payload_error() -> None:
    with pytest.raises(LlmPayloadError):
        parse_turn_understanding("no json here at all", "hello")


def test_malformed_json_raises_llm_payload_error() -> None:
    with pytest.raises(LlmPayloadError):
        parse_turn_understanding("{not valid json}", "hello")


def test_extract_understanding_succeeds_on_first_try(settings: Settings) -> None:
    provider = ScriptedLlmProvider(
        {"extraction": [LlmResponse(text='{"intent": "unclear"}', prompt_id="extraction")]}
    )
    understanding, llm_used = extract_understanding("hmm", provider, settings)
    assert llm_used is True
    assert understanding.intent is Intent.UNCLEAR


def test_extract_understanding_repairs_once_then_succeeds(settings: Settings) -> None:
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text='{"intent": "unclear"}', prompt_id="repair")],
        }
    )
    understanding, llm_used = extract_understanding(
        "hmm", provider, settings, max_repair_attempts=1
    )
    assert llm_used is True
    assert understanding.intent is Intent.UNCLEAR


def test_extract_understanding_gives_up_deterministically_after_repair_fails(
    settings: Settings,
) -> None:
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text="still not json", prompt_id="repair")],
        }
    )
    understanding, llm_used = extract_understanding(
        "hmm", provider, settings, max_repair_attempts=1
    )
    assert llm_used is False
    assert understanding.intent is Intent.UNCLEAR
    assert understanding.raw_message == "hmm"


def test_extract_understanding_never_raises_when_provider_is_unavailable(
    settings: Settings,
) -> None:
    provider = ScriptedLlmProvider({})  # no responses queued at all
    understanding, llm_used = extract_understanding("hmm", provider, settings)
    assert llm_used is False
    assert understanding.intent is Intent.UNCLEAR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
