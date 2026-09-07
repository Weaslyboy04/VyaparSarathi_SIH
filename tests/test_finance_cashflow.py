"""Monthly cash flow (CLAUDE.md §15, §16, §17). Pure & offline."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.cashflow import compute_cash_flow
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.finance.money import q_money
from vyaparsarathi.finance.operations import compute_operating_costs, compute_revenue_schedule
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
# month chaining
# ======================================================================


def test_closing_balance_of_one_month_is_opening_of_the_next() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=6)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("200000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=None,
    )
    for prev, nxt in zip(cf.months, cf.months[1:], strict=False):
        assert prev.closing_cash_inr == nxt.opening_cash_inr


def test_month_zero_reflects_financing_and_setup_only() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=1)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("200000"),
        opening_inventory_inr=Decimal("50000"),
        financing_inflow_inr=Decimal("300000"),
        debt=None,
    )
    m0 = cf.months[0]
    assert m0.financing_inflow_inr == Decimal("300000.00")
    assert m0.setup_outflow_inr == Decimal("250000.00")
    assert m0.closing_cash_inr == Decimal("50000.00")


# ======================================================================
# negative-cash detection
# ======================================================================


def test_negative_cash_months_detected_when_setup_outspends_financing() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=3)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("500000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=None,
    )
    assert 0 in cf.negative_cash_months
    assert cf.minimum_cash_balance_inr < 0
    assert cf.cash_runway_months == 0
    assert cf.peak_cash_shortfall_inr == Decimal("200000.00")


def test_cash_never_negative_gives_none_runway_and_zero_shortfall() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=3)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("100000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=None,
    )
    assert cf.negative_cash_months == []
    assert cf.cash_runway_months is None
    assert cf.peak_cash_shortfall_inr == Decimal("0.00")


# ======================================================================
# debt service wiring and cash at EMI start
# ======================================================================


def test_debt_service_applied_after_moratorium_and_cash_at_emi_start_is_reported() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=6)
    debt = compute_debt_schedule(
        _loan(200_000, "12", 6, moratorium=2, treatment=MoratoriumTreatment.INTEREST_SERVICED)
    )
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("100000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=debt,
        moratorium_months=2,
    )
    # months 1-2: only serviced interest as debt service (no EMI yet)
    assert cf.months[1].debt_service_inr == debt.moratorium_schedule[0].interest_paid_inr
    assert cf.months[2].debt_service_inr == debt.moratorium_schedule[1].interest_paid_inr
    # month 3 onward: EMI
    assert cf.months[3].debt_service_inr == debt.emi_inr
    assert cf.emi_start_month == 3
    # cash at EMI start = closing balance at the end of month 2 (before month 3's EMI)
    assert cf.cash_at_emi_start_inr == cf.months[2].closing_cash_inr


def test_no_loan_means_no_debt_service_and_no_emi_start() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=3)
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("100000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=None,
    )
    assert all(m.debt_service_inr == Decimal("0.00") for m in cf.months)
    assert cf.emi_start_month is None
    assert cf.cash_at_emi_start_inr is None


def test_accrued_interest_paid_on_emi_start_lands_as_a_one_time_outflow() -> None:
    op = _operating(revenue=100_000, margin="0.40", fixed_opex=20_000, horizon=6)
    debt = compute_debt_schedule(
        _loan(
            200_000,
            "12",
            6,
            moratorium=2,
            treatment=MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
        )
    )
    cf = compute_cash_flow(
        op,
        capex_and_contingency_inr=Decimal("100000"),
        opening_inventory_inr=Decimal("0"),
        financing_inflow_inr=Decimal("300000"),
        debt=debt,
        moratorium_months=2,
    )
    # months 1-2 carry no debt service (interest accrues, unpaid)
    assert cf.months[1].debt_service_inr == Decimal("0.00")
    assert cf.months[2].debt_service_inr == Decimal("0.00")
    # month 3 (first EMI month) includes the accrued interest lump sum plus EMI
    expected = q_money(debt.accrued_interest_paid_at_emi_start_inr + debt.emi_inr)
    assert cf.months[3].debt_service_inr == expected


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
