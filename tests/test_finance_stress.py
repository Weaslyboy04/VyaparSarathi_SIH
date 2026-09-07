"""Stress testing (CLAUDE.md §16). Pure & offline."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus, StressResult
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG
from vyaparsarathi.finance.stress import find_breaking_point, run_stress_scenarios
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


def _plan(
    seasonality: tuple[Decimal, ...] | None = None, **overrides: object
) -> FinancialPlanInput:
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
        "revenue": RevenueInput(
            monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH), seasonality_index=seasonality
        ),
        "operating_costs": OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(20_000, Unit.INR_PER_MONTH))],
        ),
        "financing": FinancingInput(promoter_cash_contribution=_provided(300_000, Unit.INR)),
        "horizon_months": 12,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


# ======================================================================
# each scenario runs and returns a status
# ======================================================================


def test_every_configured_scenario_runs_except_lean_season_without_seasonality() -> None:
    plan = _plan()
    results = run_stress_scenarios(plan)
    assert len(results) == len(DEFAULT_FINANCE_CONFIG.stress_scenarios)
    by_name = {r.name: r for r in results}
    assert by_name["lean_season"].applied is False
    assert "no seasonality" in by_name["lean_season"].skipped_reason
    for name in (
        "revenue_down_20",
        "revenue_down_30",
        "margin_down_300bps",
        "opex_up_15",
        "ramp_slower_2m",
        "combined_downside",
    ):
        assert by_name[name].applied is True
        assert by_name[name].status is not None


def test_revenue_down_scenarios_reduce_annual_revenue() -> None:
    plan = _plan()
    results = {r.name: r for r in run_stress_scenarios(plan)}
    base_annual = Decimal("1200000.00")  # 100,000 * 12
    assert results["revenue_down_20"].annual_revenue_inr[1] == base_annual * Decimal("0.80")
    assert results["revenue_down_30"].annual_revenue_inr[1] == base_annual * Decimal("0.70")


# ======================================================================
# lean season
# ======================================================================


def test_lean_season_is_skipped_with_an_explicit_note_when_not_supplied() -> None:
    results = {r.name: r for r in run_stress_scenarios(_plan(seasonality=None))}
    lean = results["lean_season"]
    assert lean.applied is False
    assert lean.status is None
    assert lean.skipped_reason != ""


def test_lean_season_flattens_revenue_to_the_minimum_seasonal_factor() -> None:
    # one lean month at 0.5, the remaining 11 months share the other 11.5 so
    # the whole index still averages exactly 1.0.
    index = tuple(Decimal("0.5") if i == 0 else Decimal("11.5") / 11 for i in range(12))
    plan = _plan(seasonality=index, horizon_months=1)
    results = {r.name: r for r in run_stress_scenarios(plan)}
    lean = results["lean_season"]
    assert lean.applied is True
    assert lean.annual_revenue_inr[1] == Decimal("100000.00") * Decimal("0.5")


# ======================================================================
# combined downside
# ======================================================================


def test_combined_downside_applies_all_three_deltas_together() -> None:
    plan = _plan()
    results = {r.name: r for r in run_stress_scenarios(plan)}
    combined = results["combined_downside"]
    # revenue x0.75, opex x1.10: worse cash position than any single lever alone
    assert combined.minimum_cash_balance_inr is not None
    single_revenue = results["revenue_down_30"].minimum_cash_balance_inr
    assert combined.minimum_cash_balance_inr < single_revenue


# ======================================================================
# base input is never mutated
# ======================================================================


def test_running_stress_scenarios_never_mutates_the_original_plan() -> None:
    plan = _plan()
    before = plan.model_dump(mode="json")
    run_stress_scenarios(plan)
    after = plan.model_dump(mode="json")
    assert before == after


# ======================================================================
# breaking point
# ======================================================================


def test_breaking_point_names_the_first_scenario_that_worsens_the_status() -> None:
    results = [
        StressResult(
            name="revenue_down_20",
            description="d",
            applied=True,
            status=FinancialFeasibilityStatus.FEASIBLE,
        ),
        StressResult(
            name="revenue_down_30",
            description="d",
            applied=True,
            status=FinancialFeasibilityStatus.CASH_FLOW_STRESS,
        ),
        StressResult(
            name="opex_up_15",
            description="d",
            applied=True,
            status=FinancialFeasibilityStatus.UNSERVICEABLE,
        ),
    ]
    point = find_breaking_point(FinancialFeasibilityStatus.FEASIBLE, results)
    assert "revenue_down_30" in point
    assert "cash_flow_stress" in point


def test_breaking_point_is_empty_when_nothing_worsens() -> None:
    results = [
        StressResult(
            name="revenue_down_20",
            description="d",
            applied=True,
            status=FinancialFeasibilityStatus.FEASIBLE,
        ),
        StressResult(
            name="lean_season", description="d", applied=False, skipped_reason="no seasonality"
        ),
    ]
    assert find_breaking_point(FinancialFeasibilityStatus.FEASIBLE, results) == ""


def test_breaking_point_skips_unapplied_scenarios() -> None:
    results = [
        StressResult(
            name="lean_season", description="d", applied=False, skipped_reason="no seasonality"
        ),
    ]
    assert find_breaking_point(FinancialFeasibilityStatus.FEASIBLE, results) == ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
