"""The shared core calculation pipeline (CLAUDE.md §15).

``run_core_pipeline`` runs every sub-calculation for one `FinancialPlanInput`,
in this exact order, and ``decide_status`` applies the feasibility ladder's
rungs 2-4 to the result. Both `finance/assessment.py` (the base case) and
`finance/stress.py` (each scenario) call these two functions — factored out
here so a stress scenario can never diverge from the base pipeline's own
logic, and so `assessment.py` can depend on `stress.py` for rung 5 without a
circular import (this module depends on neither).

This module assumes the caller has already confirmed the plan's core drivers
are present — missing-evidence handling is `assessment.py`'s job, not this
one's; the lower-level functions this module calls raise `ValueError` (a
caller mistake) if a required driver is absent.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vyaparsarathi.finance.assessment_models import (
    FinanceEvidenceRef,
    FinanceFinding,
    FinanceLadderRung,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.finance.cashflow import compute_cash_flow
from vyaparsarathi.finance.costs import (
    compute_capital_gap,
    compute_project_cost,
    compute_promoter_contribution_pct,
    compute_working_capital,
)
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.finance.dscr import compute_dscr
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG, FinanceConfig
from vyaparsarathi.finance.money import q_money, rupees
from vyaparsarathi.finance.operations import (
    compute_break_even,
    compute_operating_costs,
    compute_revenue_schedule,
)
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
from vyaparsarathi.models.finance import FinancialInput, FinancialPlanInput

_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class CoreResults:
    revenue_schedule: RevenueScheduleResult
    operating: OperatingCostResult
    working_capital: WorkingCapitalResult
    project_cost: ProjectCostResult
    debt: DebtScheduleResult | None
    cash_flow: CashFlowResult
    break_even: BreakEvenResult
    dscr: DSCRResult
    capital_gap_inr: Decimal
    promoter_contribution_pct: Decimal | None
    financing_notes: list[str]
    moratorium_months: int


def _resolve_amount(fi: FinancialInput | None, notes: list[str], label: str) -> Decimal:
    if fi is None:
        notes.append(f"no {label} was stated; treated as Rs 0")
        return _ZERO
    return q_money(rupees(fi.value))


def run_core_pipeline(
    plan: FinancialPlanInput, cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG
) -> CoreResults:
    revenue_schedule = compute_revenue_schedule(plan.revenue, plan.horizon_months)
    operating = compute_operating_costs(revenue_schedule, plan.operating_costs)

    # Working capital is sized off the steady-state (fully-ramped) monthly
    # revenue/COGS, not month 1's ramped-down figure — a rational reserve is
    # sized for the business the plan describes, not its slow start.
    base_cogs = q_money(revenue_schedule.base_monthly_revenue_inr * operating.cogs_pct)
    working_capital_result = compute_working_capital(
        plan.working_capital,
        monthly_revenue_inr=revenue_schedule.base_monthly_revenue_inr,
        monthly_cogs_inr=base_cogs,
        monthly_fixed_opex_inr=operating.fixed_opex_monthly_inr,
        cfg=cfg,
    )
    project_cost_result = compute_project_cost(plan.project_cost, working_capital_result, cfg=cfg)

    notes: list[str] = []
    promoter_cash = _resolve_amount(
        plan.financing.promoter_cash_contribution, notes, "promoter cash contribution"
    )
    other_funds = _resolve_amount(
        plan.financing.other_committed_funds, notes, "other committed funds"
    )
    loan = plan.financing.loan
    loan_principal = q_money(rupees(loan.principal_requested.value)) if loan is not None else _ZERO

    capital_gap = compute_capital_gap(
        project_cost_result.project_cost_inr,
        promoter_cash_inr=promoter_cash,
        other_committed_funds_inr=other_funds,
        loan_principal_inr=loan_principal,
    )
    promoter_pct = compute_promoter_contribution_pct(
        project_cost_result.project_cost_inr, promoter_cash
    )
    if (
        promoter_cash > 0
        and plan.profile.liquid_cash_inr is not None
        and promoter_cash > Decimal(plan.profile.liquid_cash_inr)
    ):
        notes.append(
            f"stated promoter cash contribution (Rs {promoter_cash}) exceeds the "
            f"entrepreneur's stated liquid cash (Rs {plan.profile.liquid_cash_inr})"
        )

    debt_result = compute_debt_schedule(loan) if loan is not None else None
    moratorium_months = int(loan.moratorium_months.value) if loan is not None else 0

    financing_inflow = q_money(promoter_cash + other_funds + loan_principal)
    capex_and_contingency = q_money(
        project_cost_result.capex_subtotal_inr + project_cost_result.contingency_inr
    )
    cash_flow_result = compute_cash_flow(
        operating,
        capex_and_contingency_inr=capex_and_contingency,
        opening_inventory_inr=working_capital_result.inventory_requirement_inr,
        financing_inflow_inr=financing_inflow,
        debt=debt_result,
        moratorium_months=moratorium_months,
    )

    unit_price = (
        rupees(plan.revenue.unit_price.value) if plan.revenue.unit_price is not None else None
    )
    break_even_result = compute_break_even(
        operating,
        monthly_debt_service_inr=debt_result.emi_inr if debt_result is not None else None,
        unit_price_inr=unit_price,
    )
    cash_break_even_month = next(
        (m.month for m in cash_flow_result.months if m.closing_cash_inr >= 0), None
    )
    break_even_result = break_even_result.model_copy(
        update={"cash_break_even_month": cash_break_even_month}
    )

    dscr_result = compute_dscr(operating, cash_flow_result, moratorium_months=moratorium_months)

    return CoreResults(
        revenue_schedule=revenue_schedule,
        operating=operating,
        working_capital=working_capital_result,
        project_cost=project_cost_result,
        debt=debt_result,
        cash_flow=cash_flow_result,
        break_even=break_even_result,
        dscr=dscr_result,
        capital_gap_inr=capital_gap,
        promoter_contribution_pct=promoter_pct,
        financing_notes=notes,
        moratorium_months=moratorium_months,
    )


def decide_status(
    core: CoreResults, cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG
) -> tuple[FinancialFeasibilityStatus, FinanceLadderRung, list[FinanceFinding]]:
    """Rungs 2-4 of the feasibility ladder — rung 1 (missing core driver) is
    checked against the *plan* by `assessment.py` before this ever runs; rung
    5 (stress-sensitivity) is applied by `assessment.py` after this, using
    `finance/stress.py`, only when this returns `FEASIBLE`."""
    if core.capital_gap_inr > 0:
        funded = core.project_cost.project_cost_inr - core.capital_gap_inr
        finding = FinanceFinding(
            code="funding_gap",
            message=(
                f"the plan needs Rs {core.project_cost.project_cost_inr} but only "
                f"Rs {funded} is funded; a gap of Rs {core.capital_gap_inr} remains"
            ),
            evidence=[
                FinanceEvidenceRef(
                    source="cost",
                    field="project_cost.project_cost_inr",
                    value=float(core.project_cost.project_cost_inr),
                    compared_to=float(funded),
                )
            ],
        )
        return FinancialFeasibilityStatus.FINANCING_GAP, FinanceLadderRung.FUNDING_GAP, [finding]

    horizon_closing = core.cash_flow.months[-1].closing_cash_inr
    avg_dscr = core.dscr.average_annual_dscr
    negative_amortisation = core.debt is not None and core.debt.negative_amortisation
    dscr_unserviceable = avg_dscr is not None and avg_dscr < cfg.dscr_unserviceable
    if negative_amortisation or dscr_unserviceable or horizon_closing < 0:
        if negative_amortisation:
            code = "negative_amortisation"
            message = (
                "the loan's EMI does not exceed the first period's interest; this loan "
                "would never amortise"
            )
        elif dscr_unserviceable:
            code = "average_dscr_unserviceable"
            message = (
                f"average annual DSCR ({avg_dscr}) is below the unserviceable threshold "
                f"({cfg.dscr_unserviceable})"
            )
        else:
            code = "cash_negative_at_horizon"
            message = (
                f"closing cash at the end of the horizon is Rs {horizon_closing}, still negative"
            )
        finding = FinanceFinding(code=code, message=message)
        return (
            FinancialFeasibilityStatus.UNSERVICEABLE,
            FinanceLadderRung.NOT_SERVICEABLE,
            [finding],
        )

    first_post = core.dscr.first_post_moratorium_year_dscr
    below_floor = core.cash_flow.minimum_cash_balance_inr < cfg.min_cash_floor_inr
    weak_post_moratorium = first_post is not None and first_post < cfg.dscr_unserviceable
    if core.cash_flow.negative_cash_months or below_floor or weak_post_moratorium:
        if core.cash_flow.negative_cash_months:
            code = "negative_cash_month"
            message = f"cash goes negative in month(s) {core.cash_flow.negative_cash_months}"
        elif below_floor:
            code = "cash_below_floor"
            message = (
                f"minimum cash balance (Rs {core.cash_flow.minimum_cash_balance_inr}) is below "
                f"the configured floor (Rs {cfg.min_cash_floor_inr})"
            )
        else:
            code = "post_moratorium_dscr_weak"
            message = (
                f"the first post-moratorium year's DSCR ({first_post}) is below "
                f"{cfg.dscr_unserviceable}"
            )
        finding = FinanceFinding(code=code, message=message)
        return FinancialFeasibilityStatus.CASH_FLOW_STRESS, FinanceLadderRung.CASH_STRESS, [finding]

    return FinancialFeasibilityStatus.FEASIBLE, FinanceLadderRung.CLEARS_ALL, []
