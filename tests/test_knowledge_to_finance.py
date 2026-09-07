"""End-to-end, fully offline: fixture corpus -> acquisition -> binding ->
`assess_financials` (CLAUDE.md §15, §18, §28). This is the whole Phase 5 ->
Phase 4 seam exercised together, on the same Bhagwanpur/Vaishali/Bihar
grocery scenario `scripts/phase4_demo.py` uses — see
`tests/fixtures/knowledge/README.md` for what every figure here actually is
(all synthetic)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vyaparsarathi.discovery.knowledge_acquisition import acquire_finance_knowledge
from vyaparsarathi.finance.assessment import assess_financials
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
from vyaparsarathi.models.parameters import ParameterName, ParameterQuery
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
_CLOCK = lambda: datetime(2026, 1, 20, tzinfo=UTC)  # noqa: E731 — a fixed, injected clock
_AS_OF = date(2026, 1, 1)


def _assumed(value, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x",
        value=value,
        unit=unit,
        kind=InputKind.ASSUMED,
        rationale="illustrative fixture value — not a market survey, not a quotation",
        source="config:test-fixture",
    )


def _provided(value, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _profile() -> EntrepreneurProfile:
    return EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_raw_text="pulses grocery store",
    )


def _unbound_plan() -> FinancialPlanInput:
    """The plan BEFORE any Phase 5 binding — everything ASSUMED except the
    promoter's own cash contribution."""
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


def _query() -> ParameterQuery:
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


def _run(corpus_dir: Path | str) -> tuple:
    store = FileCorpusStore(corpus_dir)
    evidence = acquire_finance_knowledge(_query(), corpus=store, clock=_CLOCK)
    bound = bind_sourced_inputs(_unbound_plan(), evidence)
    loan, loan_unbound = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    final_plan = bound.plan.model_copy(
        update={"financing": bound.plan.financing.model_copy(update={"loan": loan})}
    )
    result = assess_financials(final_plan)
    return result, bound, loan, loan_unbound, evidence


# ======================================================================
# the fixture corpus, end to end
# ======================================================================


def test_the_whole_seam_runs_to_completion() -> None:
    result, bound, loan, loan_unbound, evidence = _run(FIXTURES)
    assert result is not None
    assert loan is not None
    assert loan_unbound == ()


def test_sourced_inputs_actually_land_in_the_assessment() -> None:
    result, *_ = _run(FIXTURES)
    assert result.assumptions.counts_by_kind.get(InputKind.SOURCED, 0) >= 1


def test_assumption_share_is_lower_than_the_unbound_plan() -> None:
    bound_result, *_ = _run(FIXTURES)
    unbound_result = assess_financials(_unbound_plan())
    assert bound_result.assumptions.assumption_share < unbound_result.assumptions.assumption_share


def test_licence_fee_and_inventory_days_are_bound() -> None:
    _, bound, _, _, _ = _run(FIXTURES)
    bound_names = {b.name for b in bound.bound}
    assert ParameterName.LICENCE_FEE_INR in bound_names
    assert ParameterName.INVENTORY_DAYS in bound_names


def test_promoter_margin_resolves_but_never_reaches_the_plan() -> None:
    result, bound, *_ = _run(FIXTURES)
    assert result.declared_margin_requirement is None
    assert bound.plan.financing.declared_margin_requirement is None
    unbound_names = {u.name for u in bound.unbound}
    assert ParameterName.PROMOTER_MARGIN_PCT in unbound_names


def test_gross_margin_has_no_evidence_so_the_callers_own_assumption_survives() -> None:
    # The corpus deliberately carries no grocery gross-margin benchmark — the
    # point of the worked scenario. The plan's own ASSUMED figure must stay,
    # both on the bound plan (input) and in the computed result (output).
    _, bound, *_ = _run(FIXTURES)
    assert bound.plan.operating_costs.gross_margin_pct.kind is InputKind.ASSUMED
    assert bound.plan.operating_costs.gross_margin_pct.value == Decimal("0.12")


def test_loan_terms_are_fully_sourced() -> None:
    _, _, loan, _, _ = _run(FIXTURES)
    assert loan.interest_rate_pct.kind is InputKind.SOURCED
    assert loan.tenure_months.kind is InputKind.SOURCED
    assert loan.moratorium_months.kind is InputKind.SOURCED
    assert loan.principal_requested.kind is InputKind.USER_PROVIDED


# ======================================================================
# determinism — the clock-free binding path
# ======================================================================


def test_repeat_runs_are_byte_identical() -> None:
    result_a, *_ = _run(FIXTURES)
    result_b, *_ = _run(FIXTURES)
    assert result_a.model_dump(mode="json") == result_b.model_dump(mode="json")


# ======================================================================
# the empty-corpus equivalence — the key acceptance check
# ======================================================================


def test_empty_corpus_reproduces_the_unbound_plan_exactly(tmp_path: Path) -> None:
    empty_dir = tmp_path / "knowledge"
    empty_dir.mkdir()
    empty_result, bound, loan, loan_unbound, evidence = _run(empty_dir)

    assert loan is None
    assert len(loan_unbound) == 3
    assert bound.bound == ()
    assert bound.plan == _unbound_plan()

    unbound_reference = assess_financials(_unbound_plan())
    assert empty_result.model_dump(mode="json") == unbound_reference.model_dump(mode="json")


def test_missing_corpus_directory_also_reproduces_the_unbound_plan(tmp_path: Path) -> None:
    empty_result, bound, loan, loan_unbound, evidence = _run(tmp_path / "does-not-exist")
    assert evidence.acquisition.corpus_present is False
    assert any("no knowledge corpus" in w for w in evidence.warnings)
    assert loan is None
    unbound_reference = assess_financials(_unbound_plan())
    assert empty_result.model_dump(mode="json") == unbound_reference.model_dump(mode="json")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
