"""DSCR — the exact definition used here (CLAUDE.md §15, §17). Pure & offline.

Golden case (hand-calculated): CADS=360,000, debt_service=240,000 -> DSCR=1.50 exactly.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.cashflow import compute_cash_flow
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.finance.dscr import compute_dscr
from vyaparsarathi.finance.operations import compute_operating_costs, compute_revenue_schedule
from vyaparsarathi.finance.results import (
    CashFlowResult,
    MonthlyCashFlow,
    MonthlyOperatingCost,
    OperatingCostResult,
)
from vyaparsarathi.models.finance import (
    FinancialInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    RevenueInput,
    Unit,
)


def _fi(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _operating(revenue: int, margin: str, fixed_opex: int, horizon: int):
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_fi(revenue, Unit.INR_PER_MONTH)), horizon
    )
    return compute_operating_costs(
        schedule,
        OperatingCostInput(
            gross_margin_pct=_fi(Decimal(margin), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_fi(fixed_opex, Unit.INR_PER_MONTH))],
        ),
    )


def _loan(
    principal: int, rate: str, tenure: int, moratorium: int = 0, treatment=MoratoriumTreatment.NONE
):
    return LoanTerms(
        principal_requested=_fi(principal, Unit.INR),
        interest_rate_pct=_fi(Decimal(rate), Unit.PERCENT_PER_ANNUM),
        tenure_months=_fi(tenure, Unit.MONTHS),
        moratorium_months=_fi(moratorium, Unit.MONTHS),
        moratorium_treatment=treatment,
    )


# ======================================================================
# golden case — CADS=360,000, debt_service=240,000 -> DSCR=1.50 exactly
# ======================================================================


def _stub_operating(month: int, operating_profit: Decimal) -> OperatingCostResult:
    return OperatingCostResult(
        cogs_pct=Decimal("0.6000"),
        gross_margin_pct=Decimal("0.4000"),
        variable_opex_pct=Decimal("0.0000"),
        fixed_opex_monthly_inr=Decimal("0.00"),
        months=[
            MonthlyOperatingCost(
                month=month,
                revenue_inr=Decimal("0.00"),
                cogs_inr=Decimal("0.00"),
                variable_opex_inr=Decimal("0.00"),
                fixed_opex_inr=Decimal("0.00"),
                gross_profit_inr=Decimal("0.00"),
                contribution_inr=Decimal("0.00"),
                operating_profit_inr=operating_profit,
            )
        ],
    )


def _stub_cash_flow(month: int, debt_service: Decimal) -> CashFlowResult:
    m = MonthlyCashFlow(
        month=month,
        opening_cash_inr=Decimal("0.00"),
        operating_inflow_inr=Decimal("0.00"),
        operating_outflow_inr=Decimal("0.00"),
        debt_service_inr=debt_service,
        closing_cash_inr=Decimal("0.00"),
    )
    return CashFlowResult(
        months=[m],
        negative_cash_months=[],
        minimum_cash_balance_inr=Decimal("0.00"),
        minimum_cash_month=month,
        cash_runway_months=None,
        peak_cash_shortfall_inr=Decimal("0.00"),
        cash_at_emi_start_inr=None,
        emi_start_month=None,
    )


def test_golden_dscr_is_exactly_one_point_five() -> None:
    op = _stub_operating(month=1, operating_profit=Decimal("360000.00"))
    cf = _stub_cash_flow(month=1, debt_service=Decimal("240000.00"))
    dscr = compute_dscr(op, cf)
    assert dscr.monthly_dscr[0].dscr == Decimal("1.5000")
    assert dscr.project_period_dscr == Decimal("1.5000")


# ======================================================================
# the three windows
# ======================================================================


def test_monthly_and_annual_windows_are_both_populated() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=13)
    debt = compute_debt_schedule(_loan(200_000, "12", 12))
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("200000"),
        debt=debt,
    )
    dscr = compute_dscr(op, cf)
    assert len(dscr.monthly_dscr) == 13
    assert len(dscr.annual_dscr) == 2  # months 1-12, then month 13
    assert dscr.project_period_dscr is not None


def test_no_debt_service_in_a_window_gives_none_never_zero_or_infinity() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=3)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("200000"),
        debt=None,
    )
    dscr = compute_dscr(op, cf)
    assert dscr.project_period_dscr is None
    assert dscr.average_annual_dscr is None
    assert all(p.dscr is None for p in dscr.monthly_dscr)


def test_moratorium_only_year_yields_none_not_zero() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=12)
    debt = compute_debt_schedule(
        _loan(200_000, "12", 6, moratorium=12, treatment=MoratoriumTreatment.INTEREST_CAPITALISED)
    )
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("200000"),
        debt=debt,
        moratorium_months=12,
    )
    dscr = compute_dscr(op, cf, moratorium_months=12)
    # year 1 is entirely inside the moratorium (capitalised -> no cash debt service)
    assert dscr.annual_dscr[0].dscr is None
    assert dscr.annual_dscr[0].debt_service_inr == Decimal("0.00")


def test_first_post_moratorium_year_dscr_is_reported() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=24)
    debt = compute_debt_schedule(
        _loan(200_000, "12", 12, moratorium=6, treatment=MoratoriumTreatment.INTEREST_SERVICED)
    )
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("200000"),
        debt=debt,
        moratorium_months=6,
    )
    dscr = compute_dscr(op, cf, moratorium_months=6)
    # month 7 is the first EMI month -> falls in year 1
    assert dscr.first_post_moratorium_year_dscr == dscr.annual_dscr[0].dscr


def test_average_annual_dscr_only_averages_years_with_debt_service() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=24)
    debt = compute_debt_schedule(
        _loan(200_000, "12", 12, moratorium=12, treatment=MoratoriumTreatment.INTEREST_CAPITALISED)
    )
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("200000"),
        debt=debt,
        moratorium_months=12,
    )
    dscr = compute_dscr(op, cf, moratorium_months=12)
    # year 1 has no debt service (fully in moratorium); year 2 has the full EMI schedule.
    assert dscr.annual_dscr[0].dscr is None
    assert dscr.annual_dscr[1].dscr is not None
    assert dscr.average_annual_dscr == dscr.annual_dscr[1].dscr


def test_definition_string_is_present_on_the_result() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=1)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("0"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("0"),
        debt=None,
    )
    dscr = compute_dscr(op, cf)
    assert "no depreciation" in dscr.definition
    assert "no tax" in dscr.definition


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
