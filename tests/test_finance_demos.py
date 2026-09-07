"""Phase 4 demo scenarios as gate tests (CLAUDE.md §15, §16, §17).

The six fixture builders and their per-demo expectation checks live in
``scripts/phase4_demo.py`` so the demo script and the gate assert exactly the
same behaviour. Each test here runs one demo through the shipped engine and
asserts (a) the shared invariants and (b) that demo's specific expectations.
"""

from __future__ import annotations

import pytest
from scripts.phase4_demo import _DEMO_CHECKS, DEMOS, check_common

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus


@pytest.mark.parametrize("index", sorted(DEMOS))
def test_demo_matches_expected_phase4_behaviour(index: int) -> None:
    _title, plan = DEMOS[index]()
    res = assess_financials(plan)

    # determinism: a second run is byte-identical
    again = assess_financials(plan)
    assert res.model_dump(mode="json") == again.model_dump(mode="json")

    common = check_common(res)
    specific = _DEMO_CHECKS[index](res)
    assert not common, f"demo {index} shared-invariant failures: {common}"
    assert not specific, f"demo {index} expectation failures: {specific}"


def test_demo_1_is_feasible_and_fully_funded() -> None:
    _t, plan = DEMOS[1]()
    res = assess_financials(plan)
    assert res.status in (
        FinancialFeasibilityStatus.FEASIBLE,
        FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH,
    )
    assert res.capital_gap_inr == 0


def test_demo_2_is_a_financing_gap() -> None:
    _t, plan = DEMOS[2]()
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.FINANCING_GAP
    assert res.capital_gap_inr is not None and res.capital_gap_inr > 0


def test_demo_3_is_unserviceable() -> None:
    _t, plan = DEMOS[3]()
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.UNSERVICEABLE


def test_demo_4_is_cash_flow_stress_and_recovers() -> None:
    _t, plan = DEMOS[4]()
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.CASH_FLOW_STRESS
    assert res.cash_flow is not None
    assert res.cash_flow.negative_cash_months
    assert res.cash_flow.months[-1].closing_cash_inr > 0


def test_demo_5_is_insufficient_evidence_and_fabricates_nothing() -> None:
    _t, plan = DEMOS[5]()
    res = assess_financials(plan)
    assert res.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert res.project_cost is None
    assert res.cash_flow is None
    assert res.dscr is None


def test_demo_6_shows_moratorium_treatment_changes_the_numbers() -> None:
    _t, plan = DEMOS[6]()
    res = assess_financials(plan)
    assert res.debt is not None
    fails = _DEMO_CHECKS[6](res)
    assert not fails


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
