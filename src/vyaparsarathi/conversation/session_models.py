"""Conversation state (CLAUDE.md §2, §13, §23, §25 Phase 6). PURE — no I/O.

A :class:`ConversationSession` is the structured state a multi-turn advisory
conversation carries between turns, so a turn extracts into structured
deltas rather than the whole message being re-parsed from scratch each time
(the plan's requirement 4). Every fact the entrepreneur, an engine, or a
retrieved document contributed is a :class:`Slot`, tagged with exactly one
:class:`SlotState` — the superset of Phase 4's `InputKind`
(`models/finance.py`) that also names the states a conversation needs before
a fact exists at all: ``MISSING`` (never stated — the clarification engine's
only real source), ``AMBIGUOUS`` (stated but unresolved) and ``DECLINED``
(the user was asked and said no, or refused) join the four `InputKind`
members unchanged.

Corrections **append**, never overwrite: :attr:`Slot.history` keeps every
prior :class:`SlotValue`, so "actually I only have 4 lakh" is a new entry, not
a silent replacement — the DPR (Phase 8) can show what changed and when.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vyaparsarathi.models.finance import InputKind
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.profile import AssetKind
from vyaparsarathi.models.taxonomy import BusinessCategory


class SlotState(StrEnum):
    """A strict superset of `InputKind` (CLAUDE.md §23's four-way distinction),
    widened with the three conversation-only states a fact passes through
    before it exists. See `test_conversation_slots.py` for the anti-drift
    check that this stays a superset value-for-value."""

    MISSING = "missing"
    AMBIGUOUS = "ambiguous"
    DECLINED = "declined"
    USER_PROVIDED = "user_provided"
    SOURCED = "sourced"
    ASSUMED = "assumed"
    CALCULATED = "calculated"


_TO_INPUT_KIND: dict[SlotState, InputKind] = {
    SlotState.USER_PROVIDED: InputKind.USER_PROVIDED,
    SlotState.SOURCED: InputKind.SOURCED,
    SlotState.ASSUMED: InputKind.ASSUMED,
    SlotState.CALCULATED: InputKind.CALCULATED,
}
_FROM_INPUT_KIND: dict[InputKind, SlotState] = {v: k for k, v in _TO_INPUT_KIND.items()}


def as_input_kind(state: SlotState) -> InputKind | None:
    """`None` for the three conversation-only states; total over `InputKind`."""
    return _TO_INPUT_KIND.get(state)


def from_input_kind(kind: InputKind) -> SlotState:
    return _FROM_INPUT_KIND[kind]


class SlotName(StrEnum):
    """The closed set of facts this phase's conversation layer tracks. Adding
    a new financial or profile driver means adding a member here AND a row in
    `deltas.py::SLOT_SPECS` AND a case in `plan_builder.py` — never an ad hoc
    string, mirroring `models/parameters.py::ParameterName`'s discipline."""

    LOCATION_TEXT = "location_text"
    RADIUS_M = "radius_m"
    PROPOSED_BUSINESS_TEXT = "proposed_business_text"

    LIQUID_CASH_INR = "liquid_cash_inr"
    PROMOTER_CASH_CONTRIBUTION_INR = "promoter_cash_contribution_inr"
    YEARS_EXPERIENCE = "years_experience"

    MONTHLY_REVENUE_INR = "monthly_revenue_inr"
    COGS_PCT = "cogs_pct"
    PROJECT_COST_INR = "project_cost_inr"
    FIXED_OPEX_INR = "fixed_opex_inr"

    LOAN_PRINCIPAL_INR = "loan_principal_inr"
    LOAN_INTEREST_RATE_PCT = "loan_interest_rate_pct"
    LOAN_TENURE_MONTHS = "loan_tenure_months"
    LOAN_MORATORIUM_MONTHS = "loan_moratorium_months"


class SlotValue(BaseModel):
    """One snapshot of a `Slot` — frozen, like `FinancialInput` (§15: "every
    result object records its inputs and assumptions"). Validators mirror
    `FinancialInput._kind_shape` (`models/finance.py:124-153`) so a
    user-quoted number and an ASSUMED default can never be confused in the
    type system, not just by convention.

    ``value`` is `None` for `MISSING`/`DECLINED`; a `Decimal | int | str` for
    every populated state (money/percent/months are `Decimal`, a count is
    `int`, free text is `str`) — one concrete union rather than a generic
    `Slot[T]` (documented pydantic-v2-generics risk in the approved plan;
    this is its stated fallback).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SlotState
    value: Decimal | int | str | None = None
    raw_text: str | None = None  # verbatim user/channel text, for the DPR
    value_token: str | None = None  # the exact substring `value` was derived from
    normalization: ValueNormalization | None = None
    options: tuple[str, ...] = ()  # AMBIGUOUS only: candidate labels
    source: str | None = None
    source_ref: str | None = None
    retrieved_at: datetime | None = None
    rationale: str = ""
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    calculated_from: tuple[str, ...] = ()
    set_on_turn: int = 0

    @model_validator(mode="after")
    def _state_shape(self) -> SlotValue:
        if self.state in (SlotState.MISSING, SlotState.DECLINED):
            if self.value is not None:
                raise ValueError(f"{self.state.value} SlotValue must not carry a value")
        elif self.state is SlotState.AMBIGUOUS:
            if not self.options:
                raise ValueError("an AMBIGUOUS SlotValue requires non-empty options")
        elif self.state is SlotState.USER_PROVIDED:
            if self.source != "profile":
                raise ValueError("a USER_PROVIDED SlotValue must set source='profile'")
            if self.confidence is not None:
                raise ValueError(
                    "a USER_PROVIDED SlotValue must not carry confidence — unverified by "
                    "definition (CLAUDE.md §13)"
                )
        elif self.state is SlotState.SOURCED:
            if not (self.source and self.source_ref and self.retrieved_at is not None):
                raise ValueError("a SOURCED SlotValue requires source, source_ref and retrieved_at")
        elif self.state is SlotState.ASSUMED:
            if not self.rationale.strip() or not (self.source or "").startswith("config:"):
                raise ValueError(
                    "an ASSUMED SlotValue requires a non-empty rationale and a "
                    "source starting with 'config:'"
                )
        elif self.state is SlotState.CALCULATED:
            if not self.calculated_from:
                raise ValueError("a CALCULATED SlotValue requires calculated_from")
        return self


def missing_slot_value() -> SlotValue:
    return SlotValue(state=SlotState.MISSING)


class Slot(BaseModel):
    """A named fact plus its correction history. `current.state ==
    SlotState.MISSING` and empty `history` is the default, unpopulated slot —
    a session need not pre-create every `SlotName`."""

    model_config = ConfigDict(extra="forbid")

    current: SlotValue = Field(default_factory=missing_slot_value)
    history: tuple[SlotValue, ...] = ()

    @property
    def state(self) -> SlotState:
        return self.current.state

    @property
    def value(self) -> Decimal | int | str | None:
        return self.current.value

    def updated(self, new: SlotValue) -> Slot:
        """A new `Slot` with `new` as current and the old current appended to
        history — corrections append, never overwrite."""
        if self.current.state is SlotState.MISSING and not self.history:
            return Slot(current=new, history=())
        return Slot(current=new, history=(*self.history, self.current))


class AssetSetValue(BaseModel):
    """One snapshot of the owned-assets set (§13: assets are a *set of
    kinds*, never a single scalar `Slot` — a new asset mentioned later is a
    union, not a correction)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SlotState
    items: frozenset[AssetKind] = frozenset()
    raw_text: str | None = None
    set_on_turn: int = 0


class AssetSetSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: AssetSetValue = Field(default_factory=lambda: AssetSetValue(state=SlotState.MISSING))
    history: tuple[AssetSetValue, ...] = ()

    def updated(self, new: AssetSetValue) -> AssetSetSlot:
        if self.current.state is SlotState.MISSING and not self.history:
            return AssetSetSlot(current=new, history=())
        return AssetSetSlot(current=new, history=(*self.history, self.current))


class ExperienceSetValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SlotState
    items: frozenset[BusinessCategory] = frozenset()
    raw_text: str | None = None
    set_on_turn: int = 0


class ExperienceSetSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: ExperienceSetValue = Field(
        default_factory=lambda: ExperienceSetValue(state=SlotState.MISSING)
    )
    history: tuple[ExperienceSetValue, ...] = ()

    def updated(self, new: ExperienceSetValue) -> ExperienceSetSlot:
        if self.current.state is SlotState.MISSING and not self.history:
            return ExperienceSetSlot(current=new, history=())
        return ExperienceSetSlot(current=new, history=(*self.history, self.current))


class StepId(StrEnum):
    """The 17-node step DAG (`conversation/workflow.py` declares the edges;
    this is just the closed set of names). `*` = impure (owns a `StepRunner`
    in `llm/tools.py` that touches network/disk); the rest are pure engine
    calls over already-acquired evidence."""

    RESOLVE_PROPOSED = "resolve_proposed"
    DISCOVER = "discover"  # *
    ANALYZE = "analyze"
    METRICS = "metrics"
    DEMAND_EVIDENCE = "demand_evidence"  # *
    DEMAND_SIGNALS = "demand_signals"
    ASSESS_MARKET = "assess_market"
    OPPORTUNITY_EVIDENCE = "opportunity_evidence"  # *
    OPPORTUNITY = "opportunity"
    FINANCE_KNOWLEDGE = "finance_knowledge"  # *
    BUILD_PLAN = "build_plan"
    BIND_PLAN = "bind_plan"
    STRUCTURE_FINANCE = "structure_finance"
    ASSESS_FINANCE = "assess_finance"
    FINANCIAL_FIT = "financial_fit"
    RECOMMEND = "recommend"
    SWOT = "swot"


class StepArtifact(BaseModel):
    """One cached step output. `payload` is a plain JSON dict
    (`result.model_dump(mode="json")`) — `conversation/` never imports the
    engine model class that produced it; `llm/tools.py` (impure) is the only
    place that reconstructs a typed object from `payload` via
    `STEP_RESULT_MODEL[step].model_validate(payload)`."""

    model_config = ConfigDict(extra="forbid")

    step: StepId
    fingerprint: str
    computed_on_turn: int
    payload: dict = Field(default_factory=dict)
    payload_type: str = ""  # informational only; not used to decide anything here


class TurnRecord(BaseModel):
    """One turn's audit trail — CLAUDE.md §33: log "names and statuses only,
    never user text ... or slot values" at INFO, so this model itself is safe
    to serialize into a log line or the DPR annexure without redaction."""

    model_config = ConfigDict(extra="forbid")

    turn_index: int
    channel: str = ""
    intent: str = ""
    slots_touched: tuple[SlotName, ...] = ()
    requested_step: StepId | None = None
    executed_step: StepId | None = None
    steps_invalidated: tuple[StepId, ...] = ()
    outcome: str = ""
    narrative_generated_by: dict[str, str] = Field(default_factory=dict)
    prompt_versions: dict[str, str] = Field(default_factory=dict)
    llm_used: bool = False
    warnings: tuple[str, ...] = ()


class ConversationSession(BaseModel):
    """The full state of one advisory conversation. Owned and persisted by
    `app/` (`database/session_repository.py`), mutated only by pure functions
    in this package (`deltas.py`, `artifacts.py`) — never in place."""

    model_config = ConfigDict(extra="forbid")

    session_id: str = Field(min_length=1)
    created_at: datetime
    updated_at: datetime
    turn_index: int = 0
    ended: bool = False

    slots: dict[SlotName, Slot] = Field(default_factory=dict)
    assets: AssetSetSlot = Field(default_factory=AssetSetSlot)
    experience_categories: ExperienceSetSlot = Field(default_factory=ExperienceSetSlot)

    # The taxonomy resolution of PROPOSED_BUSINESS_TEXT — recomputed by the
    # RESOLVE_PROPOSED step, cached here so later steps (and the renderer)
    # don't need to re-derive it. `None` until RESOLVE_PROPOSED has run once.
    resolved_category: BusinessCategory | None = None
    resolved_category_resolved: bool = False
    resolved_subtypes: tuple[str, ...] = ()

    selected_geocode_candidate: int | None = None
    declined_slots: frozenset[SlotName] = frozenset()

    artifacts: dict[StepId, StepArtifact] = Field(default_factory=dict)
    turns: tuple[TurnRecord, ...] = ()
    session_warnings: tuple[str, ...] = ()

    def slot(self, name: SlotName) -> Slot:
        """A `Slot` for `name`, defaulting to MISSING — never a `KeyError`."""
        return self.slots.get(name, Slot())


__all__ = [
    "AssetSetSlot",
    "AssetSetValue",
    "ConversationSession",
    "ExperienceSetSlot",
    "ExperienceSetValue",
    "Slot",
    "SlotName",
    "SlotState",
    "SlotValue",
    "StepArtifact",
    "StepId",
    "TurnRecord",
    "as_input_kind",
    "from_input_kind",
    "missing_slot_value",
]
