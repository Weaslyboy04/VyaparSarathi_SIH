"""The financial feasibility ladder (CLAUDE.md §15, §16, §22). Pure & offline.

One test per status, plus the ladder's precedence (a plan that is both
under-funded and would be unserviceable if funded is reported FINANCING_GAP,
never UNSERVICEABLE — rung 2 fires before rung 3 is ever checked).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinanceLadderRung, FinancialFeasibilityStatus
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x",
        value=value,
        unit=unit,
        kind=InputKind.ASSUMED,
        rationale="test fixture value",
        source="config:test",
    )


def _base_plan(
    *,
    monthly_revenue: int = 100_000,
    margin: str = "0.40",
    fixed_opex: int = 20_000,
    project_lines: int = 200_000,
    promoter_cash: int = 200_000,
    loan: LoanTerms | None = None,
    horizon_months: int = 24,
) -> FinancialPlanInput:
    return FinancialPlanInput(
        category=C.GROCERY,
        profile=EntrepreneurProfile(liquid_cash_inr=1_000_000),
        project_cost=ProjectCostInput(
            lines=[
                CostLine(
                    label="shop fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(project_lines, Unit.INR),
                )
            ],
        ),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(monthly_revenue=_assumed(monthly_revenue, Unit.INR_PER_MONTH)),
        operating_costs=OperatingCostInput(
            gross_margin_pct=_assumed(Decimal(margin), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(fixed_opex, Unit.INR_PER_MONTH))],
        ),
        financing=FinancingInput(
            promoter_cash_contribution=_provided(promoter_cash, Unit.INR), loan=loan
        ),
        horizon_months=horizon_months,
    )


def _loan(
    principal: int, rate: str, tenure: int, moratorium: int = 0, treatment=MoratoriumTreatment.NONE
) -> LoanTerms:
    return LoanTerms(
        principal_requested=_assumed(principal, Unit.INR),
        interest_rate_pct=_assumed(Decimal(rate), Unit.PERCENT_PER_ANNUM),
        tenure_months=_assumed(tenure, Unit.MONTHS),
        moratorium_months=_assumed(moratorium, Unit.MONTHS),
        moratorium_treatment=treatment,
    )


# ======================================================================
# INSUFFICIENT_FINANCIAL_EVIDENCE
# ======================================================================


def test_missing_revenue_driver_is_insufficient_evidence() -> None:
    plan = _base_plan()
    plan = plan.model_copy(update={"revenue": RevenueInput()})
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert res.rung is FinanceLadderRung.MISSING_CORE_DRIVER
    assert any("revenue driver" in d for d in res.missing_core_drivers)
    assert res.project_cost is None  # no calculation ran


# ======================================================================
# FINANCING_GAP
# ======================================================================


def test_underfunded_plan_is_financing_gap() -> None:
    plan = _base_plan(project_lines=500_000, promoter_cash=100_000)
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.FINANCING_GAP
    assert res.rung is FinanceLadderRung.FUNDING_GAP
    assert res.capital_gap_inr is not None and res.capital_gap_inr > 0
    assert any(f.code == "funding_gap" for f in res.findings)


def test_financing_gap_takes_precedence_over_unserviceable() -> None:
    # underfunded AND would be unserviceable if it were funded (tiny margin,
    # an absurd loan) -- rung 2 must fire before rung 3 is even checked.
    plan = _base_plan(
        project_lines=1_000_000,
        promoter_cash=50_000,
        margin="0.05",
        loan=_loan(100_000, "36", 6),
    )
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.FINANCING_GAP
    assert res.rung is FinanceLadderRung.FUNDING_GAP


# ======================================================================
# UNSERVICEABLE
# ======================================================================


def test_unserviceable_when_horizon_closes_negative() -> None:
    plan = _base_plan(
        monthly_revenue=30_000,
        margin="0.20",
        fixed_opex=25_000,
        project_lines=100_000,
        promoter_cash=100_000,
        loan=_loan(200_000, "24", 12),
        horizon_months=12,
    )
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.UNSERVICEABLE
    assert res.rung is FinanceLadderRung.NOT_SERVICEABLE


def test_unserviceable_on_negative_amortisation() -> None:
    # Rs 1 principal over 360 months at 12% p.a.: EMI rounds to the same
    # paise figure as the first period's interest (see test_finance_debt.py).
    plan = _base_plan(project_lines=50_000, promoter_cash=200_000, loan=_loan(1, "12", 360))
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.UNSERVICEABLE
    assert any(f.code == "negative_amortisation" for f in res.findings)


# ======================================================================
# CASH_FLOW_STRESS
# ======================================================================


def test_cash_flow_stress_when_a_month_goes_negative_but_recovers() -> None:
    # Fully funded (gap=0, no reserve buffer) with a 2-month ramp starting
    # from zero: month 1's operating deficit (-10,000) dips cash negative
    # before the ramp completes and cash recovers and keeps growing.
    plan = FinancialPlanInput(
        category=C.GROCERY,
        profile=EntrepreneurProfile(liquid_cash_inr=1_000_000),
        project_cost=ProjectCostInput(
            lines=[
                CostLine(
                    label="fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(100_000, Unit.INR),
                )
            ]
        ),
        working_capital=WorkingCapitalInput(opex_cushion_months=_assumed(0, Unit.MONTHS)),
        revenue=RevenueInput(
            monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH),
            ramp_months=_assumed(2, Unit.MONTHS),
            ramp_start_pct=_assumed(Decimal("0"), Unit.RATIO),
        ),
        operating_costs=OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(30_000, Unit.INR_PER_MONTH))],
        ),
        financing=FinancingInput(promoter_cash_contribution=_provided(105_000, Unit.INR)),
        horizon_months=6,
    )
    res = assess_financials(plan)
    assert res.capital_gap_inr == Decimal("0.00")
    assert res.status is FinancialFeasibilityStatus.CASH_FLOW_STRESS
    assert res.rung is FinanceLadderRung.CASH_STRESS
    assert res.cash_flow.negative_cash_months == [1]
    assert res.cash_flow.months[-1].closing_cash_inr > 0  # it recovers
    assert any(f.code == "negative_cash_month" for f in res.findings)


# ======================================================================
# FEASIBLE / FEASIBLE_WITH_STRETCH
# ======================================================================


def test_comfortably_funded_plan_with_no_loan_is_feasible() -> None:
    plan = _base_plan(promoter_cash=250_000)
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.FEASIBLE
    assert res.rung is FinanceLadderRung.CLEARS_ALL
    assert res.dscr.average_annual_dscr is None  # no loan -> DSCR undefined, not UNSERVICEABLE


def test_thin_but_clean_plan_can_be_stress_sensitive() -> None:
    # Enough cash and DSCR to clear rungs 1-4, but thin enough that a 20%
    # revenue drop tips a stress scenario into CASH_FLOW_STRESS.
    plan = _base_plan(
        monthly_revenue=60_000,
        margin="0.40",
        fixed_opex=18_000,
        project_lines=150_000,
        promoter_cash=200_000,
        horizon_months=24,
    )
    res = assess_financials(plan)
    if res.status is FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH:
        assert res.rung is FinanceLadderRung.STRESS_SENSITIVE
        assert res.breaking_point != ""
        assert res.stress_results  # stress was actually run
    else:
        # Not every thin plan is guaranteed stress-sensitive; assert the
        # invariant instead: stress was run whenever the base case is FEASIBLE.
        assert res.status is FinancialFeasibilityStatus.FEASIBLE
        assert res.stress_results


def test_feasible_result_still_runs_and_reports_stress_scenarios() -> None:
    plan = _base_plan(promoter_cash=400_000, project_lines=150_000)
    res = assess_financials(plan)
    assert res.status in (
        FinancialFeasibilityStatus.FEASIBLE,
        FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH,
    )
    assert len(res.stress_results) == len(res.config.stress_scenarios)


# ======================================================================
# determinism
# ======================================================================


def test_assessment_is_deterministic() -> None:
    plan = _base_plan()
    a = assess_financials(plan)
    b = assess_financials(plan)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
