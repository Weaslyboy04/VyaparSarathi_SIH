"""Apply a `TurnUnderstanding` to a `ConversationSession` (CLAUDE.md §3.1,
§25 Phase 6). PURE — no I/O, no clock (the caller passes `turn_index`, never
reads a clock here).

This is where the plan's structural guarantee lives: **the LLM (or a
structured channel input) cannot invent a number.** :func:`resolve_slot_value`
requires ``value_token`` to occur verbatim in ``raw_text`` and re-derives the
value through Phase 5's `normalize_value` (`models/parameters.py`) — the same
function `SourcedParameter` uses to check a reviewed registry row is not
fabricated. A malformed update never crashes the turn: it is dropped and
reported as a warning, so one bad field never blocks every other field the
user stated in the same message.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.session_models import (
    AssetSetValue,
    ConversationSession,
    ExperienceSetValue,
    Slot,
    SlotName,
    SlotState,
    SlotValue,
)
from vyaparsarathi.conversation.understanding import TurnUnderstanding
from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.models.parameters import ValueNormalization, normalize_value
from vyaparsarathi.models.taxonomy import BusinessCategory


class ConversationInputError(ValueError):
    """A `SlotUpdateInput` (or a directly-supplied value) failed a structural
    check — not fabricated data reaching a slot, just a caller/LLM mistake.
    Always caught by :func:`apply_understanding`; never escapes it."""


class SlotKind(StrEnum):
    """What shape of value a `SlotName` holds, and therefore how its
    `value_token` is turned into a value."""

    TEXT = "text"  # value_token IS the value (stripped), no normalize_value call
    MONEY_INR = "money_inr"  # Decimal, Unit.INR
    PERCENT_RATIO = "percent_ratio"  # Decimal in [0,1], Unit.RATIO
    PERCENT_ANNUAL = "percent_annual"  # Decimal, Unit.PERCENT_PER_ANNUM (not divided)
    MONTHS = "months"  # int, Unit.MONTHS
    COUNT = "count"  # plain int (years, metres) — AS_STATED only


class SlotSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SlotKind
    allowed_normalizations: tuple[ValueNormalization, ...]


_MONEY_NORMS = (
    ValueNormalization.AS_STATED,
    ValueNormalization.LAKH_TO_INR,
    ValueNormalization.CRORE_TO_INR,
)

SLOT_SPECS: dict[SlotName, SlotSpec] = {
    SlotName.LOCATION_TEXT: SlotSpec(
        kind=SlotKind.TEXT, allowed_normalizations=(ValueNormalization.AS_STATED,)
    ),
    SlotName.PROPOSED_BUSINESS_TEXT: SlotSpec(
        kind=SlotKind.TEXT, allowed_normalizations=(ValueNormalization.AS_STATED,)
    ),
    SlotName.RADIUS_M: SlotSpec(
        kind=SlotKind.COUNT, allowed_normalizations=(ValueNormalization.AS_STATED,)
    ),
    SlotName.LIQUID_CASH_INR: SlotSpec(
        kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS
    ),
    SlotName.PROMOTER_CASH_CONTRIBUTION_INR: SlotSpec(
        kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS
    ),
    SlotName.MONTHLY_REVENUE_INR: SlotSpec(
        kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS
    ),
    SlotName.PROJECT_COST_INR: SlotSpec(
        kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS
    ),
    SlotName.FIXED_OPEX_INR: SlotSpec(kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS),
    SlotName.LOAN_PRINCIPAL_INR: SlotSpec(
        kind=SlotKind.MONEY_INR, allowed_normalizations=_MONEY_NORMS
    ),
    SlotName.YEARS_EXPERIENCE: SlotSpec(
        kind=SlotKind.COUNT, allowed_normalizations=(ValueNormalization.AS_STATED,)
    ),
    SlotName.COGS_PCT: SlotSpec(
        kind=SlotKind.PERCENT_RATIO,
        allowed_normalizations=(ValueNormalization.PERCENT_TO_RATIO, ValueNormalization.AS_STATED),
    ),
    SlotName.LOAN_INTEREST_RATE_PCT: SlotSpec(
        kind=SlotKind.PERCENT_ANNUAL,
        allowed_normalizations=(
            ValueNormalization.PERCENT_AS_ANNUAL_RATE,
            ValueNormalization.AS_STATED,
        ),
    ),
    SlotName.LOAN_TENURE_MONTHS: SlotSpec(
        kind=SlotKind.MONTHS,
        allowed_normalizations=(ValueNormalization.AS_STATED, ValueNormalization.YEARS_TO_MONTHS),
    ),
    SlotName.LOAN_MORATORIUM_MONTHS: SlotSpec(
        kind=SlotKind.MONTHS,
        allowed_normalizations=(ValueNormalization.AS_STATED, ValueNormalization.YEARS_TO_MONTHS),
    ),
}


def resolve_slot_value(
    spec: SlotSpec, value_token: str, normalization: ValueNormalization, raw_text: str
) -> Decimal | int | str:
    """`raw_text` -> a typed value for `spec.kind`, or raise
    `ConversationInputError`. `value_token` must occur verbatim in `raw_text`;
    for non-text kinds the value is then re-derived by Phase 5's pure
    `normalize_value` — never trusted directly."""
    if value_token not in raw_text:
        raise ConversationInputError(
            f"value_token {value_token!r} does not occur in raw_text {raw_text!r}"
        )
    if spec.kind is SlotKind.TEXT:
        stripped = value_token.strip()
        if not stripped:
            raise ConversationInputError("value_token is empty after stripping")
        return stripped
    if normalization not in spec.allowed_normalizations:
        raise ConversationInputError(
            f"normalization {normalization!r} is not permitted for a {spec.kind.value} slot"
        )
    try:
        raw = normalize_value(value_token, normalization)
    except FinancialInputError as exc:
        raise ConversationInputError(str(exc)) from exc
    if spec.kind in (SlotKind.COUNT, SlotKind.MONTHS):
        return int(raw)
    return raw


def apply_understanding(
    session: ConversationSession, understanding: TurnUnderstanding, *, turn_index: int
) -> tuple[ConversationSession, list[str]]:
    """Fold one turn's `TurnUnderstanding` into `session`. Never raises: a
    malformed individual update is dropped and named in the returned warning
    list, but every other update in the same turn still applies."""
    slots = dict(session.slots)
    warnings: list[str] = []
    touched: list[SlotName] = []

    for update in understanding.slot_updates:
        spec = SLOT_SPECS.get(update.slot)
        if spec is None:  # pragma: no cover — SlotName is closed; defensive only
            warnings.append(f"unknown slot {update.slot!r}; ignored")
            continue
        try:
            value = resolve_slot_value(
                spec, update.value_token, update.normalization, update.raw_text
            )
        except ConversationInputError as exc:
            warnings.append(f"could not apply update to {update.slot.value}: {exc}")
            continue
        new_value = SlotValue(
            state=SlotState.USER_PROVIDED,
            value=value,
            raw_text=update.raw_text,
            value_token=update.value_token,
            normalization=None if spec.kind is SlotKind.TEXT else update.normalization,
            source="profile",
            set_on_turn=turn_index,
        )
        slots[update.slot] = slots.get(update.slot, Slot()).updated(new_value)
        touched.append(update.slot)

    declined = set(session.declined_slots)
    for name in understanding.declined_slots:
        declined.add(name)
        slots[name] = slots.get(name, Slot()).updated(
            SlotValue(state=SlotState.DECLINED, set_on_turn=turn_index)
        )
        touched.append(name)

    assets = session.assets
    if understanding.asset_update is not None:
        merged = frozenset(assets.current.items) | frozenset(understanding.asset_update.items)
        assets = assets.updated(
            AssetSetValue(
                state=SlotState.USER_PROVIDED,
                items=merged,
                raw_text=understanding.asset_update.raw_text,
                set_on_turn=turn_index,
            )
        )

    experience = session.experience_categories
    if understanding.experience_update is not None:
        merged_experience = frozenset(experience.current.items) | frozenset(
            understanding.experience_update.items
        )
        experience = experience.updated(
            ExperienceSetValue(
                state=SlotState.USER_PROVIDED,
                items=merged_experience,
                raw_text=understanding.experience_update.raw_text,
                set_on_turn=turn_index,
            )
        )

    selected_geocode_candidate = session.selected_geocode_candidate
    if understanding.selected_choice is not None:
        selected_geocode_candidate = understanding.selected_choice

    new_session = session.model_copy(
        update={
            "slots": slots,
            "declined_slots": frozenset(declined),
            "assets": assets,
            "experience_categories": experience,
            "selected_geocode_candidate": selected_geocode_candidate,
        }
    )
    return new_session, warnings


def set_slot_ambiguous(
    session: ConversationSession,
    name: SlotName,
    options: tuple[str, ...],
    *,
    raw_text: str | None,
    turn_index: int,
) -> ConversationSession:
    """Used by the orchestrator after an engine step reports an ambiguity
    (e.g. `DiscoveryStatus.LOCATION_AMBIGUOUS`) — never by direct user input."""
    slots = dict(session.slots)
    slots[name] = slots.get(name, Slot()).updated(
        SlotValue(
            state=SlotState.AMBIGUOUS, options=options, raw_text=raw_text, set_on_turn=turn_index
        )
    )
    return session.model_copy(update={"slots": slots})


def set_slot_assumed(
    session: ConversationSession,
    name: SlotName,
    value: Decimal | int | str,
    *,
    rationale: str,
    config_source: str,
    turn_index: int,
) -> ConversationSession:
    """A configured MVP default (CLAUDE.md §3.5) — e.g. the catchment radius
    the entrepreneur never stated. Never used for a money/rate/margin field:
    those stay `MISSING` and drive a clarification instead (CLAUDE.md §30)."""
    if not config_source.startswith("config:"):
        raise ConversationInputError("config_source must start with 'config:'")
    slots = dict(session.slots)
    slots[name] = slots.get(name, Slot()).updated(
        SlotValue(
            state=SlotState.ASSUMED,
            value=value,
            rationale=rationale,
            source=config_source,
            set_on_turn=turn_index,
        )
    )
    return session.model_copy(update={"slots": slots})


def select_geocode_candidate(session: ConversationSession, index: int) -> ConversationSession:
    return session.model_copy(update={"selected_geocode_candidate": index})


def set_resolved_category(
    session: ConversationSession,
    *,
    category: BusinessCategory | None,
    resolved: bool,
    subtypes: tuple[str, ...],
) -> ConversationSession:
    return session.model_copy(
        update={
            "resolved_category": category,
            "resolved_category_resolved": resolved,
            "resolved_subtypes": subtypes,
        }
    )


__all__ = [
    "SLOT_SPECS",
    "ConversationInputError",
    "SlotKind",
    "SlotSpec",
    "apply_understanding",
    "resolve_slot_value",
    "select_geocode_candidate",
    "set_resolved_category",
    "set_slot_ambiguous",
    "set_slot_assumed",
]
