"""`conversation/session_models.py` (CLAUDE.md §23, §25 Phase 6). Pure; no I/O."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    Slot,
    SlotName,
    SlotState,
    SlotValue,
    as_input_kind,
    from_input_kind,
)
from vyaparsarathi.models.finance import InputKind


def test_slot_state_is_a_superset_of_input_kind() -> None:
    assert {k.value for k in InputKind} <= {s.value for s in SlotState}


def test_as_input_kind_round_trips_every_input_kind() -> None:
    for kind in InputKind:
        state = from_input_kind(kind)
        assert as_input_kind(state) is kind


def test_as_input_kind_is_none_for_conversation_only_states() -> None:
    assert as_input_kind(SlotState.MISSING) is None
    assert as_input_kind(SlotState.AMBIGUOUS) is None
    assert as_input_kind(SlotState.DECLINED) is None


def test_missing_slot_value_carries_no_value() -> None:
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.MISSING, value=650_000)


def test_ambiguous_requires_options() -> None:
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.AMBIGUOUS)
    SlotValue(state=SlotState.AMBIGUOUS, options=("Bhagwanpur, Bihar", "Bhagwanpur, UP"))


def test_user_provided_requires_profile_source_and_forbids_confidence() -> None:
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.USER_PROVIDED, value=650_000, source="somewhere_else")
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.USER_PROVIDED, value=650_000, source="profile", confidence=0.5)
    SlotValue(state=SlotState.USER_PROVIDED, value=650_000, source="profile")


def test_assumed_requires_rationale_and_config_source() -> None:
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.ASSUMED, value=5_000, source="config:default_radius")
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.ASSUMED, value=5_000, rationale="default", source="elsewhere")
    SlotValue(
        state=SlotState.ASSUMED, value=5_000, rationale="default radius", source="config:radius"
    )


def test_sourced_requires_source_ref_and_retrieved_at() -> None:
    from datetime import UTC, datetime

    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.SOURCED, value=11, source="knowledge:doc-1")
    SlotValue(
        state=SlotState.SOURCED,
        value=11,
        source="knowledge:doc-1",
        source_ref="doc-1#p1",
        retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def test_calculated_requires_calculated_from() -> None:
    with pytest.raises(ValidationError):
        SlotValue(state=SlotState.CALCULATED, value=10)
    SlotValue(state=SlotState.CALCULATED, value=10, calculated_from=("a", "b"))


def test_slot_updated_appends_history_never_overwrites() -> None:
    slot = Slot()
    v1 = SlotValue(state=SlotState.USER_PROVIDED, value=650_000, source="profile", set_on_turn=1)
    slot = slot.updated(v1)
    assert slot.history == ()  # first real value replaces the empty MISSING default in place
    assert slot.value == 650_000

    v2 = SlotValue(state=SlotState.USER_PROVIDED, value=400_000, source="profile", set_on_turn=3)
    slot = slot.updated(v2)
    assert slot.value == 400_000
    assert len(slot.history) == 1
    assert slot.history[0].value == 650_000


def test_session_slot_defaults_to_missing_without_keyerror() -> None:
    from datetime import UTC, datetime

    session = ConversationSession(
        session_id="s1", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    slot = session.slot(SlotName.LIQUID_CASH_INR)
    assert slot.state is SlotState.MISSING
    assert slot.value is None


def test_conversation_session_round_trips_json() -> None:
    from datetime import UTC, datetime

    session = ConversationSession(
        session_id="s1", created_at=datetime.now(UTC), updated_at=datetime.now(UTC)
    )
    session = session.model_copy(
        update={
            "slots": {
                SlotName.LIQUID_CASH_INR: Slot().updated(
                    SlotValue(
                        state=SlotState.USER_PROVIDED,
                        value=650_000,
                        raw_text="I have 6.5 lakh",
                        source="profile",
                        set_on_turn=1,
                    )
                )
            }
        }
    )
    dumped = session.model_dump(mode="json")
    restored = ConversationSession.model_validate(dumped)
    assert restored.slot(SlotName.LIQUID_CASH_INR).value == 650_000


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
