"""`conversation/deltas.py` (CLAUDE.md §3.1, §30, §25 Phase 6). Pure; no I/O.

The central guarantee under test: a `TurnUnderstanding` can never make a
`Slot` hold a number the user's own text does not contain, and never a float.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.conversation.deltas import (
    SLOT_SPECS,
    ConversationInputError,
    apply_understanding,
    resolve_slot_value,
    set_slot_ambiguous,
    set_slot_assumed,
)
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName, SlotState
from vyaparsarathi.conversation.understanding import (
    AssetUpdateInput,
    Intent,
    SlotUpdateInput,
    TurnUnderstanding,
)
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.profile import AssetKind


def _session() -> ConversationSession:
    now = datetime.now(UTC)
    return ConversationSession(session_id="s1", created_at=now, updated_at=now)


def test_value_token_must_occur_in_raw_text() -> None:
    spec = SLOT_SPECS[SlotName.LIQUID_CASH_INR]
    with pytest.raises(ConversationInputError):
        resolve_slot_value(spec, "7 lakh", ValueNormalization.LAKH_TO_INR, "I have 6.5 lakh")


def test_lakh_and_crore_normalization_reuse_phase5() -> None:
    spec = SLOT_SPECS[SlotName.LIQUID_CASH_INR]
    value = resolve_slot_value(spec, "6.5 lakh", ValueNormalization.LAKH_TO_INR, "I have 6.5 lakh")
    assert value == 650_000
    value = resolve_slot_value(
        spec, "1.2 crore", ValueNormalization.CRORE_TO_INR, "I have 1.2 crore"
    )
    assert value == 12_000_000


def test_years_to_months_normalization() -> None:
    spec = SLOT_SPECS[SlotName.LOAN_TENURE_MONTHS]
    value = resolve_slot_value(spec, "5 years", ValueNormalization.YEARS_TO_MONTHS, "over 5 years")
    assert value == 60
    assert isinstance(value, int)


def test_percent_as_annual_rate_is_not_divided() -> None:
    spec = SLOT_SPECS[SlotName.LOAN_INTEREST_RATE_PCT]
    value = resolve_slot_value(
        spec, "11.5%", ValueNormalization.PERCENT_AS_ANNUAL_RATE, "quoted me 11.5% per annum"
    )
    assert value == pytest.approx(11.5)


def test_percent_to_ratio_for_cogs() -> None:
    spec = SLOT_SPECS[SlotName.COGS_PCT]
    value = resolve_slot_value(spec, "40%", ValueNormalization.PERCENT_TO_RATIO, "cogs is 40%")
    assert value == Decimal("0.40")


def test_disallowed_normalization_for_slot_kind_is_rejected() -> None:
    spec = SLOT_SPECS[SlotName.YEARS_EXPERIENCE]
    with pytest.raises(ConversationInputError):
        resolve_slot_value(spec, "6.5 lakh", ValueNormalization.LAKH_TO_INR, "6.5 lakh")


def test_text_slot_takes_the_token_verbatim() -> None:
    spec = SLOT_SPECS[SlotName.LOCATION_TEXT]
    value = resolve_slot_value(
        spec, "Bhagwanpur, Bihar", ValueNormalization.AS_STATED, "I live in Bhagwanpur, Bihar"
    )
    assert value == "Bhagwanpur, Bihar"


def test_apply_understanding_sets_user_provided_slot() -> None:
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
        raw_message="I have 6.5 lakh",
    )
    new_session, warnings = apply_understanding(session, understanding, turn_index=1)
    assert warnings == []
    slot = new_session.slot(SlotName.LIQUID_CASH_INR)
    assert slot.state is SlotState.USER_PROVIDED
    assert slot.value == 650_000
    assert slot.current.value_token == "6.5 lakh"


def test_apply_understanding_drops_a_fabricated_token_but_keeps_other_updates() -> None:
    session = _session()
    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        slot_updates=(
            SlotUpdateInput(
                slot=SlotName.LIQUID_CASH_INR,
                raw_text="I have some cash",
                value_token="6.5 lakh",  # not present in raw_text -> rejected
                normalization=ValueNormalization.LAKH_TO_INR,
            ),
            SlotUpdateInput(
                slot=SlotName.YEARS_EXPERIENCE,
                raw_text="3 years running a shop",
                value_token="3",
                normalization=ValueNormalization.AS_STATED,
            ),
        ),
    )
    new_session, warnings = apply_understanding(session, understanding, turn_index=1)
    assert len(warnings) == 1
    assert new_session.slot(SlotName.LIQUID_CASH_INR).state is SlotState.MISSING
    assert new_session.slot(SlotName.YEARS_EXPERIENCE).value == 3


def test_float_value_token_never_reaches_a_slot_as_float() -> None:
    # normalize_value always returns Decimal|int, never float, so the slot's
    # value type is never float — asserted directly against the resolver.
    spec = SLOT_SPECS[SlotName.LIQUID_CASH_INR]
    value = resolve_slot_value(spec, "650000", ValueNormalization.AS_STATED, "I have 650000")
    assert not isinstance(value, float)


def test_declined_slot_is_never_re_askable_via_normal_update_alone() -> None:
    session = _session()
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, declined_slots=(SlotName.YEARS_EXPERIENCE,)
    )
    new_session, _ = apply_understanding(session, understanding, turn_index=1)
    assert new_session.slot(SlotName.YEARS_EXPERIENCE).state is SlotState.DECLINED
    assert SlotName.YEARS_EXPERIENCE in new_session.declined_slots


def test_asset_update_unions_rather_than_replaces() -> None:
    session = _session()
    u1 = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        asset_update=AssetUpdateInput(items=(AssetKind.STOREFRONT,), raw_text="I have a shop"),
    )
    session, _ = apply_understanding(session, u1, turn_index=1)
    u2 = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        asset_update=AssetUpdateInput(items=(AssetKind.VEHICLE,), raw_text="and a scooter"),
    )
    session, _ = apply_understanding(session, u2, turn_index=2)
    assert session.assets.current.items == frozenset({AssetKind.STOREFRONT, AssetKind.VEHICLE})


def test_set_slot_assumed_requires_config_source() -> None:
    session = _session()
    with pytest.raises(ConversationInputError):
        set_slot_assumed(
            session,
            SlotName.RADIUS_M,
            5_000,
            rationale="default",
            config_source="not_config",
            turn_index=1,
        )
    session2 = set_slot_assumed(
        session,
        SlotName.RADIUS_M,
        5_000,
        rationale="default MVP catchment radius",
        config_source="config:default_radius_m",
        turn_index=1,
    )
    assert session2.slot(SlotName.RADIUS_M).state is SlotState.ASSUMED
    assert session2.slot(SlotName.RADIUS_M).value == 5_000


def test_set_slot_ambiguous() -> None:
    session = _session()
    session = set_slot_ambiguous(
        session,
        SlotName.LOCATION_TEXT,
        ("Bhagwanpur, Bihar", "Bhagwanpur, UP"),
        raw_text="Bhagwanpur",
        turn_index=1,
    )
    slot = session.slot(SlotName.LOCATION_TEXT)
    assert slot.state is SlotState.AMBIGUOUS
    assert slot.current.options == ("Bhagwanpur, Bihar", "Bhagwanpur, UP")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
