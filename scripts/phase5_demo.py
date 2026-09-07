"""Phase 5 knowledge/evidence layer — a fixture-corpus demo end to end
(CLAUDE.md §18, §22, §23).

Runs the full Phase 5 -> Phase 4 seam against the synthetic, **visibly
fictional** corpus under ``tests/fixtures/knowledge/`` (see its own README):
resolve a handful of financial parameters for the Bhagwanpur/Vaishali/Bihar
pulses-grocery scenario named in CLAUDE.md §31, bind what resolves into a
`FinancialPlanInput`, and run the shipped Phase 4 engine on the result — the
same profile and category `scripts/phase4_demo.py` uses.

No figure here is real. No document, publisher, scheme, rate, fee, or
benchmark in the fixture corpus describes an actual Indian credit scheme —
every one is invented for testing, exactly as `phase4_demo.py`'s own
``_RATIONALE`` says of its own numbers. This script exists to show the
*machinery* working, not to report a real interest rate.

Run:  ``./.venv/Scripts/python.exe scripts/phase5_demo.py``
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from vyaparsarathi.discovery.knowledge_acquisition import acquire_finance_knowledge
from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.knowledge.plan_binding import bind_sourced_inputs, build_loan_terms
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence, ParameterName, ParameterQuery
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "knowledge"
_AS_OF = date(2026, 1, 1)
_RATIONALE = "illustrative fixture value for the demo — not a market survey, not a quotation"


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x",
        value=value,
        unit=unit,
        kind=InputKind.ASSUMED,
        rationale=_RATIONALE,
        source="config:phase5-demo",
    )


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _profile() -> EntrepreneurProfile:
    """Bhagwanpur, Vaishali, Bihar: Rs 650,000 cash, storefront + vehicle,
    dairy experience — the scenario named in CLAUDE.md §31, matching
    `scripts/phase4_demo.py::_profile`."""
    return EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_raw_text="pulses grocery store",
    )


def unbound_plan() -> FinancialPlanInput:
    """Before Phase 5: every market-facing figure is ASSUMED, matching
    `phase4_demo.py`'s own convention, except the promoter's own cash."""
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
            ]
        ),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(monthly_revenue=_assumed(120_000, Unit.INR_PER_MONTH)),
        operating_costs=OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.12"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(5_000, Unit.INR_PER_MONTH))],
        ),
        financing=FinancingInput(promoter_cash_contribution=_provided(150_000, Unit.INR)),
        horizon_months=24,
    )


def build_query() -> ParameterQuery:
    return ParameterQuery(
        names=(
            ParameterName.INTEREST_RATE_PCT,
            ParameterName.LOAN_TENURE_MONTHS,
            ParameterName.MORATORIUM_MONTHS,
            ParameterName.LICENCE_FEE_INR,
            ParameterName.PROMOTER_MARGIN_PCT,
            ParameterName.GROSS_MARGIN_PCT,
            ParameterName.INVENTORY_DAYS,
        ),
        category=C.GROCERY,
        state="Bihar",
        district="Vaishali",
        scheme="test-rural-udyog-yojana",
        loan_amount_inr=Decimal("80000"),
        as_of=_AS_OF,
    )


def gather_evidence(corpus_dir: Path = _FIXTURES) -> FinanceKnowledgeEvidence:
    store = FileCorpusStore(corpus_dir)
    return acquire_finance_knowledge(
        build_query(), corpus=store, clock=lambda: datetime(2026, 1, 20, tzinfo=UTC)
    )


def run() -> tuple[FinancialAssessmentResult, FinancialAssessmentResult]:
    """Returns (unbound_result, bound_result) — the same plan, before and
    after Phase 5 resolves what it can against the fixture corpus."""
    evidence = gather_evidence()
    bound = bind_sourced_inputs(unbound_plan(), evidence)
    loan, _loan_unbound = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    final_plan = bound.plan.model_copy(
        update={"financing": bound.plan.financing.model_copy(update={"loan": loan})}
    )
    unbound_result = assess_financials(unbound_plan())
    bound_result = assess_financials(final_plan)
    return unbound_result, bound_result


def _resolution_line(evidence: FinanceKnowledgeEvidence, name: ParameterName) -> str:
    resolution = evidence.resolution_for(name)
    if resolution is None:
        return f"  {name.value:<24} (not queried)"
    status = resolution.status.value
    if resolution.chosen is not None:
        return (
            f"  {name.value:<24} {status:<22} value={resolution.chosen.value} "
            f"{resolution.chosen.unit.value:<18} conf={resolution.confidence} "
            f"src={resolution.source_ref}"
        )
    return f"  {name.value:<24} {status}"


def summarise() -> str:
    evidence = gather_evidence()
    bound = bind_sourced_inputs(unbound_plan(), evidence)
    unbound_result, bound_result = run()

    lines = [
        "=" * 78,
        "Phase 5 demo — Bhagwanpur, Vaishali, Bihar / pulses grocery (SYNTHETIC FIXTURE DATA)",
        "=" * 78,
        "Every document, scheme, rate, and fee below is INVENTED for this demo — see "
        "tests/fixtures/knowledge/README.md.",
        "",
        "Resolutions:",
    ]
    for name in build_query().names:
        lines.append(_resolution_line(evidence, name))

    lines += [
        "",
        f"Bound into the plan ({len(bound.bound)}):",
        *[f"  - {b.name.value} -> {b.target_field}" for b in bound.bound],
        "",
        f"Resolved but NOT bound ({len(bound.unbound)}) — the unit-trap / no-field cases:",
        *[f"  - {u.name.value}: {u.reason}" for u in bound.unbound],
        "",
        f"Assumption share BEFORE Phase 5: {unbound_result.assumptions.assumption_share:.0%}",
        f"Assumption share AFTER Phase 5:  {bound_result.assumptions.assumption_share:.0%}",
        f"Sourced inputs after Phase 5:    "
        f"{bound_result.assumptions.counts_by_kind.get(InputKind.SOURCED, 0)}",
        "",
        f"Phase 4 status (before): {unbound_result.status.value}",
        f"Phase 4 status (after):  {bound_result.status.value}",
    ]
    return "\n".join(lines)


def check_common() -> list[str]:
    """Invariants a Phase 5 demo run must never violate."""
    failures: list[str] = []
    unbound_result, bound_result = run()
    if bound_result.assumptions.assumption_share >= unbound_result.assumptions.assumption_share:
        failures.append("binding evidence did not lower assumption_share")
    if bound_result.assumptions.counts_by_kind.get(InputKind.SOURCED, 0) < 1:
        failures.append("no SOURCED input reached the bound assessment")

    _unbound_again, bound_again = run()
    if bound_result.model_dump(mode="json") != bound_again.model_dump(mode="json"):
        failures.append("two runs of the same demo were not byte-identical")
    return failures


def main(argv: list[str] | None = None) -> int:
    del argv  # single scenario; no per-index selection like phase4_demo.py
    print(summarise())
    fails = check_common()
    print()
    print("CHECKS: PASS" if not fails else f"CHECKS: FAIL -> {fails}")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
