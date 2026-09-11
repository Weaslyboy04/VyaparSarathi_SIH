"""`conversation/readiness.py` (CLAUDE.md §2, §25 Phase 6). Pure; no I/O."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.conversation.deltas import apply_understanding
from vyaparsarathi.conversation.readiness import (
    COLLECTION_ORDER,
    CollectionItem,
    advisory_readiness,
)
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName
from vyaparsarathi.conversation.understanding import (
    AssetUpdateInput,
    ExperienceUpdateInput,
    Intent,
    SlotUpdateInput,
    TurnUnderstanding,
)
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.profile import AssetKind
from vyaparsarathi.models.taxonomy import BusinessCategory


def _session() -> ConversationSession:
    now = datetime.now(UTC)
    return ConversationSession(session_id="s1", created_at=now, updated_at=now)


def _provide(session: ConversationSession, **updates: object) -> ConversationSession:
    """Convenience: `_provide(session, business="grocery", location="Bhagwanpur")`
    etc. applies one `TurnUnderstanding` per call."""
    slot_map = {
        "business": SlotName.PROPOSED_BUSINESS_TEXT,
        "location": SlotName.LOCATION_TEXT,
        "cash": SlotName.LIQUID_CASH_INR,
    }
    slot_updates = tuple(
        SlotUpdateInput(
            slot=slot_map[key],
            raw_text=str(value),
            value_token=str(value),
            normalization=ValueNormalization.AS_STATED,
        )
        for key, value in updates.items()
        if key in slot_map
    )
    understanding = TurnUnderstanding(intent=Intent.PROVIDE_INFO, slot_updates=slot_updates)
    new_session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    return new_session


def test_empty_session_is_not_ready_and_asks_for_business_first() -> None:
    readiness = advisory_readiness(_session())
    assert readiness.ready is False
    assert readiness.next_ask is CollectionItem.PROPOSED_BUSINESS_TEXT
    assert readiness.missing_required == COLLECTION_ORDER


def test_collection_order_is_respected_one_item_at_a_time() -> None:
    session = _session()
    assert advisory_readiness(session).next_ask is CollectionItem.PROPOSED_BUSINESS_TEXT

    session = _provide(session, business="grocery")
    assert advisory_readiness(session).next_ask is CollectionItem.LOCATION_TEXT

    session = _provide(session, location="Bhagwanpur, Bihar")
    assert advisory_readiness(session).next_ask is CollectionItem.OWNED_ASSETS

    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        asset_update=AssetUpdateInput(items=(AssetKind.STOREFRONT,), raw_text="I have a shop"),
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    assert advisory_readiness(session).next_ask is CollectionItem.TRADE_EXPERIENCE

    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        experience_update=ExperienceUpdateInput(
            items=(BusinessCategory.GROCERY,), raw_text="I've run a grocery before"
        ),
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    assert advisory_readiness(session).next_ask is CollectionItem.LIQUID_CASH_INR

    session = _provide(session, cash="100000")
    readiness = advisory_readiness(session)
    assert readiness.ready is True
    assert readiness.next_ask is None
    assert readiness.missing_required == ()


def test_declined_assets_and_experience_count_as_answered() -> None:
    session = _session()
    session = _provide(session, business="grocery", location="Bhagwanpur, Bihar")
    understanding = TurnUnderstanding(intent=Intent.DECLINE_SLOT, assets_declined=True)
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    assert advisory_readiness(session).next_ask is CollectionItem.TRADE_EXPERIENCE

    understanding = TurnUnderstanding(intent=Intent.DECLINE_SLOT, experience_declined=True)
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    assert advisory_readiness(session).next_ask is CollectionItem.LIQUID_CASH_INR

    session = _provide(session, cash="100000")
    assert advisory_readiness(session).ready is True


def test_tier_a_ready_with_exactly_three_answers_plus_two_declines() -> None:
    """Business + location + cash, with assets/experience explicitly
    declined: exactly the minimum path to `ready=True`."""
    session = _session()
    session = _provide(session, business="grocery", location="Bhagwanpur, Bihar")
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, assets_declined=True, experience_declined=True
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    session = _provide(session, cash="100000")
    readiness = advisory_readiness(session)
    assert readiness.ready is True
    assert readiness.missing_required == ()


def test_missing_viability_is_independent_of_missing_required() -> None:
    """Tier A being ready says nothing about viability readiness — the four
    core financial drivers are a wholly separate gate."""
    session = _session()
    session = _provide(session, business="grocery", location="Bhagwanpur, Bihar")
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, assets_declined=True, experience_declined=True
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    session = _provide(session, cash="100000")
    readiness = advisory_readiness(session)
    assert readiness.ready is True
    assert readiness.viability_ready is False
    assert len(readiness.missing_viability) == 4


def test_missing_viability_matches_missing_core_drivers_exactly() -> None:
    from vyaparsarathi.conversation.plan_builder import build_plan_input
    from vyaparsarathi.finance.assessment import missing_core_drivers

    session = _session()
    readiness = advisory_readiness(session)
    assert set(readiness.missing_viability) == set(missing_core_drivers(build_plan_input(session)))


def _tier_a_ready_session() -> ConversationSession:
    session = _session()
    session = _provide(session, business="grocery", location="Bhagwanpur, Bihar")
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, assets_declined=True, experience_declined=True
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    return _provide(session, cash="100000")


def test_next_missing_driver_is_revenue_first_when_nothing_stated() -> None:
    """`missing_core_drivers`' own order is followed: revenue, then margin,
    then project cost, then fixed opex."""
    readiness = advisory_readiness(_tier_a_ready_session())
    assert readiness.next_missing_driver_text == (
        "a revenue driver (monthly_revenue, or unit_price + units_per_month)"
    )
    assert readiness.next_missing_driver_slot is SlotName.MONTHLY_REVENUE_INR


def test_declining_a_driver_advances_to_the_next_one() -> None:
    session = _tier_a_ready_session()
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT, declined_slots=(SlotName.MONTHLY_REVENUE_INR,)
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    readiness = advisory_readiness(session)
    assert readiness.next_missing_driver_text == "a margin driver (cogs_pct or gross_margin_pct)"
    assert readiness.next_missing_driver_slot is SlotName.COGS_PCT


def test_next_missing_driver_is_none_once_every_driver_is_declined() -> None:
    """Every driver declined -> nothing left worth asking, even though
    `viability_ready` stays `False` (declining is not the same as
    supplying a real figure — the eventual advisory must still say so)."""
    session = _tier_a_ready_session()
    understanding = TurnUnderstanding(
        intent=Intent.DECLINE_SLOT,
        declined_slots=(
            SlotName.MONTHLY_REVENUE_INR,
            SlotName.COGS_PCT,
            SlotName.PROJECT_COST_INR,
            SlotName.FIXED_OPEX_INR,
        ),
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    readiness = advisory_readiness(session)
    assert readiness.next_missing_driver_text is None
    assert readiness.next_missing_driver_slot is None
    assert readiness.viability_ready is False
    assert len(readiness.missing_viability) == 4


def test_next_missing_driver_is_none_once_viability_is_actually_satisfied() -> None:
    session = _tier_a_ready_session()
    understanding = TurnUnderstanding(
        intent=Intent.PROVIDE_INFO,
        slot_updates=(
            SlotUpdateInput(
                slot=SlotName.MONTHLY_REVENUE_INR,
                raw_text="50000",
                value_token="50000",
                normalization=ValueNormalization.AS_STATED,
            ),
            SlotUpdateInput(
                slot=SlotName.COGS_PCT,
                raw_text="60",
                value_token="60",
                normalization=ValueNormalization.PERCENT_TO_RATIO,
            ),
            SlotUpdateInput(
                slot=SlotName.PROJECT_COST_INR,
                raw_text="200000",
                value_token="200000",
                normalization=ValueNormalization.AS_STATED,
            ),
            SlotUpdateInput(
                slot=SlotName.FIXED_OPEX_INR,
                raw_text="5000",
                value_token="5000",
                normalization=ValueNormalization.AS_STATED,
            ),
        ),
    )
    session, _ = apply_understanding(session, understanding, turn_index=session.turn_index + 1)
    readiness = advisory_readiness(session)
    assert readiness.viability_ready is True
    assert readiness.next_missing_driver_text is None
    assert readiness.next_missing_driver_slot is None


def test_driver_slot_mapping_covers_exactly_the_missing_core_driver_literals() -> None:
    """Anti-drift: `readiness.py`'s private driver->slot mapping must name
    exactly the four literal strings `missing_core_drivers` can emit for a
    maximally-empty plan — never more, never fewer, never a stale wording."""
    from vyaparsarathi.conversation.plan_builder import build_plan_input
    from vyaparsarathi.conversation.readiness import _DRIVER_SLOT
    from vyaparsarathi.finance.assessment import missing_core_drivers

    all_missing = missing_core_drivers(build_plan_input(_session()))
    assert len(all_missing) == 4
    assert set(_DRIVER_SLOT.keys()) == set(all_missing)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
