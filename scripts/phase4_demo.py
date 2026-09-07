"""Phase 4 deterministic financial engine — fixture-based demo cases
(CLAUDE.md §15, §16, §17).

Six hand-authored ``FinancialPlanInput`` scenarios that exercise the shipped
Phase 4 engine end to end **with no live APIs and no market/geospatial data**.
Every number is an ``ASSUMED`` :class:`FinancialInput` whose rationale reads
*"illustrative fixture value for the demo"* — none is a market survey, a
quotation, or a benchmark. ``run_demo`` scores it, prints a concise
human-readable summary, and checks the expectations.

Run:  ``./.venv/Scripts/python.exe scripts/phase4_demo.py``          (all six)
      ``./.venv/Scripts/python.exe scripts/phase4_demo.py 3``        (one)

The same builders are imported by ``tests/test_finance_demos.py`` so the
expectations are asserted in the gate too.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import (
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.finance.fit import to_financial_fit
from vyaparsarathi.models.finance import (
    AssetSpendOffset,
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
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_RATIONALE = "illustrative fixture value for the demo — not a market survey, not a quotation"

# --------------------------------------------------------------------------
# fixture primitives
# --------------------------------------------------------------------------


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x",
        value=value,
        unit=unit,
        kind=InputKind.ASSUMED,
        rationale=_RATIONALE,
        source="config:phase4-demo",
    )


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _profile() -> EntrepreneurProfile:
    """Bhagwanpur, Vaishali, Bihar: Rs 650,000 cash, storefront, vehicle, dairy
    experience — the scenario named in CLAUDE.md §31 / the Phase 4 task brief."""
    return EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_raw_text="pulses grocery store",
    )


def _bhagwanpur_pulses_grocery_plan(
    *,
    loan_principal: int = 80_000,
    loan_rate: str = "11",
    loan_tenure: int = 48,
    moratorium: int = 6,
    treatment: MoratoriumTreatment = MoratoriumTreatment.INTEREST_SERVICED,
    ramp_months: int = 3,
    ramp_start: str = "0.5",
    horizon_months: int = 24,
    fixed_opex: int = 5_000,
    cushion_months: int = 2,
    include_revenue: bool = True,
) -> FinancialPlanInput:
    """The base fixture every demo but #5 builds on. Every figure is
    ``ASSUMED``, except the entrepreneur's own stated cash contribution and
    the storefront's stated spend offset, which are ``USER_PROVIDED``."""
    revenue = (
        RevenueInput(
            monthly_revenue=_assumed(120_000, Unit.INR_PER_MONTH),
            ramp_months=_assumed(ramp_months, Unit.MONTHS),
            ramp_start_pct=_assumed(Decimal(ramp_start), Unit.RATIO),
        )
        if include_revenue
        else RevenueInput()
    )
    return FinancialPlanInput(
        category=C.GROCERY,
        profile=_profile(),
        project_cost=ProjectCostInput(
            lines=[
                CostLine(
                    label="shop fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(150_000, Unit.INR),
                ),
                CostLine(
                    label="shelving and equipment",
                    kind=CostLineKind.EQUIPMENT,
                    amount=_assumed(80_000, Unit.INR),
                ),
            ],
            offsets=[
                AssetSpendOffset(
                    asset=AssetKind.STOREFRONT,
                    reduces_line="shop fit-out",
                    amount_avoided=_provided(150_000, Unit.INR),
                )
            ],
            contingency_pct=_assumed(Decimal("0.05"), Unit.RATIO),
        ),
        working_capital=WorkingCapitalInput(
            inventory_days=_assumed(30, Unit.DAYS),
            opex_cushion_months=_assumed(cushion_months, Unit.MONTHS),
        ),
        revenue=revenue,
        operating_costs=OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.12"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(fixed_opex, Unit.INR_PER_MONTH))],
        ),
        financing=FinancingInput(
            promoter_cash_contribution=_provided(150_000, Unit.INR),
            loan=LoanTerms(
                principal_requested=_assumed(loan_principal, Unit.INR),
                interest_rate_pct=_assumed(Decimal(loan_rate), Unit.PERCENT_PER_ANNUM),
                tenure_months=_assumed(loan_tenure, Unit.MONTHS),
                moratorium_months=_assumed(moratorium, Unit.MONTHS),
                moratorium_treatment=treatment,
            ),
        ),
        horizon_months=horizon_months,
    )


