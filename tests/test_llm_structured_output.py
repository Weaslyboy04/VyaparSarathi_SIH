"""`llm/structured.py` — fenced/prose JSON parsing, one repair, deterministic
give-up (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import pytest

from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.understanding import Intent
from vyaparsarathi.errors import LlmPayloadError
from vyaparsarathi.llm.extraction_context import CollectedFact, ExtractionContext
from vyaparsarathi.llm.fake import ScriptedLlmProvider
from vyaparsarathi.llm.llm_models import LlmResponse
from vyaparsarathi.llm.structured import (
    ExtractionOutcome,
    extract_understanding,
    parse_turn_understanding,
)


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
    understanding, llm_used, outcome = extract_understanding("hmm", provider, settings)
    assert llm_used is True
    assert outcome is ExtractionOutcome.SUCCEEDED
    assert understanding.intent is Intent.UNCLEAR


def test_extract_understanding_repairs_once_then_succeeds(settings: Settings) -> None:
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text='{"intent": "unclear"}', prompt_id="repair")],
        }
    )
    understanding, llm_used, outcome = extract_understanding(
        "hmm", provider, settings, max_repair_attempts=1
    )
    assert llm_used is True
    assert outcome is ExtractionOutcome.SUCCEEDED
    assert understanding.intent is Intent.UNCLEAR


def test_extract_understanding_gives_up_deterministically_after_repair_fails(
    settings: Settings,
) -> None:
    """A reachable provider that never returns usable JSON is UNPARSEABLE —
    distinct from PROVIDER_UNAVAILABLE (CLAUDE.md §25 Phase 6 Priority 6:
    'your answer was unclear' vs 'the service is unavailable')."""
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text="still not json", prompt_id="repair")],
        }
    )
    understanding, llm_used, outcome = extract_understanding(
        "hmm", provider, settings, max_repair_attempts=1
    )
    assert llm_used is False
    assert outcome is ExtractionOutcome.UNPARSEABLE
    assert understanding.intent is Intent.UNCLEAR
    assert understanding.raw_message == "hmm"


def test_extract_understanding_never_raises_when_provider_is_unavailable(
    settings: Settings,
) -> None:
    """An unreachable provider is PROVIDER_UNAVAILABLE — distinct from
    UNPARSEABLE, so the caller can tell the user the service (not their
    answer) is the problem."""
    provider = ScriptedLlmProvider({})  # no responses queued at all
    understanding, llm_used, outcome = extract_understanding("hmm", provider, settings)
    assert llm_used is False
    assert outcome is ExtractionOutcome.PROVIDER_UNAVAILABLE
    assert understanding.intent is Intent.UNCLEAR


def test_default_config_max_output_tokens_is_4096() -> None:
    """Gemini thinking models: internal reasoning tokens count against
    max_output_tokens, so 4096 provides headroom for both thinking and output."""
    settings = Settings()
    assert settings.llm_max_output_tokens == 4096


def test_extraction_request_uses_configured_max_output_tokens() -> None:
    """Initial extraction request must use the configured output-token budget."""
    settings = Settings(llm_max_output_tokens=8192)
    provider = ScriptedLlmProvider(
        {"extraction": [LlmResponse(text='{"intent": "unclear"}', prompt_id="extraction")]}
    )
    extract_understanding("hmm", provider, settings)
    assert len(provider.calls) == 1
    assert provider.calls[0].max_output_tokens == 8192


def test_repair_request_uses_configured_max_output_tokens() -> None:
    """Repair request must use the same output-token budget, not a hardcoded fallback."""
    settings = Settings(llm_max_output_tokens=8192)
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text='{"intent": "unclear"}', prompt_id="repair")],
        }
    )
    extract_understanding("hmm", provider, settings, max_repair_attempts=1)
    assert len(provider.calls) == 2
    assert provider.calls[0].max_output_tokens == 8192
    assert provider.calls[1].max_output_tokens == 8192


def test_no_context_means_no_context_block_in_the_prompt(settings: Settings) -> None:
    """The common first-turn case: context=None must add nothing extra to
    the prompt (byte-identical to the pre-Phase-B request shape)."""
    provider = ScriptedLlmProvider(
        {"extraction": [LlmResponse(text='{"intent": "unclear"}', prompt_id="extraction")]}
    )
    extract_understanding("hmm", provider, settings, context=None)
    user_content = provider.calls[0].messages[1].content
    # The instructions mention the block's name generically; only the actual
    # rendered block (with its trailing colon+newline) signals its presence.
    assert "Current conversation state:\n" not in user_content


def test_pending_options_reach_the_prompt_as_numbered_choices(settings: Settings) -> None:
    context = ExtractionContext(
        pending_kind="ask_disambiguation",
        pending_options=(
            "Bhagwanpur, Vaishali, Bihar, India",
            "Bhagwanpur, Begusarai, Bihar, India",
        ),
    )
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text='{"intent": "select_candidate", "selected_choice": 1}',
                    prompt_id="extraction",
                )
            ]
        }
    )
    extract_understanding("Vaishali one", provider, settings, context=context)
    user_content = provider.calls[0].messages[1].content
    assert "1. Bhagwanpur, Vaishali, Bihar, India" in user_content
    assert "2. Bhagwanpur, Begusarai, Bihar, India" in user_content


def test_collected_facts_reach_the_prompt(settings: Settings) -> None:
    context = ExtractionContext(
        collected=(CollectedFact(label="liquid cash inr", value_text="I have 6.5 lakh"),)
    )
    provider = ScriptedLlmProvider(
        {"extraction": [LlmResponse(text='{"intent": "unclear"}', prompt_id="extraction")]}
    )
    extract_understanding("actually only 4 lakh", provider, settings, context=context)
    user_content = provider.calls[0].messages[1].content
    assert 'liquid cash inr = "I have 6.5 lakh"' in user_content


def test_context_never_leaks_into_the_system_prompt(settings: Settings) -> None:
    """The context is turn-specific, per-user data — it must only ever land
    in the user message, never the (cacheable, session-independent) system
    prompt."""
    context = ExtractionContext(
        collected=(CollectedFact(label="liquid cash inr", value_text="I have 6.5 lakh"),)
    )
    provider = ScriptedLlmProvider(
        {"extraction": [LlmResponse(text='{"intent": "unclear"}', prompt_id="extraction")]}
    )
    extract_understanding("hmm", provider, settings, context=context)
    system_content = provider.calls[0].messages[0].content
    assert "6.5 lakh" not in system_content


def test_natural_choice_utterance_resolves_via_scripted_provider(settings: Settings) -> None:
    """Contract test for Priority 2's natural-choice mapping: given a
    context listing valid choices, a scripted response mapping "the first
    one" to selected_choice=1 round-trips correctly through parsing. This
    proves the plumbing (context in, selected_choice out) works; whether a
    live Gemini call actually produces this response is validated only by
    the manual live-smoke script, never here."""
    context = ExtractionContext(
        pending_kind="ask_disambiguation",
        pending_slot=None,
        pending_options=(
            "Bhagwanpur, Vaishali, Bihar, India",
            "Bhagwanpur, Begusarai, Bihar, India",
        ),
    )
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text='{"intent": "select_candidate", "selected_choice": 1}',
                    prompt_id="extraction",
                )
            ]
        }
    )
    understanding, llm_used, outcome = extract_understanding(
        "the first one", provider, settings, context=context
    )
    assert llm_used is True
    assert outcome is ExtractionOutcome.SUCCEEDED
    assert understanding.intent is Intent.SELECT_CANDIDATE
    assert understanding.selected_choice == 1


def test_hinglish_message_round_trips_through_parsing(settings: Settings) -> None:
    """Verbatim-substring matching is script/language-agnostic — this proves
    the parsing/validation pipeline handles a correct extraction from a
    romanized Hindi/English message exactly like an English one. Whether a
    live Gemini call actually PRODUCES this correct response for such a
    message is a live-model capability question, validated only by the
    manual smoke script — never asserted here."""
    raw_message = "mere paas 5 lakh hain aur kirana ki dukan kholni hai"
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text=(
                        '{"intent": "provide_info", "slot_updates": ['
                        '{"slot": "liquid_cash_inr", "raw_text": '
                        f'"{raw_message}", "value_token": "5 lakh", '
                        '"normalization": "lakh_to_inr"}, '
                        '{"slot": "proposed_business_text", "raw_text": '
                        f'"{raw_message}", "value_token": "kirana ki dukan", '
                        '"normalization": "as_stated"}]}'
                    ),
                    prompt_id="extraction",
                )
            ]
        }
    )
    understanding, llm_used, outcome = extract_understanding(raw_message, provider, settings)
    assert llm_used is True
    assert outcome is ExtractionOutcome.SUCCEEDED
    assert understanding.intent is Intent.PROVIDE_INFO
    tokens = {u.slot.value: u.value_token for u in understanding.slot_updates}
    assert tokens["liquid_cash_inr"] == "5 lakh"
    assert tokens["proposed_business_text"] == "kirana ki dukan"


def test_assets_removed_field_parses_correctly() -> None:
    text = '{"intent": "correct_slot", "assets_removed": ["vehicle"]}'
    understanding = parse_turn_understanding(text, "actually no bike")
    assert understanding.intent is Intent.CORRECT_SLOT
    from vyaparsarathi.models.profile import AssetKind

    assert understanding.assets_removed == (AssetKind.VEHICLE,)


def test_experience_removed_field_parses_correctly() -> None:
    text = '{"intent": "correct_slot", "experience_removed": ["dairy"]}'
    understanding = parse_turn_understanding(text, "no dairy experience actually")
    from vyaparsarathi.models.taxonomy import BusinessCategory

    assert understanding.experience_removed == (BusinessCategory.DAIRY,)


def test_asset_update_notes_field_parses_correctly() -> None:
    text = (
        '{"intent": "provide_info", "asset_update": {"items": ["livestock"], '
        '"raw_text": "2 cows", "notes": ["2 cows"]}}'
    )
    understanding = parse_turn_understanding(text, "I have 2 cows")
    assert understanding.asset_update is not None
    assert understanding.asset_update.notes == ("2 cows",)


def test_refusal_phrase_maps_to_decline_via_scripted_provider(settings: Settings) -> None:
    """Contract test: a scripted response representing a correct refusal
    mapping ("I don't know" -> decline_slot) round-trips through parsing.
    Whether a live Gemini call actually classifies a given refusal phrase
    this way is validated only by the manual smoke script."""
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text='{"intent": "decline_slot", "declined_slots": ["years_experience"]}',
                    prompt_id="extraction",
                )
            ]
        }
    )
    understanding, llm_used, outcome = extract_understanding("I don't know", provider, settings)
    assert llm_used is True
    assert outcome is ExtractionOutcome.SUCCEEDED
    assert understanding.intent is Intent.DECLINE_SLOT
    from vyaparsarathi.conversation.session_models import SlotName

    assert understanding.declined_slots == (SlotName.YEARS_EXPERIENCE,)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
