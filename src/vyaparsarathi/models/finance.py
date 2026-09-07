"""The Phase 4 financial-plan seam and its provenance model (CLAUDE.md §14, §15,
§22, §23).

Two concerns live here, deliberately together:

1. **`FinancialInput` / `InputKind`** — the reusable provenance primitive every
   financial figure in this engine is wrapped in. It answers, for every number
   that ends up in a calculation: is this what the entrepreneur told us
   (``user_provided``), a fact retrieved from an official source
   (``sourced`` — the Phase 5 seam), a configured MVP convention we are
   choosing to assume (``assumed``, and it must say why), or something this
   engine derived (``calculated``)? There is deliberately **no default value**
   for any market claim (revenue, price, volume, margin, a cost line, a rate,
   a tenure, a moratorium) anywhere in this module or in ``finance/*`` — those
   fields are always `FinancialInput | None`, and a missing one is read by the
   engine as *evidence not available*, never filled in.

2. **`FinancialPlanInput`** — the acquisition → engine seam, following the
   `DemandEvidence` / `OpportunityEvidence` pattern: one JSON-round-trippable
   object the pure engine in ``finance/`` consumes, built by a caller (a demo
   fixture in Phase 4; a retrieval layer in Phase 5) rather than by the engine
   itself.

This module imports only `models.profile`, `models.taxonomy` and the shared
`errors` module — never `market` (the cycle `market` → `models` already runs
the other way) and never `finance` (engines are downstream of their input
models, not the reverse).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory


class InputKind(StrEnum):
    """What kind of fact a `FinancialInput` rests on (CLAUDE.md §23's four-way
    distinction, specialised for money)."""

    USER_PROVIDED = "user_provided"  # the entrepreneur stated it; unverified
    SOURCED = "sourced"  # an external document/dataset (the Phase 5 seam)
    ASSUMED = "assumed"  # a configured, rationalised MVP convention
    CALCULATED = "calculated"  # derived by this engine from other inputs


class Unit(StrEnum):
    """Units a `FinancialInput.value` may carry. Kept coarse and explicit so a
    value can never be silently reinterpreted."""

    INR = "inr"
    INR_PER_MONTH = "inr_per_month"
    INR_PER_UNIT = "inr_per_unit"
    UNITS_PER_MONTH = "units_per_month"
    PERCENT_PER_ANNUM = "percent_per_annum"
    RATIO = "ratio"
    MONTHS = "months"
    DAYS = "days"


class FinancialInput(BaseModel):
    """One provenance-tagged financial fact. Frozen: once built it does not
    change — a re-derivation makes a new `FinancialInput` with
    ``kind=CALCULATED`` and ``calculated_from`` naming its inputs.

    "Missing" is expressed by a `FinancialInput | None` field on the parent
    model being `None` — never by a `FinancialInput` instance holding a `None`
    value. A `FinancialInput` that exists always carries a concrete value; that
    keeps "was this supplied at all" unambiguous everywhere downstream.

    Validation rules (CLAUDE.md §15 "every result object records its inputs
    and assumptions"; §30 "do not silently substitute assumptions for official
    scheme rules"):

    * ``ASSUMED`` requires a non-empty ``rationale`` and a ``source`` starting
      with ``"config:"`` — an assumption with no stated reason, or one that
      claims to come from somewhere else, is a contradiction in terms.
    * ``SOURCED`` requires ``source``, ``source_ref`` and ``retrieved_at`` —
      Phase 4 accepts sourced facts but does not retrieve them; a sourced
      value with no citation cannot be told apart from an assumption.
    * ``CALCULATED`` requires a non-empty ``calculated_from`` (the labels it
      was derived from) and forbids ``source_ref`` (a calculation is not a
      citation).
    * ``USER_PROVIDED`` requires ``source == "profile"`` and forbids
      ``confidence`` — the entrepreneur's own statement is unverified by
      definition; attaching a confidence number would imply otherwise.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str = Field(min_length=1)  # e.g. "monthly_revenue", "interest_rate_pct"
    # Every unit this engine carries (money, a rate, a count of months/days, a
    # ratio) is non-negative by construction; a negative figure here is always
    # a caller mistake, never a legitimate financial fact.
    value: Decimal | int = Field(ge=0)
    unit: Unit
    kind: InputKind
    rationale: str = ""  # WHY this value; required (non-empty) for ASSUMED
    source: str | None = None
    source_ref: str | None = None
    retrieved_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    calculated_from: tuple[str, ...] = Field(default_factory=tuple)

    @field_validator("value", mode="before")
    @classmethod
    def _reject_float_value(cls, v: object) -> object:
        # mode="before" is required: pydantic's `Decimal | int` coercion runs
        # BEFORE an "after" validator, so a `float` would already have been
        # silently turned into a `Decimal` by the time an "after" hook saw it.
        if isinstance(v, float):
            raise FinancialInputError(
                f"FinancialInput.value rejects float ({v!r}); pass an int or a Decimal "
                "(CLAUDE.md §4.2)."
            )
        return v

    @model_validator(mode="after")
    def _kind_shape(self) -> FinancialInput:
        if self.kind is InputKind.ASSUMED:
            if not self.rationale.strip():
                raise ValueError("an ASSUMED FinancialInput requires a non-empty rationale")
            if not (self.source or "").startswith("config:"):
                raise ValueError(
                    "an ASSUMED FinancialInput must set source to a 'config:...' reference"
                )
        elif self.kind is InputKind.SOURCED:
            if not self.source:
                raise ValueError("a SOURCED FinancialInput requires source")
            if not self.source_ref:
                raise ValueError("a SOURCED FinancialInput requires source_ref (a citation)")
            if self.retrieved_at is None:
                raise ValueError("a SOURCED FinancialInput requires retrieved_at")
        elif self.kind is InputKind.CALCULATED:
            if not self.calculated_from:
                raise ValueError("a CALCULATED FinancialInput requires calculated_from")
            if self.source_ref is not None:
                raise ValueError("a CALCULATED FinancialInput must not carry source_ref")
        elif self.kind is InputKind.USER_PROVIDED:
            if self.source != "profile":
                raise ValueError("a USER_PROVIDED FinancialInput must set source='profile'")
            if self.confidence is not None:
                raise ValueError(
                    "a USER_PROVIDED FinancialInput must not carry confidence — it is "
                    "unverified by definition (CLAUDE.md §13)"
                )
        return self


class AssumptionRegister(BaseModel):
    """Every `FinancialInput` a run touched, and how much of the plan rests on
    configured assumptions rather than stated or sourced facts. The financial
    analogue of Phase 2C's `demand_data_confidence`: reported once,
    unmultiplied, and it never changes a verdict (CLAUDE.md §22)."""

    model_config = ConfigDict(extra="forbid")

    inputs: list[FinancialInput] = Field(default_factory=list)
    counts_by_kind: dict[InputKind, int] = Field(default_factory=dict)
    core_drivers_total: int = 0
    core_drivers_provided_or_sourced: int = 0
    assumption_share: float = Field(default=0.0, ge=0.0, le=1.0)
    note: str = (
        "How much of this plan rests on configured assumptions rather than stated or "
        "sourced facts. NOT a probability of success and NOT a measure of the business "
        "(CLAUDE.md §22)."
    )


# --- project cost -----------------------------------------------------------


class CostLineKind(StrEnum):
    EQUIPMENT = "equipment"
    CIVIL_WORK = "civil_work"
    FURNITURE_FIXTURES = "furniture_fixtures"
    DEPOSIT = "deposit"
    LICENCE = "licence"
    PRE_OPERATIVE = "pre_operative"


class CostLine(BaseModel):
    """One project-cost line item. ``amount`` must carry ``unit=Unit.INR``;
    checked by the engine, not here, to keep this module free of `finance/`
    imports."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    kind: CostLineKind
    amount: FinancialInput


class AssetSpendOffset(BaseModel):
    """An owned asset reduces what a `CostLine` needs to spend. It is
    deliberately **not** a valuation of the asset and does **not** count as
    promoter margin — CLAUDE.md §13/§14: cash and physical assets are tracked
    separately and never silently summed, and whether an asset satisfies a
    scheme's margin rule is a retrieved rule this engine does not make."""

    model_config = ConfigDict(extra="forbid")

    asset: AssetKind
    reduces_line: str = Field(min_length=1)  # the CostLine.label it offsets
    amount_avoided: FinancialInput  # kind must be USER_PROVIDED or SOURCED
    note: str = (
        "Spend avoided because the entrepreneur already owns this asset. This is NOT a "
        "valuation of the asset and does NOT count as promoter margin — margin is a "
        "scheme rule, decided elsewhere."
    )

    @field_validator("amount_avoided")
    @classmethod
    def _not_assumed(cls, v: FinancialInput) -> FinancialInput:
        if v.kind is InputKind.ASSUMED:
            raise ValueError(
                "AssetSpendOffset.amount_avoided must be USER_PROVIDED or SOURCED, never "
                "ASSUMED — this engine does not invent a value for someone's asset"
            )
        return v


class ProjectCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[CostLine] = Field(default_factory=list)
    offsets: list[AssetSpendOffset] = Field(default_factory=list)
    contingency_pct: FinancialInput | None = None  # unit=RATIO


# --- working capital ---------------------------------------------------------


class WorkingCapitalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory_days: FinancialInput | None = None  # unit=DAYS
    receivable_days: FinancialInput | None = None  # unit=DAYS
    payable_days: FinancialInput | None = None  # unit=DAYS
    opening_inventory: FinancialInput | None = None  # unit=INR
    opex_cushion_months: FinancialInput | None = None  # unit=MONTHS


# --- revenue ------------------------------------------------------------


class RevenueInput(BaseModel):
    """Either ``monthly_revenue`` or the pair (``unit_price``,
    ``units_per_month``) — never both, and a lone one of the pair is a
    malformed plan, not a missing-evidence case, so it is rejected here rather
    than surfaced as `INSUFFICIENT_FINANCIAL_EVIDENCE`. All three may be
    `None`: that IS the missing-evidence case, and the engine reports it as
    such rather than raising."""

    model_config = ConfigDict(extra="forbid")

    monthly_revenue: FinancialInput | None = None  # unit=INR_PER_MONTH
    unit_price: FinancialInput | None = None  # unit=INR_PER_UNIT
    units_per_month: FinancialInput | None = None  # unit=UNITS_PER_MONTH
    ramp_months: FinancialInput | None = None  # unit=MONTHS
    ramp_start_pct: FinancialInput | None = None  # unit=RATIO, in [0, 1]
    # 12 monthly multipliers averaging 1.0; validated by the engine, not here,
    # so a caller can still assemble a partially-invalid plan for a
    # missing-evidence test without a model-level exception.
    seasonality_index: tuple[Decimal, ...] | None = None

    @model_validator(mode="after")
    def _driver_shape(self) -> RevenueInput:
        has_flat = self.monthly_revenue is not None
        has_price = self.unit_price is not None
        has_volume = self.units_per_month is not None
        if has_price != has_volume:
            raise ValueError(
                "unit_price and units_per_month must be supplied together, or not at all"
            )
        if has_flat and has_price and has_volume:
            raise ValueError("supply monthly_revenue OR (unit_price, units_per_month), not both")
        return self


# --- operating costs ---------------------------------------------------------


class OpexLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    amount: FinancialInput  # unit=INR_PER_MONTH


class OperatingCostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cogs_pct: FinancialInput | None = None  # unit=RATIO
    gross_margin_pct: FinancialInput | None = None  # unit=RATIO
    variable_opex_pct: FinancialInput | None = None  # unit=RATIO, of revenue
    fixed_lines: list[OpexLine] = Field(default_factory=list)

    @model_validator(mode="after")
    def _margin_shape(self) -> OperatingCostInput:
        if self.cogs_pct is not None and self.gross_margin_pct is not None:
            raise ValueError("supply cogs_pct OR gross_margin_pct, not both")
        return self


# --- financing / debt ---------------------------------------------------------


class MoratoriumTreatment(StrEnum):
    """How interest is handled during the moratorium (CLAUDE.md §17). No
    default is ever applied — a `LoanTerms` must state one explicitly."""

    NONE = "none"  # no moratorium (moratorium_months == 0)
    INTEREST_SERVICED = "interest_serviced"  # interest paid monthly; principal deferred
    INTEREST_CAPITALISED = "interest_capitalised"  # interest added to principal monthly
    INTEREST_ACCRUED_PAID_ON_EMI_START = "interest_accrued_paid_on_emi_start"


class RepaymentFrequency(StrEnum):
    """Only monthly repayment is modelled in Phase 4; the field exists so a
    future frequency is additive rather than a breaking change."""

    MONTHLY = "monthly"


class LoanTerms(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_requested: FinancialInput  # unit=INR
    interest_rate_pct: FinancialInput  # unit=PERCENT_PER_ANNUM
    tenure_months: FinancialInput  # unit=MONTHS (post-moratorium EMI count)
    moratorium_months: FinancialInput  # unit=MONTHS
    moratorium_treatment: MoratoriumTreatment
    repayment_frequency: RepaymentFrequency = RepaymentFrequency.MONTHLY


class FinancingInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    promoter_cash_contribution: FinancialInput | None = None  # unit=INR
    other_committed_funds: FinancialInput | None = None  # unit=INR
    retained_working_capital_reserve: FinancialInput | None = None  # unit=INR
    loan: LoanTerms | None = None
    # A scheme's stated promoter-margin requirement, when actually retrieved or
    # stated — never invented by this engine (CLAUDE.md §14, §18).
    declared_margin_requirement: FinancialInput | None = None

    @field_validator("declared_margin_requirement")
    @classmethod
    def _margin_not_assumed(cls, v: FinancialInput | None) -> FinancialInput | None:
        if v is not None and v.kind is InputKind.ASSUMED:
            raise ValueError(
                "declared_margin_requirement must not be ASSUMED — Phase 4 must never "
                "invent a scheme margin (CLAUDE.md §14, §18); that is a Phase 5 retrieved "
                "rule or an explicit user/sourced statement"
            )
        return v


# --- the plan -----------------------------------------------------------


class FinancialPlanInput(BaseModel):
    """The acquisition → engine seam (CLAUDE.md §15). Pure input to
    `vyaparsarathi.finance.assessment.assess_financials`. Every field a caller
    would normally invent is instead an optional `FinancialInput` — a `None`
    is read by the engine as *evidence not available*, not as zero and not as
    a reason to guess."""

    model_config = ConfigDict(extra="forbid")

    category: BusinessCategory
    profile: EntrepreneurProfile
    project_cost: ProjectCostInput
    working_capital: WorkingCapitalInput
    revenue: RevenueInput
    operating_costs: OperatingCostInput
    financing: FinancingInput
    horizon_months: int = Field(gt=0)
    # Display-only label (e.g. "2026-04"); never read by any calculation
    # (CLAUDE.md §28 — no clock in the engine). Month arithmetic is always in
    # integer month indices starting at 0.
    start_month_label: str | None = None
