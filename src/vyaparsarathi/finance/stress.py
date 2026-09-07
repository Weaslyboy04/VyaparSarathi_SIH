"""Stress testing (CLAUDE.md §16).

Each scenario is a pure transform of `FinancialPlanInput`, followed by a full
re-run of `finance/pipeline.py::run_core_pipeline` and `decide_status` — a
scenario can never diverge from the base pipeline's own logic. Every
transform returns a *new* plan (pydantic's `model_copy`); the caller's plan is
never mutated.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus, StressResult
from vyaparsarathi.finance.finance_config import (
    DEFAULT_FINANCE_CONFIG,
    FinanceConfig,
    StressScenario,
)
from vyaparsarathi.finance.money import q_money, rupees
from vyaparsarathi.finance.pipeline import decide_status, run_core_pipeline
from vyaparsarathi.models.finance import (
    FinancialInput,
    FinancialPlanInput,
    InputKind,
    OpexLine,
    Unit,
)

_ZERO = Decimal("0.00")

# Statuses in worsening order — used to detect whether a stress scenario made
# things worse than the base case (`INSUFFICIENT_FINANCIAL_EVIDENCE` and
# `FINANCING_GAP` never appear here: stress scenarios only run once a plan has
# already cleared rungs 1-4, i.e. the base case is `FEASIBLE`).
_SEVERITY_ORDER = (
    FinancialFeasibilityStatus.FEASIBLE,
    FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH,
    FinancialFeasibilityStatus.CASH_FLOW_STRESS,
    FinancialFeasibilityStatus.UNSERVICEABLE,
)


def _scale_money(fi: FinancialInput, factor: Decimal) -> FinancialInput:
    return FinancialInput(
        label=fi.label,
        value=q_money(rupees(fi.value) * factor),
        unit=fi.unit,
        kind=InputKind.CALCULATED,
        calculated_from=(fi.label,),
    )


def _apply_revenue_multiplier(plan: FinancialPlanInput, factor: Decimal) -> FinancialPlanInput:
    if factor == 1:
        return plan
    revenue = plan.revenue
    updates: dict[str, FinancialInput] = {}
    if revenue.monthly_revenue is not None:
        updates["monthly_revenue"] = _scale_money(revenue.monthly_revenue, factor)
    if revenue.unit_price is not None:
        updates["unit_price"] = _scale_money(revenue.unit_price, factor)
    return plan.model_copy(update={"revenue": revenue.model_copy(update=updates)})


def _apply_margin_delta(plan: FinancialPlanInput, delta_pct: Decimal) -> FinancialPlanInput:
    if delta_pct == 0:
        return plan
    oc = plan.operating_costs
    updates: dict[str, FinancialInput] = {}
    if oc.gross_margin_pct is not None:
        new_val = max(Decimal("0"), rupees(oc.gross_margin_pct.value) - delta_pct)
        updates["gross_margin_pct"] = FinancialInput(
            label=oc.gross_margin_pct.label,
            value=new_val,
            unit=Unit.RATIO,
            kind=InputKind.CALCULATED,
            calculated_from=(oc.gross_margin_pct.label,),
        )
    elif oc.cogs_pct is not None:
        new_val = min(Decimal("1"), rupees(oc.cogs_pct.value) + delta_pct)
        updates["cogs_pct"] = FinancialInput(
            label=oc.cogs_pct.label,
            value=new_val,
            unit=Unit.RATIO,
            kind=InputKind.CALCULATED,
            calculated_from=(oc.cogs_pct.label,),
        )
    return plan.model_copy(update={"operating_costs": oc.model_copy(update=updates)})


def _apply_fixed_opex_multiplier(plan: FinancialPlanInput, factor: Decimal) -> FinancialPlanInput:
    if factor == 1:
        return plan
    oc = plan.operating_costs
    new_lines = [
        OpexLine(label=line.label, amount=_scale_money(line.amount, factor))
        for line in oc.fixed_lines
    ]
    return plan.model_copy(
        update={"operating_costs": oc.model_copy(update={"fixed_lines": new_lines})}
    )


def _apply_ramp_delta(plan: FinancialPlanInput, months_delta: int) -> FinancialPlanInput:
    if months_delta == 0:
        return plan
    revenue = plan.revenue
    current = int(revenue.ramp_months.value) if revenue.ramp_months is not None else 0
    label = revenue.ramp_months.label if revenue.ramp_months is not None else "ramp_months"
    new_field = FinancialInput(
        label=label,
        value=current + months_delta,
        unit=Unit.MONTHS,
        kind=InputKind.CALCULATED,
        calculated_from=(label,),
    )
    return plan.model_copy(
        update={"revenue": revenue.model_copy(update={"ramp_months": new_field})}
    )


def _apply_lean_season(plan: FinancialPlanInput) -> tuple[FinancialPlanInput | None, str]:
    """A sustained low-season stretch (CLAUDE.md §16), not the plan's normal
    cyclical pattern: the whole horizon is flattened to the *minimum* factor in
    the plan's own `seasonality_index`. Skipped — never fabricated — when the
    plan supplied no seasonality at all."""
    index = plan.revenue.seasonality_index
    if index is None:
        return None, "no seasonality was supplied for this plan"
    min_factor = min(index)
    scaled = _apply_revenue_multiplier(plan, min_factor)
    flattened = scaled.revenue.model_copy(update={"seasonality_index": None})
    return scaled.model_copy(update={"revenue": flattened}), ""


def _apply_scenario(
    plan: FinancialPlanInput, scenario: StressScenario
) -> tuple[FinancialPlanInput | None, str]:
    if scenario.apply_seasonality:
        return _apply_lean_season(plan)
    result = plan
    result = _apply_revenue_multiplier(result, scenario.revenue_multiplier)
    result = _apply_margin_delta(result, scenario.margin_delta_pct)
    result = _apply_fixed_opex_multiplier(result, scenario.fixed_opex_multiplier)
    result = _apply_ramp_delta(result, scenario.ramp_months_delta)
    return result, ""


def run_stress_scenarios(
    plan: FinancialPlanInput, *, cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG
) -> list[StressResult]:
    results: list[StressResult] = []
    for scenario in cfg.stress_scenarios:
        stressed_plan, skip_reason = _apply_scenario(plan, scenario)
        if stressed_plan is None:
            results.append(
                StressResult(
                    name=scenario.name,
                    description=scenario.description,
                    applied=False,
                    skipped_reason=skip_reason,
                )
            )
            continue

        core = run_core_pipeline(stressed_plan, cfg=cfg)
        status, _rung, _findings = decide_status(core, cfg=cfg)
        total_debt_service = sum((m.debt_service_inr for m in core.cash_flow.months), _ZERO)
        results.append(
            StressResult(
                name=scenario.name,
                description=scenario.description,
                applied=True,
                annual_revenue_inr=core.revenue_schedule.annual_revenue_inr,
                minimum_cash_balance_inr=core.cash_flow.minimum_cash_balance_inr,
                minimum_cash_month=core.cash_flow.minimum_cash_month,
                negative_cash_months=core.cash_flow.negative_cash_months,
                total_debt_service_inr=q_money(total_debt_service),
                average_annual_dscr=core.dscr.average_annual_dscr,
                first_post_moratorium_year_dscr=core.dscr.first_post_moratorium_year_dscr,
                status=status,
            )
        )
    return results


def find_breaking_point(
    base_status: FinancialFeasibilityStatus, stress_results: list[StressResult]
) -> str:
    """The first scenario, in config order, that worsens the base status —
    named in one sentence (CLAUDE.md §16). Empty when nothing does."""
    base_rank = _SEVERITY_ORDER.index(base_status) if base_status in _SEVERITY_ORDER else -1
    for result in stress_results:
        if not result.applied or result.status is None:
            continue
        rank = _SEVERITY_ORDER.index(result.status) if result.status in _SEVERITY_ORDER else -1
        if rank > base_rank:
            return (
                f"'{result.name}' ({result.description}) pushes the plan to {result.status.value}."
            )
    return ""