# --------------------------------------------------------------------------
# demo builders -> (title, plan)
# --------------------------------------------------------------------------

Demo = tuple[str, FinancialPlanInput]


def build_demo_1() -> Demo:
    """Bhagwanpur pulses grocery, financed and structured comfortably."""
    return "1. Bhagwanpur pulses grocery — comfortably financed", _bhagwanpur_pulses_grocery_plan()


def build_demo_2() -> Demo:
    """Same plan, but the requested loan is far too small for the project."""
    return (
        "2. Same plan, loan requested too small",
        _bhagwanpur_pulses_grocery_plan(loan_principal=5_000),
    )


def build_demo_3() -> Demo:
    """A short tenure at a punishing rate: the debt cannot be serviced."""
    return (
        "3. Short tenure + high rate — unserviceable",
        _bhagwanpur_pulses_grocery_plan(
            loan_principal=200_000,
            loan_rate="36",
            loan_tenure=6,
            moratorium=0,
            treatment=MoratoriumTreatment.NONE,
        ),
    )


def build_demo_4() -> Demo:
    """A smaller loan under a capitalised 6-month moratorium plus a slow ramp:
    cash dips negative for several months during ramp-up, then recovers
    strongly once the ramp completes."""
    return (
        "4. Capitalised moratorium + slow ramp — cash-flow stress",
        _bhagwanpur_pulses_grocery_plan(
            loan_principal=40_000,
            treatment=MoratoriumTreatment.INTEREST_CAPITALISED,
            ramp_months=6,
            ramp_start="0",
            fixed_opex=9_000,
            cushion_months=0,
        ),
    )


def build_demo_5() -> Demo:
    """The revenue driver is simply not supplied. No number is fabricated to
    fill the gap."""
    return (
        "5. Revenue driver omitted — insufficient financial evidence",
        _bhagwanpur_pulses_grocery_plan(include_revenue=False),
    )


def build_demo_6() -> Demo:
    """The moratorium treatment is never assumed: run the SAME plan under all
    three real treatments (plus a no-moratorium baseline) and show that EMI,
    total interest, and cash at EMI start all differ. The registered demo runs
    the INTEREST_SERVICED variant; ``run_demo`` prints the full comparison."""
    return (
        "6. Same plan, four moratorium treatments compared",
        _bhagwanpur_pulses_grocery_plan(
            treatment=MoratoriumTreatment.INTEREST_SERVICED, moratorium=6
        ),
    )


DEMOS: dict[int, Callable[[], Demo]] = {
    1: build_demo_1,
    2: build_demo_2,
    3: build_demo_3,
    4: build_demo_4,
    5: build_demo_5,
    6: build_demo_6,
}


# --------------------------------------------------------------------------
# expectations (asserted here and in tests/test_finance_demos.py)
# --------------------------------------------------------------------------

_BANNED_VERDICT = ("impossible", "unaffordable", "guaranteed", "eligible", "sanctioned", "approved")


def _text_blob(res: FinancialAssessmentResult) -> str:
    parts = list(res.caveats) + list(res.warnings) + [res.breaking_point]
    parts += [f.message for f in res.findings]
    parts += [s.description for s in res.stress_results]
    return " ".join(parts).lower()


