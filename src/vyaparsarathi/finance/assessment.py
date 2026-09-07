"""The Phase 4 orchestrator: `FinancialPlanInput` -> `FinancialAssessmentResult`
(CLAUDE.md §15, §16, §22).

Rung 1 (missing core driver / over-assumed) is decided against the *plan*
directly, before any calculation runs — `finance/costs.py` / `operations.py` /
`debt.py` assume their inputs are already resolved and raise `ValueError` (a
caller mistake) otherwise; this module is what decides whether a plan is
complete enough to call them at all. Rungs 2-4 come from
`finance/pipeline.py::decide_status`; rung 5 (stress-sensitivity) comes from
`finance/stress.py`, applied only when rungs 1-4 all clear.
"""

from __future__ import annotations

from pydantic import BaseModel

from vyaparsarathi.finance.assessment_models import (
    FinanceFinding,
    FinanceLadderRung,
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
    StressResult,
)
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG, FinanceConfig
from vyaparsarathi.finance.pipeline import decide_status, run_core_pipeline
from vyaparsarathi.finance.stress import find_breaking_point, run_stress_scenarios
from vyaparsarathi.models.finance import (
    AssumptionRegister,
    FinancialInput,
    FinancialPlanInput,
    InputKind,
)


def _missing_core_drivers(plan: FinancialPlanInput) -> list[str]:
    """The core drivers named in CLAUDE.md §15: a revenue driver, a margin
    driver, at least one project-cost line, and fixed operating expenses.
    (A requested loan's own rate/tenure/moratorium fields cannot be partially
    missing — `LoanTerms` requires all of them at construction — so there is
    nothing further to check there.)"""
    missing: list[str] = []
    revenue = plan.revenue
    has_revenue = revenue.monthly_revenue is not None or (
        revenue.unit_price is not None and revenue.units_per_month is not None
    )
    if not has_revenue:
        missing.append("a revenue driver (monthly_revenue, or unit_price + units_per_month)")

    oc = plan.operating_costs
    if oc.cogs_pct is None and oc.gross_margin_pct is None:
        missing.append("a margin driver (cogs_pct or gross_margin_pct)")

    if not plan.project_cost.lines:
        missing.append("at least one project-cost line")

    if not oc.fixed_lines:
        missing.append(
            "fixed operating-expense lines (state a Rs 0 line if there genuinely are none)"
        )

    return missing


def _walk_financial_inputs(obj: object) -> list[FinancialInput]:
    """Every `FinancialInput` reachable from a pydantic model, recursively —
    the basis of the `AssumptionRegister`."""
    found: list[FinancialInput] = []
    if isinstance(obj, FinancialInput):
        found.append(obj)
    elif isinstance(obj, BaseModel):
        for name in type(obj).model_fields:
            found.extend(_walk_financial_inputs(getattr(obj, name)))
    elif isinstance(obj, list | tuple):
        for item in obj:
            found.extend(_walk_financial_inputs(item))
    return found


def _collect_assumptions(plan: FinancialPlanInput) -> AssumptionRegister:
    inputs = _walk_financial_inputs(plan)
    counts: dict[InputKind, int] = {}
    for fi in inputs:
        counts[fi.kind] = counts.get(fi.kind, 0) + 1
    total = len(inputs)
    provided_or_sourced = counts.get(InputKind.USER_PROVIDED, 0) + counts.get(InputKind.SOURCED, 0)
    share = round(counts.get(InputKind.ASSUMED, 0) / total, 3) if total else 0.0
    return AssumptionRegister(
        inputs=inputs,
        counts_by_kind=counts,
        core_drivers_total=total,
        core_drivers_provided_or_sourced=provided_or_sourced,
        assumption_share=share,
    )


def assess_financials(
    plan: FinancialPlanInput, *, cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG
) -> FinancialAssessmentResult:
    assumptions = _collect_assumptions(plan)
    missing = _missing_core_drivers(plan)
    over_assumed = assumptions.assumption_share > cfg.max_assumption_share

    if missing or over_assumed:
        findings: list[FinanceFinding] = []
        warnings: list[str] = []
        if missing:
            findings.append(
                FinanceFinding(code="missing_core_driver", message=f"missing: {'; '.join(missing)}")
            )
            warnings.append(
                f"{len(missing)} core financial driver(s) missing; no calculation was run"
            )
        if over_assumed:
            findings.append(
                FinanceFinding(
                    code="over_assumed",
                    message=(
                        f"assumption_share ({assumptions.assumption_share:.0%}) exceeds the "
                        f"configured maximum ({cfg.max_assumption_share:.0%})"
                    ),
                )
            )
            warnings.append("too much of this plan rests on unstated assumptions to assess it")
        return FinancialAssessmentResult(
            status=FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE,
            rung=FinanceLadderRung.MISSING_CORE_DRIVER,
            category=plan.category,
            horizon_months=plan.horizon_months,
            missing_core_drivers=missing,
            findings=findings,
            assumptions=assumptions,
            declared_margin_requirement=plan.financing.declared_margin_requirement,
            caveats=list(cfg.caveats),
            config=cfg,
            warnings=warnings,
        )

    core = run_core_pipeline(plan, cfg=cfg)
    status, rung, findings = decide_status(core, cfg=cfg)

    stress_results: list[StressResult] = []
    breaking_point = ""
    if status is FinancialFeasibilityStatus.FEASIBLE:
        stress_results = run_stress_scenarios(plan, cfg=cfg)
        breaking_point = find_breaking_point(status, stress_results)
        if breaking_point:
            status = FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH
            rung = FinanceLadderRung.STRESS_SENSITIVE

    return FinancialAssessmentResult(
        status=status,
        rung=rung,
        category=plan.category,
        horizon_months=plan.horizon_months,
        project_cost=core.project_cost,
        working_capital=core.working_capital,
        revenue=core.revenue_schedule,
        operating_costs=core.operating,
        break_even=core.break_even,
        debt=core.debt,
        cash_flow=core.cash_flow,
        dscr=core.dscr,
        capital_gap_inr=core.capital_gap_inr,
        promoter_contribution_pct=core.promoter_contribution_pct,
        declared_margin_requirement=plan.financing.declared_margin_requirement,
        stress_results=stress_results,
        breaking_point=breaking_point,
        findings=findings,
        assumptions=assumptions,
        caveats=list(cfg.caveats),
        config=cfg,
        warnings=list(core.financing_notes),
    )
