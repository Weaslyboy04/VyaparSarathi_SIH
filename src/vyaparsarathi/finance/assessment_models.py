"""Phase 4 financial-feasibility verdict models (CLAUDE.md §15, §16, §22).

`FinancialAssessmentResult` is the single top-level Phase 4 output — the same
"one result object per phase" shape as `OpportunityAnalysisResult` /
`MarketAssessmentResult`: a `status` decided by an explicit, ordered ladder
(mirroring Phase 2D's `LadderRung`), every sub-calculation echoed, a
`caveats`/`config`/`warnings` trio, and full JSON round-trippability.

There is **no financial score.** `FinancialFeasibilityStatus` is a status
enum, not a number, and nothing here is named `score`, `probability`,
`success`, or `guarantee`. It is also a *distinct* measurement from the Phase
3 opportunity score, `market_data_confidence`, and this module's own
`assumption_share` — none of the four multiplies or implies another
(CLAUDE.md §22); a business can be `underserved` at a high Phase 3 score and
`UNSERVICEABLE` here, and that is a valid, expected combination.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG, FinanceConfig
from vyaparsarathi.finance.results import (
    BreakEvenResult,
    CashFlowResult,
    DebtScheduleResult,
    DSCRResult,
    OperatingCostResult,
    ProjectCostResult,
    RevenueScheduleResult,
    WorkingCapitalResult,
)
from vyaparsarathi.models.finance import AssumptionRegister, FinancialInput
from vyaparsarathi.models.taxonomy import BusinessCategory


class FinancialFeasibilityStatus(StrEnum):
    """The verdict. Never collapsed into a single score — see the module
    docstring."""

    FEASIBLE = "feasible"
    FEASIBLE_WITH_STRETCH = "feasible_with_stretch"
    FINANCING_GAP = "financing_gap"
    CASH_FLOW_STRESS = "cash_flow_stress"
    UNSERVICEABLE = "unserviceable"
    INSUFFICIENT_FINANCIAL_EVIDENCE = "insufficient_financial_evidence"


class FinanceLadderRung(StrEnum):
    """Which precedence rung decided the status (the Phase 2D `LadderRung`
    pattern). Checked in this fixed order; the first match wins."""

    MISSING_CORE_DRIVER = "missing_core_driver"
    FUNDING_GAP = "funding_gap"
    NOT_SERVICEABLE = "not_serviceable"
    CASH_STRESS = "cash_stress"
    STRESS_SENSITIVE = "stress_sensitive"
    CLEARS_ALL = "clears_all"


class FinanceEvidenceRef(BaseModel):
    """A typed pointer at the upstream field a finding rests on (the Phase 2D
    / Phase 3 auditability pattern, widened with finance-specific sources)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal[
        "cost",
        "working_capital",
        "revenue",
        "debt",
        "cashflow",
        "dscr",
        "stress",
        "input",
        "config",
    ]
    field: str
    value: float | int | str | bool | None
    compared_to: float | None = None


class FinanceFinding(BaseModel):
    """Names the exact condition a status rests on (CLAUDE.md §16: 'names the
    exact condition that first breaks it')."""

    model_config = ConfigDict(extra="forbid")

    code: str  # stable, machine-readable, unique within a result
    message: str
    evidence: list[FinanceEvidenceRef] = Field(default_factory=list)


class StressResult(BaseModel):
    """One stress scenario's outcome (CLAUDE.md §16)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    applied: bool = True
    skipped_reason: str = ""  # e.g. "no seasonality supplied" for lean_season

    annual_revenue_inr: dict[int, Decimal] = Field(default_factory=dict)
    minimum_cash_balance_inr: Decimal | None = None
    minimum_cash_month: int | None = None
    negative_cash_months: list[int] = Field(default_factory=list)
    total_debt_service_inr: Decimal | None = None
    average_annual_dscr: Decimal | None = None
    first_post_moratorium_year_dscr: Decimal | None = None
    status: FinancialFeasibilityStatus | None = None


class FinancialAssessmentResult(BaseModel):
    """Phase 4 output. Deterministic, JSON-serializable."""

    model_config = ConfigDict(extra="forbid")

    status: FinancialFeasibilityStatus
    rung: FinanceLadderRung
    category: BusinessCategory
    horizon_months: int = 0

    # --- sub-calculations, echoed; None when the ladder stopped before them ---
    project_cost: ProjectCostResult | None = None
    working_capital: WorkingCapitalResult | None = None
    revenue: RevenueScheduleResult | None = None
    operating_costs: OperatingCostResult | None = None
    break_even: BreakEvenResult | None = None
    debt: DebtScheduleResult | None = None
    cash_flow: CashFlowResult | None = None
    dscr: DSCRResult | None = None

    capital_gap_inr: Decimal | None = None
    promoter_contribution_pct: Decimal | None = None
    promoter_contribution_note: str = (
        "An arithmetic ratio of stated promoter cash to total project cost. Whether it "
        "satisfies any scheme's promoter-margin requirement is NOT determined here — that "
        "is a retrieved rule (CLAUDE.md §14, §18)."
    )
    # Echoed straight from FinancialPlanInput.financing.declared_margin_requirement
    # (never derived) so finance/fit.py can populate FinancialFitInput's
    # required_promoter_margin_inr without ever inventing a scheme margin.
    declared_margin_requirement: FinancialInput | None = None

    stress_results: list[StressResult] = Field(default_factory=list)
    breaking_point: str = ""

    findings: list[FinanceFinding] = Field(default_factory=list)
    missing_core_drivers: list[str] = Field(default_factory=list)

    assumptions: AssumptionRegister = Field(default_factory=AssumptionRegister)

    caveats: list[str] = Field(default_factory=list)  # fixed, config-authored
    config: FinanceConfig = Field(default_factory=lambda: DEFAULT_FINANCE_CONFIG)
    warnings: list[str] = Field(default_factory=list)