def check_common(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    for banned in _BANNED_VERDICT:
        if banned in _text_blob(res):
            f.append(f"banned verdict word '{banned}' appears in the result text")
    if res.status is not FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE:
        if res.project_cost is None:
            f.append("a calculated result has no project_cost")
        if res.cash_flow is None:
            f.append("a calculated result has no cash_flow")
        if res.dscr is None:
            f.append("a calculated result has no dscr")
    if (
        res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
        and res.project_cost is not None
    ):
        f.append("insufficient-evidence result still ran a calculation")
    if res.caveats != list(res.config.caveats):
        f.append("caveats do not match the config that produced them")
    return f


def _d1(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    if res.status not in (
        FinancialFeasibilityStatus.FEASIBLE,
        FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH,
    ):
        f.append(f"status {res.status.value}, expected feasible or feasible_with_stretch")
    if res.capital_gap_inr != Decimal("0.00"):
        f.append(f"capital_gap_inr {res.capital_gap_inr}, expected 0.00 (fully funded)")
    return f


def _d2(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    if res.status is not FinancialFeasibilityStatus.FINANCING_GAP:
        f.append(f"status {res.status.value}, expected financing_gap")
    if not (res.capital_gap_inr and res.capital_gap_inr > 0):
        f.append("capital_gap_inr is not positive")
    return f


def _d3(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    if res.status is not FinancialFeasibilityStatus.UNSERVICEABLE:
        f.append(f"status {res.status.value}, expected unserviceable")
    if not any(
        f_.code
        in ("average_dscr_unserviceable", "negative_amortisation", "cash_negative_at_horizon")
        for f_ in res.findings
    ):
        f.append("no finding names the breaking condition")
    return f


def _d4(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    if res.status is not FinancialFeasibilityStatus.CASH_FLOW_STRESS:
        f.append(f"status {res.status.value}, expected cash_flow_stress")
    if not res.cash_flow or not res.cash_flow.negative_cash_months:
        f.append("no negative cash month was detected")
    if not res.cash_flow or res.cash_flow.months[-1].closing_cash_inr <= 0:
        f.append("cash does not recover by the end of the horizon")
    return f


def _d5(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    if res.status is not FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE:
        f.append(f"status {res.status.value}, expected insufficient_financial_evidence")
    if not any("revenue driver" in d for d in res.missing_core_drivers):
        f.append("missing_core_drivers does not name the revenue driver")
    if res.project_cost is not None:
        f.append("a calculation ran despite missing evidence")
    return f


def _d6(res: FinancialAssessmentResult) -> list[str]:
    f: list[str] = []
    plan = _bhagwanpur_pulses_grocery_plan(moratorium=0, treatment=MoratoriumTreatment.NONE)
    none_debt = compute_debt_schedule(plan.financing.loan)  # type: ignore[arg-type]
    variants = {}
    for treatment in (
        MoratoriumTreatment.INTEREST_SERVICED,
        MoratoriumTreatment.INTEREST_CAPITALISED,
        MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
    ):
        p = _bhagwanpur_pulses_grocery_plan(moratorium=6, treatment=treatment)
        variants[treatment] = compute_debt_schedule(p.financing.loan)  # type: ignore[arg-type]
    emis = {none_debt.emi_inr} | {d.emi_inr for d in variants.values()}
    starts = {none_debt.principal_at_emi_start_inr} | {
        d.principal_at_emi_start_inr for d in variants.values()
    }
    if len(emis) < 2:
        f.append("EMI does not differ across moratorium treatments")
    if len(starts) < 2:
        f.append("principal at EMI start does not differ across moratorium treatments")
    return f


_DEMO_CHECKS: dict[int, Callable[[FinancialAssessmentResult], list[str]]] = {
    1: _d1,
    2: _d2,
    3: _d3,
    4: _d4,
    5: _d5,
    6: _d6,
}


# --------------------------------------------------------------------------
# rendering + run
# --------------------------------------------------------------------------


def _money(value: Decimal | None) -> str:
    return "  -  " if value is None else f"Rs {value:,.2f}"


def summarise(title: str, res: FinancialAssessmentResult) -> str:
    lines = [
        "=" * 78,
        title,
        "=" * 78,
        f"Status: {res.status.value.upper()}    Rung: {res.rung.value}    "
        f"Category: {res.category.value}    Horizon: {res.horizon_months}mo",
    ]
    if res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE:
        lines.append(f"Missing: {'; '.join(res.missing_core_drivers)}")
        lines.append(f"Assumption share: {res.assumptions.assumption_share:.0%}")
        return "\n".join(lines)

    assert res.project_cost is not None
    assert res.cash_flow is not None
    assert res.dscr is not None
    lines += [
        f"Project cost:        {_money(res.project_cost.project_cost_inr)}",
        f"  capex subtotal:    {_money(res.project_cost.capex_subtotal_inr)}",
        f"  contingency:       {_money(res.project_cost.contingency_inr)}",
        f"  net working cap.:  {_money(res.project_cost.net_working_capital_inr)}",
        f"Capital gap:         {_money(res.capital_gap_inr)}",
        f"Promoter contrib. %: {res.promoter_contribution_pct}",
    ]
    if res.debt is not None:
        lines += [
            f"EMI:                 {_money(res.debt.emi_inr)}",
            f"Total interest:      {_money(res.debt.total_interest_inr)}",
        ]
    if res.break_even is not None:
        lines += [
            f"Break-even revenue:  {_money(res.break_even.break_even_revenue_monthly_inr)}/mo "
            f"(month {res.break_even.operating_break_even_month})",
        ]
    lines += [
        f"Average annual DSCR: {res.dscr.average_annual_dscr}",
        f"1st post-mor. DSCR:  {res.dscr.first_post_moratorium_year_dscr}",
        f"Minimum cash:        {_money(res.cash_flow.minimum_cash_balance_inr)} "
        f"(month {res.cash_flow.minimum_cash_month})",
        f"Cash at EMI start:   {_money(res.cash_flow.cash_at_emi_start_inr)}",
        "",
        f"Stress scenarios ({len(res.stress_results)}):",
    ]
    for s in res.stress_results:
        status = s.status.value if s.status else f"skipped ({s.skipped_reason})"
        lines.append(f"  - {s.name:<20} {status}")
    if res.breaking_point:
        lines.append(f"Breaking point: {res.breaking_point}")
    lines.append(
        f"Assumption share: {res.assumptions.assumption_share:.0%} "
        f"({res.assumptions.core_drivers_provided_or_sourced}/{res.assumptions.core_drivers_total} "
        "provided or sourced)"
    )
    fit = to_financial_fit(res)
    lines.append(f"Phase 3 handoff: feasible={fit.feasible}  capital_gap_inr={fit.capital_gap_inr}")
    return "\n".join(lines)


def _moratorium_comparison_table() -> str:
    plan_none = _bhagwanpur_pulses_grocery_plan(moratorium=0, treatment=MoratoriumTreatment.NONE)
    rows = [("none (no moratorium)", compute_debt_schedule(plan_none.financing.loan))]  # type: ignore[arg-type]
    for treatment in (
        MoratoriumTreatment.INTEREST_SERVICED,
        MoratoriumTreatment.INTEREST_CAPITALISED,
        MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
    ):
        p = _bhagwanpur_pulses_grocery_plan(moratorium=6, treatment=treatment)
        rows.append((treatment.value, compute_debt_schedule(p.financing.loan)))  # type: ignore[arg-type]

    lines = [
        "",
        "-" * 78,
        "Moratorium treatment comparison (6-month moratorium, same loan):",
        "-" * 78,
    ]
    lines.append(f"  {'treatment':<32}{'EMI':>14}{'principal@start':>18}{'total interest':>16}")
    for name, debt in rows:
        lines.append(
            f"  {name:<32}{_money(debt.emi_inr):>14}{_money(debt.principal_at_emi_start_inr):>18}"
            f"{_money(debt.total_interest_inr):>16}"
        )
    return "\n".join(lines)


def run_demo(index: int, *, verbose: bool = True) -> tuple[FinancialAssessmentResult, list[str]]:
    title, plan = DEMOS[index]()
    res = assess_financials(plan)

    again = assess_financials(plan)
    determinism_fail = res.model_dump(mode="json") != again.model_dump(mode="json")

    common = check_common(res)
    specific = _DEMO_CHECKS[index](res)
    fails = list(common) + list(specific)
    if determinism_fail:
        fails.append("two runs of the same plan were not byte-identical")

    if verbose:
        print(summarise(title, res))
        if index == 6:
            print(_moratorium_comparison_table())
        print()
        print("CHECKS: PASS" if not fails else f"CHECKS: FAIL -> {fails}")
    return res, fails


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    indices = [int(a) for a in args] if args else sorted(DEMOS)
    all_fails: dict[int, list[str]] = {}
    for i in indices:
        _, fails = run_demo(i)
        if fails:
            all_fails[i] = fails
    print("=" * 78)
    if all_fails:
        print(f"RESULT: {len(all_fails)} demo(s) FAILED expectations: {all_fails}")
        return 1
    print(f"RESULT: all {len(indices)} demo(s) matched their expected Phase 4 behaviour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
