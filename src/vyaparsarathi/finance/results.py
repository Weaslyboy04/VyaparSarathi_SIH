"""Phase 4 calculation result models (CLAUDE.md §15).

Every result here is a pure, deterministic summary of one Phase 4 calculation
step. None carries its own `status` — these are sub-computations, not phase
outputs; the single `status` for a whole run lives on the top-level
`FinancialAssessmentResult` in `finance/assessment_models.py`. Each mirrors
the house shape used across `market/`: money as `Decimal`, a `notes` list
wherever a figure rests on a structural default rather than a stated one, and
full JSON round-trippability.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.finance import CostLineKind, MoratoriumTreatment

# --- project cost / working capital ------------------------------------------


class CostLineBreakdown(BaseModel):
    """One project-cost line after any asset-spend offset is applied."""

    model_config = ConfigDict(extra="forbid")

    label: str
    kind: CostLineKind
    stated_amount_inr: Decimal
    offset_applied_inr: Decimal = Decimal("0")
    net_amount_inr: Decimal


class ProjectCostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines: list[CostLineBreakdown] = Field(default_factory=list)
    capex_subtotal_inr: Decimal  # sum of net_amount_inr across lines
    contingency_pct: Decimal
    contingency_inr: Decimal  # on capex_subtotal only, never on working capital
    net_working_capital_inr: Decimal
    project_cost_inr: Decimal  # capex_subtotal + contingency + net_working_capital
    notes: list[str] = Field(default_factory=list)


class WorkingCapitalResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Days figures actually used in the calculation (0 when an opening
    # inventory value was supplied directly instead of via inventory_days, or
    # when nothing at all was supplied for that line).
    inventory_days: Decimal
    receivable_days: Decimal
    payable_days: Decimal
    opex_cushion_months: Decimal

    inventory_requirement_inr: Decimal
    receivables_inr: Decimal
    payables_inr: Decimal
    operating_reserve_inr: Decimal
    gross_working_capital_inr: Decimal
    net_working_capital_inr: Decimal  # gross - payables
    notes: list[str] = Field(default_factory=list)


# --- revenue --------------------------------------------------------------


class MonthlyRevenue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: int  # 1-based
    ramp_factor: Decimal
    seasonality_factor: Decimal
    revenue_inr: Decimal


class RevenueScheduleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_monthly_revenue_inr: Decimal
    ramp_months: int
    ramp_start_pct: Decimal
    seasonality_applied: bool
    months: list[MonthlyRevenue] = Field(default_factory=list)
    # 1-based project year -> total revenue that year.
    annual_revenue_inr: dict[int, Decimal] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


# --- operating costs / profitability ------------------------------------


class MonthlyOperatingCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: int
    revenue_inr: Decimal
    cogs_inr: Decimal
    variable_opex_inr: Decimal
    fixed_opex_inr: Decimal
    gross_profit_inr: Decimal
    contribution_inr: Decimal
    # A cash-basis operating surplus. Deliberately not named EBITDA/PAT/"net
    # profit" — no depreciation and no tax are modelled (see the flags below).
    operating_profit_inr: Decimal


class OperatingCostResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cogs_pct: Decimal
    gross_margin_pct: Decimal
    variable_opex_pct: Decimal
    fixed_opex_monthly_inr: Decimal
    months: list[MonthlyOperatingCost] = Field(default_factory=list)
    depreciation_modelled: bool = False
    tax_modelled: bool = False
    notes: list[str] = Field(default_factory=list)


class BreakEvenResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # None, with `undefined_reason` set, when the contribution margin is
    # zero or negative — never a huge or misleading number.
    contribution_margin_ratio: Decimal | None
    break_even_revenue_monthly_inr: Decimal | None
    break_even_revenue_incl_debt_monthly_inr: Decimal | None
    break_even_units_monthly: Decimal | None  # only meaningful in the unit-price model
    operating_break_even_month: int | None
    cash_break_even_month: int | None  # filled in once cash flow is known (4B)
    undefined_reason: str = ""


# --- debt / amortisation / moratorium ------------------------------------


class AmortisationPeriod(BaseModel):
    """One EMI period, counted from 1 at the first post-moratorium payment."""

    model_config = ConfigDict(extra="forbid")

    period: int
    opening_balance_inr: Decimal
    interest_inr: Decimal
    principal_inr: Decimal
    payment_inr: Decimal
    closing_balance_inr: Decimal


class MoratoriumPeriod(BaseModel):
    """One month inside the moratorium window, before any EMI is paid."""

    model_config = ConfigDict(extra="forbid")

    period: int  # 1-based within the moratorium window
    opening_balance_inr: Decimal
    interest_accrued_inr: Decimal
    interest_paid_inr: Decimal = Decimal("0.00")  # > 0 only for INTEREST_SERVICED
    capitalised_inr: Decimal = Decimal("0.00")  # > 0 only for INTEREST_CAPITALISED
    closing_balance_inr: Decimal


class DebtScheduleResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_inr: Decimal
    annual_rate_pct: Decimal
    monthly_rate: Decimal
    rate_convention: str
    tenure_months: int
    moratorium_months: int
    moratorium_treatment: MoratoriumTreatment

    # True when EMI <= the first period's interest — the loan would never
    # amortise. `emi_inr` and the schedules are then empty/None; the caller
    # never loops looking for a balance that only grows.
    negative_amortisation: bool = False

    emi_inr: Decimal | None = None
    principal_at_emi_start_inr: Decimal
    capitalised_interest_inr: Decimal = Decimal("0.00")
    # A one-time outflow at the first EMI month, for INTEREST_ACCRUED_PAID_ON_EMI_START.
    accrued_interest_paid_at_emi_start_inr: Decimal = Decimal("0.00")

    moratorium_schedule: list[MoratoriumPeriod] = Field(default_factory=list)
    amortisation_schedule: list[AmortisationPeriod] = Field(default_factory=list)

    total_interest_inr: Decimal | None = None
    total_payment_inr: Decimal | None = None
    notes: list[str] = Field(default_factory=list)


# --- cash flow --------------------------------------------------------


class MonthlyCashFlow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: int  # 0-based; month 0 is disbursement + initial spend
    opening_cash_inr: Decimal
    operating_inflow_inr: Decimal
    operating_outflow_inr: Decimal
    debt_service_inr: Decimal
    # Month 0 only: loan disbursement + promoter/other infusion.
    financing_inflow_inr: Decimal = Decimal("0.00")
    # Month 0 only: capex + contingency + the opening inventory outlay.
    setup_outflow_inr: Decimal = Decimal("0.00")
    closing_cash_inr: Decimal


class CashFlowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    months: list[MonthlyCashFlow] = Field(default_factory=list)
    negative_cash_months: list[int] = Field(default_factory=list)
    minimum_cash_balance_inr: Decimal
    minimum_cash_month: int
    # Months until the first negative closing balance; `None` if it never goes negative.
    cash_runway_months: int | None
    # The most negative closing balance, expressed as a positive "additional
    # funds needed" figure; `0.00` if cash never goes negative.
    peak_cash_shortfall_inr: Decimal
    # CLAUDE.md §17's central question: cash on hand right when EMI starts.
    cash_at_emi_start_inr: Decimal | None
    emi_start_month: int | None
    notes: list[str] = Field(default_factory=list)


# --- DSCR ------------------------------------------------------------


class DSCRPeriod(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period: int  # 1-based year (annual window) or month index (monthly window)
    cads_inr: Decimal
    debt_service_inr: Decimal
    dscr: Decimal | None  # None when debt_service == 0 ("no debt service in this window")


class DSCRResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition: str = (
        "DSCR(window) = CADS(window) / debt_service(window), where CADS = revenue "
        "collected minus COGS paid minus operating expenses, BEFORE any debt service. "
        "Cash basis: no depreciation add-back and no tax deduction, because neither is "
        "modelled in this engine."
    )
    annual_dscr: list[DSCRPeriod] = Field(default_factory=list)
    monthly_dscr: list[DSCRPeriod] = Field(default_factory=list)
    project_period_dscr: Decimal | None = None
    # The headline pair.
    average_annual_dscr: Decimal | None = None  # mean over years with nonzero debt service
    first_post_moratorium_year_dscr: Decimal | None = None
    notes: list[str] = Field(default_factory=list)
