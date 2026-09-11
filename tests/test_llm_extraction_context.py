"""`llm/extraction_context.py::build_extraction_context` (CLAUDE.md §25 Phase
6). Pure; no I/O, no provider — verifies the peek-mode `decide()` call
surfaces exactly what is currently pending, and that the collected-facts
summary stays compact and provenance-free."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.conversation.deltas import apply_understanding, set_slot_ambiguous
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName
from vyaparsarathi.conversation.understanding import Intent, SlotUpdateInput, TurnUnderstanding
from vyaparsarathi.llm.extraction_context import build_extraction_context
from vyaparsarathi.models.parameters import ValueNormalization


def _session() -> ConversationSession:
    now = datetime.now(UTC)
    return ConversationSession(session_id="s1", created_at=now, updated_at=now)


def test_empty_session_has_no_pending_options_or_collected_facts() -> None:
    """A brand-new session (the real first-turn case, before any step has
    run) has something structurally ready (e.g. SCHEME_CAPACITY, which needs
    no user slots) rather than a clarification — `decide()`'s own rung 5 runs
    before rung 3's missing-slot check. There is nothing to ask about yet,
    so no pending question/options and nothing collected."""
    ctx = build_extraction_context(_session())
    assert ctx.pending_options == ()
    assert ctx.collected == ()
    assert ctx.pending_question == ""


def test_ambiguous_location_surfaces_pending_options_in_display_order() -> None:
    session = set_slot_ambiguous(
        _session(),
        SlotName.LOCATION_TEXT,
        ("Bhagwanpur, Vaishali, Bihar, India", "Bhagwanpur, Begusarai, Bihar, India"),
        raw_text="Bhagwanpur",
        turn_index=1,
    )
    ctx = build_extraction_context(session)
    assert ctx.pending_kind == "ask_disambiguation"
    assert ctx.pending_slot is SlotName.LOCATION_TEXT
    assert ctx.pending_options == (
        "Bhagwanpur, Vaishali, Bihar, India",
        "Bhagwanpur, Begusarai, Bihar, India",
    )


def test_pending_options_empty_when_not_currently_ambiguous() -> None:
    """A slot that WAS ambiguous but has since been resolved must not leak
    stale options into a later turn's context."""
    session = set_slot_ambiguous(
        _session(), SlotName.LOCATION_TEXT, ("A", "B"), raw_text="q", turn_index=1
    )
    understanding = TurnUnderstanding(intent=Intent.SELECT_CANDIDATE, selected_choice=1)
    session, _ = apply_understanding(session, understanding, turn_index=2)
    ctx = build_extraction_context(session)
    assert ctx.pending_options == ()


def test_collected_facts_reflect_user_provided_slots_only() -> None:
    session = _session()
    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        slot_updates=(
            SlotUpdateInput(
                slot=SlotName.LIQUID_CASH_INR,
                raw_text="I have 6.5 lakh",
                value_token="6.5 lakh",
                normalization=ValueNormalization.LAKH_TO_INR,
            ),
        ),
    )
    session, _ = apply_understanding(session, understanding, turn_index=1)
    ctx = build_extraction_context(session)
    labels = {f.label for f in ctx.collected}
    assert "liquid cash inr" in labels
    fact = next(f for f in ctx.collected if f.label == "liquid cash inr")
    assert fact.value_text == "I have 6.5 lakh"  # the user's own phrase, not the derived Decimal


def test_collected_facts_exclude_missing_ambiguous_and_declined_slots() -> None:
    session = _session()
    session = set_slot_ambiguous(
        session, SlotName.LOCATION_TEXT, ("A", "B"), raw_text="q", turn_index=1
    )
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, declined_slots=(SlotName.YEARS_EXPERIENCE,)
    )
    session, _ = apply_understanding(session, understanding, turn_index=2)
    ctx = build_extraction_context(session)
    labels = {f.label for f in ctx.collected}
    assert "location text" not in labels
    assert "years experience" not in labels


def test_collected_facts_include_assets_and_experience_when_present() -> None:
    from vyaparsarathi.conversation.understanding import AssetUpdateInput, ExperienceUpdateInput
    from vyaparsarathi.models.profile import AssetKind
    from vyaparsarathi.models.taxonomy import BusinessCategory

    session = _session()
    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        asset_update=AssetUpdateInput(
            items=(AssetKind.LIVESTOCK, AssetKind.STOREFRONT), raw_text="2 cows and a shop"
        ),
        experience_update=ExperienceUpdateInput(
            items=(BusinessCategory.GROCERY,), raw_text="worked in a kirana shop"
        ),
    )
    session, _ = apply_understanding(session, understanding, turn_index=1)
    ctx = build_extraction_context(session)
    labels = {f.label: f.value_text for f in ctx.collected}
    assert "livestock" in labels["assets"]
    assert "storefront" in labels["assets"]
    assert "grocery" in labels["experience"]


def test_context_is_a_pure_peek_and_never_mutates_or_persists_anything() -> None:
    """Calling build_extraction_context repeatedly must be side-effect-free —
    it must not run a step, add an artifact, or change slot state."""
    session = _session()
    before = session.model_dump(mode="json")
    build_extraction_context(session)
    build_extraction_context(session)
    after = session.model_dump(mode="json")
    assert before == after


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
