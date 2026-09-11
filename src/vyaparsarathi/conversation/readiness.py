"""The minimum advisory readiness gate (CLAUDE.md §2, §25 Phase 6). PURE.

Two independent gates, deliberately not conflated:

* **`missing_required` / `ready`** — the three facts (business, location,
  Available Margin Capital) plus the two declinable profile questions
  (owned assets, trade experience) that `conversation/planner.py`'s rung 6
  collects, in `COLLECTION_ORDER`, before it will show a NORMAL-mode
  advisory at all. `PROPOSED_BUSINESS_TEXT` and `LOCATION_TEXT` are the same
  two hard structural blockers `_first_missing_hard_blocker` already gates
  the DAG on; `LIQUID_CASH_INR` is Available Margin Capital — with it alone,
  `finance/capacity.py` already produces the complete SIH financing answer
  (capacity, promoter contribution, loan, scheme, rate, tenure, moratorium,
  EMI), so it is required, not optional. `OWNED_ASSETS`/`TRADE_EXPERIENCE`
  block nothing structurally (the opportunity engine renormalises around a
  missing component — `market/opportunity.py`) but are asked once, in
  order, because each is a named 0.25/0.15-weight factor of the opportunity
  score; a `DECLINED` answer counts as answered, never re-asked.

* **`missing_viability` / `viability_ready`** — the four core financial
  drivers `finance/assessment.py::missing_core_drivers` needs for an actual
  DSCR/cash-flow/break-even/stress verdict on the entrepreneur's REAL plan.
  Deliberately outside `COLLECTION_ORDER` (Tier-A's own gate is never widened
  to include these) — the SIH capacity answer and market/opportunity
  evidence are allowed to exist the moment `missing_required` is empty,
  independent of whether a single rupee of the real plan has been described.
  `planner.py`'s rung 7 asks about each one in turn (`next_missing_driver_*`
  below), one at a time, *after* Tier-A — never before it, and never as a
  hard block: a `DECLINED` answer counts as asked, exactly like
  `OWNED_ASSETS`/`TRADE_EXPERIENCE` above, so the conversation always
  terminates in a delivered advisory (full or transparently partial) rather
  than looping forever on a figure the entrepreneur won't or can't give.

Derived from `finance/assessment.py::missing_core_drivers` directly (via
`conversation/plan_builder.py::build_plan_input`) — never re-listing its four
literal strings, so a Phase 4 wording change fails loudly wherever it
actually matters (`tests/test_conversation_clarify.py` already pins this
same principle for `clarify.py::MISSING_DRIVER_QUESTIONS`).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.plan_builder import build_plan_input
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    SlotState,
    satisfied_slots,
)
from vyaparsarathi.finance.assessment import missing_core_drivers


class CollectionItem(StrEnum):
    """The closed set of things the NORMAL-mode readiness gate asks about —
    a superset of `SlotName` in spirit (three members share their string
    value with a real `SlotName`) widened with the two set-valued profile
    facts (`session.assets`, `session.experience_categories`) that are not
    scalar `Slot`s at all and so cannot be `SlotName` members themselves."""

    PROPOSED_BUSINESS_TEXT = "proposed_business_text"
    LOCATION_TEXT = "location_text"
    OWNED_ASSETS = "owned_assets"
    TRADE_EXPERIENCE = "trade_experience"
    LIQUID_CASH_INR = "liquid_cash_inr"


# The order rung 6 asks in — business and location first (the two hard
# structural blockers rung 3 already gates the DAG itself on), then the two
# declinable profile questions, then Available Margin Capital last, since
# it is the one figure that immediately produces a complete SIH financing
# answer once everything else is in.
COLLECTION_ORDER: tuple[CollectionItem, ...] = (
    CollectionItem.PROPOSED_BUSINESS_TEXT,
    CollectionItem.LOCATION_TEXT,
    CollectionItem.OWNED_ASSETS,
    CollectionItem.TRADE_EXPERIENCE,
    CollectionItem.LIQUID_CASH_INR,
)

# CollectionItem -> the SlotName it mirrors, for the three scalar members.
_SCALAR_SLOT: dict[CollectionItem, SlotName] = {
    CollectionItem.PROPOSED_BUSINESS_TEXT: SlotName.PROPOSED_BUSINESS_TEXT,
    CollectionItem.LOCATION_TEXT: SlotName.LOCATION_TEXT,
    CollectionItem.LIQUID_CASH_INR: SlotName.LIQUID_CASH_INR,
}

# A set-valued profile fact (assets/experience) counts as "answered" once it
# has been stated OR explicitly declined — mirrors the scalar-slot
# convention (`DECLINED` counts as answered, never re-asked), applied to
# `AssetSetValue`/`ExperienceSetValue`'s own `state` field.
_SET_ANSWERED_STATES = frozenset({SlotState.USER_PROVIDED, SlotState.DECLINED})

# The four literal strings `finance/assessment.py::missing_core_drivers` can
# emit, mapped to the `SlotName` a "decline_slot" answer would name. Kept
# private and separate from `clarify.py::MISSING_DRIVER_QUESTIONS` (which
# maps the same four literals to question text) rather than importing one
# from the other — `clarify.py` already imports `CollectionItem` from this
# module, so the reverse import would cycle. Both dicts' keys are pinned to
# `missing_core_drivers`' actual output in their respective test files, so a
# Phase 4 wording change fails loudly in both places rather than drifting.
_DRIVER_SLOT: dict[str, SlotName] = {
    "a revenue driver (monthly_revenue, or unit_price + units_per_month)": (
        SlotName.MONTHLY_REVENUE_INR
    ),
    "a margin driver (cogs_pct or gross_margin_pct)": SlotName.COGS_PCT,
    "at least one project-cost line": SlotName.PROJECT_COST_INR,
    "fixed operating-expense lines (state a Rs 0 line if there genuinely are none)": (
        SlotName.FIXED_OPEX_INR
    ),
}


class AdvisoryReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ready: bool
    missing_required: tuple[CollectionItem, ...]
    next_ask: CollectionItem | None = None

    viability_ready: bool
    missing_viability: tuple[str, ...]
    # The first entry of `missing_viability` whose slot has not already been
    # explicitly declined — `None` once every remaining driver has either
    # been supplied or declined, i.e. there is nothing left worth asking
    # (`planner.py`'s rung 7 stops asking exactly when this goes `None`,
    # whether or not `viability_ready` ever became `True`).
    next_missing_driver_text: str | None = None
    next_missing_driver_slot: SlotName | None = None


def _item_satisfied(
    session: ConversationSession, item: CollectionItem, satisfied: frozenset[SlotName]
) -> bool:
    slot_name = _SCALAR_SLOT.get(item)
    if slot_name is not None:
        return slot_name in satisfied
    if item is CollectionItem.OWNED_ASSETS:
        return session.assets.current.state in _SET_ANSWERED_STATES
    if item is CollectionItem.TRADE_EXPERIENCE:
        return session.experience_categories.current.state in _SET_ANSWERED_STATES
    raise AssertionError(f"unhandled CollectionItem: {item!r}")  # pragma: no cover — closed enum


def advisory_readiness(session: ConversationSession) -> AdvisoryReadiness:
    """The NORMAL-mode readiness gate's whole answer for one session. Cheap
    and side-effect-free — safe to call every turn (it is; see
    `planner.py`'s rung 6)."""
    satisfied = satisfied_slots(session)
    missing_required = tuple(
        item for item in COLLECTION_ORDER if not _item_satisfied(session, item, satisfied)
    )

    plan = build_plan_input(session)
    missing_viability = tuple(missing_core_drivers(plan))
    next_driver_text = next(
        (
            driver
            for driver in missing_viability
            if session.slot(_DRIVER_SLOT[driver]).current.state is not SlotState.DECLINED
        ),
        None,
    )
    next_driver_slot = _DRIVER_SLOT[next_driver_text] if next_driver_text is not None else None

    return AdvisoryReadiness(
        ready=not missing_required,
        missing_required=missing_required,
        next_ask=missing_required[0] if missing_required else None,
        viability_ready=not missing_viability,
        missing_viability=missing_viability,
        next_missing_driver_text=next_driver_text,
        next_missing_driver_slot=next_driver_slot,
    )


__all__ = ["COLLECTION_ORDER", "AdvisoryReadiness", "CollectionItem", "advisory_readiness"]
