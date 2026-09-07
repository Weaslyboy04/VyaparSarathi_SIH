"""Missing / inconsistent / edge-case financial inputs (CLAUDE.md §15).
Pure & offline. The engine never crashes and never fabricates a driver."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinanceLadderRung, FinancialFeasibilityStatus
from vyaparsarathi.finance.finance_config import FinanceConfig
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.ASSUMED, rationale="t", source="config:t"
    )


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _complete_plan(**overrides: object) -> FinancialPlanInput:
    base = {
        "category": C.GROCERY,
        "profile": EntrepreneurProfile(),
        "project_cost": ProjectCostInput(
            lines=[
                CostLine(
                    label="fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(100_000, Unit.INR),
                )
            ]
        ),
        "working_capital": WorkingCapitalInput(),
        "revenue": RevenueInput(monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH)),
        "operating_costs": OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(20_000, Unit.INR_PER_MONTH))],
        ),
        "financing": FinancingInput(promoter_cash_contribution=_provided(200_000, Unit.INR)),
        "horizon_months": 12,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


# ======================================================================
# each core driver missing individually
# ======================================================================


def test_missing_revenue_driver() -> None:
    res = assess_financials(_complete_plan(revenue=RevenueInput()))
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert any("revenue driver" in d for d in res.missing_core_drivers)


def test_missing_margin_driver() -> None:
    plan = _complete_plan(
        operating_costs=OperatingCostInput(
            fixed_lines=[OpexLine(label="rent", amount=_assumed(20_000, Unit.INR_PER_MONTH))]
        )
    )
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert any("margin driver" in d for d in res.missing_core_drivers)


def test_missing_project_cost_line() -> None:
    res = assess_financials(_complete_plan(project_cost=ProjectCostInput()))
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert any("project-cost line" in d for d in res.missing_core_drivers)


def test_missing_fixed_opex_lines() -> None:
    plan = _complete_plan(
        operating_costs=OperatingCostInput(gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO))
    )
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert any("fixed operating-expense" in d for d in res.missing_core_drivers)


def test_all_core_drivers_missing_at_once() -> None:
    plan = _complete_plan(
        revenue=RevenueInput(),
        operating_costs=OperatingCostInput(),
        project_cost=ProjectCostInput(),
    )
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert len(res.missing_core_drivers) == 4


def test_zero_fixed_opex_stated_as_a_rs_zero_line_is_not_missing() -> None:
    plan = _complete_plan(
        operating_costs=OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="none", amount=_assumed(0, Unit.INR_PER_MONTH))],
        )
    )
    res = assess_financials(plan)
    assert res.status is not FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE


# ======================================================================
# over-assumed gate
# ======================================================================


def test_assumption_share_over_the_configured_maximum_is_insufficient_evidence() -> None:
    cfg = FinanceConfig(max_assumption_share=0.0)  # every ASSUMED input trips it
    res = assess_financials(_complete_plan(), cfg=cfg)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert res.rung is FinanceLadderRung.MISSING_CORE_DRIVER
    assert any(f.code == "over_assumed" for f in res.findings)


# ======================================================================
# inconsistent inputs
# ======================================================================


def test_both_revenue_drivers_at_once_is_rejected_at_the_model() -> None:
    with pytest.raises(ValueError):
        RevenueInput(
            monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH),
            unit_price=_assumed(Decimal("10"), Unit.INR_PER_UNIT),
            units_per_month=_assumed(1000, Unit.UNITS_PER_MONTH),
        )


def test_unit_price_without_units_per_month_is_rejected_at_the_model() -> None:
    with pytest.raises(ValueError):
        RevenueInput(unit_price=_assumed(Decimal("10"), Unit.INR_PER_UNIT))


def test_both_margin_drivers_at_once_is_rejected_at_the_model() -> None:
    with pytest.raises(ValueError):
        OperatingCostInput(
            cogs_pct=_assumed(Decimal("0.6"), Unit.RATIO),
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
        )


# ======================================================================
# zero / negative edge cases
# ======================================================================


def test_zero_project_cost_is_not_a_financing_gap() -> None:
    # a Rs 0 project (degenerate but not malformed) should not crash. Zero the
    # working-capital cushion too, or the default reserve alone would make the
    # project cost nonzero.
    plan = _complete_plan(
        project_cost=ProjectCostInput(
            lines=[
                CostLine(
                    label="fit-out", kind=CostLineKind.CIVIL_WORK, amount=_assumed(0, Unit.INR)
                )
            ]
        ),
        working_capital=WorkingCapitalInput(opex_cushion_months=_assumed(0, Unit.MONTHS)),
        financing=FinancingInput(promoter_cash_contribution=_provided(0, Unit.INR)),
    )
    res = assess_financials(plan)
    assert res.project_cost is not None and res.project_cost.project_cost_inr == Decimal("0.00")
    assert res.capital_gap_inr == Decimal("0.00")
    assert res.promoter_contribution_pct is None  # ratio undefined at zero project cost


def test_negative_amount_is_rejected_at_the_financial_input_boundary() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        _assumed(-100_000, Unit.INR)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
