"""`conversation/recommendation.py::combine` — the 30-cell Stance x
FinancialFeasibilityStatus decision table (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import itertools

import pytest

from vyaparsarathi.conversation.recommendation import combine
from vyaparsarathi.conversation.recommendation_models import Verdict
from vyaparsarathi.finance.assessment_models import (
    FinanceLadderRung,
    FinancialAssessmentResult,
)
from vyaparsarathi.finance.assessment_models import (
    FinancialFeasibilityStatus as F,
)
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult, OpportunityStatus
from vyaparsarathi.market.opportunity_models import Stance as S
from vyaparsarathi.models.taxonomy import BusinessCategory

_EXPECTED: dict[tuple[S, F], Verdict] = {}
for _s in (S.NO_PROPOSAL_TO_COMPARE, S.NO_RECOMMENDATION):
    for _f in F:
        _EXPECTED[(_s, _f)] = Verdict.INSUFFICIENT_EVIDENCE

for _s in (S.PROPOSED_IS_BEST, S.ALTERNATIVE_MATERIALLY_BETTER, S.ALTERNATIVES_COMPARABLE):
    _EXPECTED[(_s, F.INSUFFICIENT_FINANCIAL_EVIDENCE)] = Verdict.INSUFFICIENT_EVIDENCE

for _f in (
    F.FEASIBLE,
    F.FEASIBLE_WITH_STRETCH,
    F.FINANCING_GAP,
    F.CASH_FLOW_STRESS,
    F.UNSERVICEABLE,
):
    _EXPECTED[(S.ALTERNATIVE_MATERIALLY_BETTER, _f)] = Verdict.PIVOT

_EXPECTED[(S.PROPOSED_IS_BEST, F.UNSERVICEABLE)] = Verdict.ADJUST
_EXPECTED[(S.PROPOSED_IS_BEST, F.FEASIBLE)] = Verdict.PROCEED
_EXPECTED[(S.PROPOSED_IS_BEST, F.FEASIBLE_WITH_STRETCH)] = Verdict.PROCEED_WITH_CAUTION
_EXPECTED[(S.PROPOSED_IS_BEST, F.FINANCING_GAP)] = Verdict.ADJUST
_EXPECTED[(S.PROPOSED_IS_BEST, F.CASH_FLOW_STRESS)] = Verdict.ADJUST

_EXPECTED[(S.ALTERNATIVES_COMPARABLE, F.UNSERVICEABLE)] = Verdict.ADJUST
_EXPECTED[(S.ALTERNATIVES_COMPARABLE, F.FEASIBLE)] = Verdict.PROCEED_WITH_CAUTION
_EXPECTED[(S.ALTERNATIVES_COMPARABLE, F.FEASIBLE_WITH_STRETCH)] = Verdict.PROCEED_WITH_CAUTION
_EXPECTED[(S.ALTERNATIVES_COMPARABLE, F.FINANCING_GAP)] = Verdict.ADJUST
_EXPECTED[(S.ALTERNATIVES_COMPARABLE, F.CASH_FLOW_STRESS)] = Verdict.ADJUST


def _opportunity(stance: S, *, pivot: BusinessCategory | None = None) -> OpportunityAnalysisResult:
    return OpportunityAnalysisResult(
        status=OpportunityStatus.OK,
        stance=stance,
        recommended_pivot=pivot,
        market_data_confidence=0.5,
    )


def _finance(status: F) -> FinancialAssessmentResult:
    return FinancialAssessmentResult(
        status=status,
        rung=FinanceLadderRung.CLEARS_ALL,
        category=BusinessCategory.GROCERY,
    )


def test_all_thirty_cells_are_covered_by_the_fixture() -> None:
    assert len(_EXPECTED) == 30
    assert set(_EXPECTED) == set(itertools.product(S, F))


@pytest.mark.parametrize("cell", sorted(_EXPECTED, key=lambda c: (c[0].value, c[1].value)))
def test_combiner_matches_the_decision_table(cell: tuple[S, F]) -> None:
    stance, status = cell
    pivot = (
        BusinessCategory.LIVESTOCK_SERVICES if stance is S.ALTERNATIVE_MATERIALLY_BETTER else None
    )
    result = combine(_opportunity(stance, pivot=pivot), None, _finance(status))
    assert result.verdict is _EXPECTED[cell], (
        f"{cell} -> expected {_EXPECTED[cell]}, got {result.verdict}"
    )


def test_pivot_verdict_always_names_a_pivot() -> None:
    result = combine(
        _opportunity(S.ALTERNATIVE_MATERIALLY_BETTER, pivot=BusinessCategory.DAIRY),
        None,
        _finance(F.FEASIBLE),
    )
    assert result.verdict is Verdict.PIVOT
    assert result.recommended_pivot is BusinessCategory.DAIRY


def test_unserviceable_never_proceeds() -> None:
    for stance in (S.PROPOSED_IS_BEST, S.ALTERNATIVES_COMPARABLE):
        result = combine(_opportunity(stance), None, _finance(F.UNSERVICEABLE))
        assert result.verdict is not Verdict.PROCEED


def test_confidence_perturbation_never_changes_the_verdict() -> None:
    base = combine(_opportunity(S.PROPOSED_IS_BEST), None, _finance(F.FEASIBLE))
    for confidence in (0.0, 0.2, 0.5, 0.9, 1.0):
        opp = _opportunity(S.PROPOSED_IS_BEST)
        opp = opp.model_copy(
            update={"market_data_confidence": confidence, "profile_completeness": confidence}
        )
        result = combine(opp, None, _finance(F.FEASIBLE))
        assert result.verdict is base.verdict


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
