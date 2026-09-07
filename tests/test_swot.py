"""`conversation/swot.py` (CLAUDE.md §3.5, §11, §12, §22, §30; Tier 1
"Deterministic Structured SWOT"). Pure & offline.
"""

from __future__ import annotations

import pytest

from vyaparsarathi.conversation.grounding import BANNED_PHRASES
from vyaparsarathi.conversation.swot import build_swot
from vyaparsarathi.conversation.swot_config import SwotConfig
from vyaparsarathi.conversation.swot_models import SwotQuadrant, SwotStatus
from vyaparsarathi.finance.assessment_models import (
    FinanceFinding,
    FinanceLadderRung,
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
    StressResult,
)
from vyaparsarathi.finance.structuring_models import (
    SchemeStructureResult,
    SchemeStructureStatus,
    StructureFinding,
)
from vyaparsarathi.market.assessment_models import (
    Finding,
    FindingKind,
    MarketAssessmentResult,
    MarketAssessmentStatus,
)
from vyaparsarathi.market.opportunity_models import (
    CapitalFit,
    OpportunityAnalysisResult,
    OpportunityStatus,
    ScoreComponent,
    ScoredCandidate,
    Stance,
)
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _finance(**overrides: object) -> FinancialAssessmentResult:
    base: dict[str, object] = {
        "status": FinancialFeasibilityStatus.FEASIBLE,
        "rung": FinanceLadderRung.CLEARS_ALL,
        "category": C.GROCERY,
    }
    base.update(overrides)
    return FinancialAssessmentResult(**base)  # type: ignore[arg-type]


def _opportunity(**overrides: object) -> OpportunityAnalysisResult:
    base: dict[str, object] = {
        "status": OpportunityStatus.OK,
        "stance": Stance.PROPOSED_IS_BEST,
    }
    base.update(overrides)
    return OpportunityAnalysisResult(**base)  # type: ignore[arg-type]


def _market(**overrides: object) -> MarketAssessmentResult:
    base: dict[str, object] = {
        "status": MarketAssessmentStatus.OK,
        "proposed_category": C.GROCERY,
    }
    base.update(overrides)
    return MarketAssessmentResult(**base)  # type: ignore[arg-type]


def _candidate(**overrides: object) -> ScoredCandidate:
    base: dict[str, object] = {"category": C.GROCERY}
    base.update(overrides)
    return ScoredCandidate(**base)  # type: ignore[arg-type]


def _component(name: str, value: float, *, reason: str = "reason text") -> ScoreComponent:
    return ScoreComponent(
        name=name,
        available=True,
        value=value,
        nominal_weight=0.6,
        effective_weight=0.6,
        contribution=value * 0.6,
        reason=reason,
    )


def _market_finding(code: str, kind: FindingKind, message: str) -> Finding:
    return Finding(code=code, kind=kind, message=message)


def _structure(**overrides: object) -> SchemeStructureResult:
    base: dict[str, object] = {
        "status": SchemeStructureStatus.STRUCTURED,
        "category": C.GROCERY,
    }
    base.update(overrides)
    return SchemeStructureResult(**base)  # type: ignore[arg-type]


# --- gates -------------------------------------------------------------


def test_no_evidence_status_yields_no_evidence_and_every_quadrant_noted() -> None:
    result = build_swot(
        _opportunity(status=OpportunityStatus.NO_EVIDENCE, stance=Stance.NO_RECOMMENDATION),
        _finance(),
    )
    assert result.status is SwotStatus.NO_EVIDENCE
    assert result.items == []
    assert set(result.quadrant_notes) == set(SwotQuadrant)


# --- strengths -----------------------------------------------------------


def test_strength_sourced_from_positive_signal() -> None:
    market = _market(
        positive_signals=[
            _market_finding(
                "no_direct_competitors_confident",
                FindingKind.POSITIVE,
                "No direct competitors were found, with confident coverage.",
            )
        ]
    )
    result = build_swot(_opportunity(), _finance(), market=market)
    strengths = [i for i in result.items if i.quadrant is SwotQuadrant.STRENGTH]
    assert any(i.code == "strength.market.no_direct_competitors_confident" for i in strengths)


def test_low_coverage_absence_produces_no_strength_only_a_data_caveat() -> None:
    """The key safety property: a low-confidence 'no competitors found' case
    must never become a Strength — it must land only in data_caveats."""
    market = _market(
        positive_signals=[],  # Phase 2D's own ladder never emits the POSITIVE finding here
        data_caveats=[
            _market_finding(
                "competitor_absence_low_coverage",
                FindingKind.DATA_CAVEAT,
                "No competitors were found, but coverage confidence is low.",
            )
        ],
    )
    result = build_swot(_opportunity(), _finance(), market=market)
    assert not any(
        i.quadrant is SwotQuadrant.STRENGTH and i.origin.value == "market_assessment"
        for i in result.items
    )
    assert any("low" in c for c in result.data_caveats)


def test_strong_component_becomes_a_strength() -> None:
    candidate = _candidate(
        is_proposed=True,
        opportunity_score=85,
        rank=1,
        components=[_component("market_opportunity", 90.0)],
    )
    opp = _opportunity(candidates=[candidate])
    result = build_swot(opp, _finance())
    assert any(i.code == "strength.opportunity.market_opportunity" for i in result.items)


def test_weak_component_is_not_a_strength() -> None:
    candidate = _candidate(
        is_proposed=True,
        opportunity_score=40,
        rank=1,
        components=[_component("market_opportunity", 30.0)],
    )
    opp = _opportunity(candidates=[candidate])
    result = build_swot(opp, _finance())
    assert not any(i.code == "strength.opportunity.market_opportunity" for i in result.items)


def test_affordable_capital_fit_is_a_strength() -> None:
    candidate = _candidate(
        is_proposed=True,
        capital_fit=CapitalFit.AFFORDABLE,
        capital_fit_reason="Stated capital is comfortably above the typical figure.",
    )
    opp = _opportunity(candidates=[candidate])
    result = build_swot(opp, _finance())
    assert any(i.code == "strength.opportunity.capital_fit_affordable" for i in result.items)


def test_feasible_finance_is_a_strength() -> None:
    result = build_swot(_opportunity(), _finance(status=FinancialFeasibilityStatus.FEASIBLE))
    assert any(i.code == "strength.finance.feasible" for i in result.items)


def test_non_feasible_finance_is_not_a_strength() -> None:
    result = build_swot(
        _opportunity(),
        _finance(
            status=FinancialFeasibilityStatus.CASH_FLOW_STRESS, rung=FinanceLadderRung.CASH_STRESS
        ),
    )
    assert not any(i.code == "strength.finance.feasible" for i in result.items)


# --- weaknesses ------------------------------------------------------------


def test_non_competitive_concern_is_a_weakness() -> None:
    market = _market(
        concerns=[
            _market_finding("small_catchment", FindingKind.CONCERN, "The catchment is small.")
        ]
    )
    result = build_swot(_opportunity(), _finance(), market=market)
    assert any(
        i.code == "weakness.market.small_catchment" and i.quadrant is SwotQuadrant.WEAKNESS
        for i in result.items
    )


def test_competitive_concern_is_a_threat_not_a_weakness() -> None:
    market = _market(
        concerns=[
            _market_finding("high_competition", FindingKind.CONCERN, "Competition is high here.")
        ]
    )
    result = build_swot(_opportunity(), _finance(), market=market)
    assert any(i.code == "threat.market.high_competition" for i in result.items)
    assert not any(i.code == "weakness.market.high_competition" for i in result.items)


def test_missing_component_is_a_weakness() -> None:
    candidate = _candidate(is_proposed=True, components_missing=["experience_fit"])
    opp = _opportunity(candidates=[candidate])
    result = build_swot(opp, _finance())
    assert any(i.code == "weakness.opportunity.missing.experience_fit" for i in result.items)


def test_finance_finding_is_a_weakness() -> None:
    result = build_swot(
        _opportunity(),
        _finance(
            status=FinancialFeasibilityStatus.FINANCING_GAP,
            rung=FinanceLadderRung.FUNDING_GAP,
            findings=[FinanceFinding(code="funding_gap", message="a gap of Rs 50000 remains")],
        ),
    )
    assert any(i.code == "weakness.finance.funding_gap" for i in result.items)


def test_missing_core_driver_is_a_weakness() -> None:
    result = build_swot(
        _opportunity(),
        _finance(
            status=FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE,
            rung=FinanceLadderRung.MISSING_CORE_DRIVER,
            missing_core_drivers=[
                "a revenue driver (monthly_revenue, or unit_price + units_per_month)"
            ],
        ),
    )
    assert any(i.code == "weakness.finance.missing_driver.0" for i in result.items)


def test_structured_finding_is_a_weakness_only_when_structured() -> None:
    structure = _structure(
        status=SchemeStructureStatus.STRUCTURED,
        findings=[
            StructureFinding(code="margin_shortfall_against_liquid_cash", message="shortfall text")
        ],
    )
    result = build_swot(_opportunity(), _finance(), structure=structure)
    assert any(
        i.code == "weakness.structure.margin_shortfall_against_liquid_cash" for i in result.items
    )


def test_not_configured_structure_findings_are_not_folded_into_weaknesses() -> None:
    structure = _structure(
        status=SchemeStructureStatus.NOT_CONFIGURED,
        findings=[StructureFinding(code="scheme_not_configured", message="no scheme declared")],
    )
    result = build_swot(_opportunity(), _finance(), structure=structure)
    assert not any(i.origin.value == "structure" for i in result.items)


# --- opportunities -----------------------------------------------------


def test_pivot_recommendation_is_an_opportunity() -> None:
    opp = _opportunity(
        stance=Stance.ALTERNATIVE_MATERIALLY_BETTER,
        recommended_pivot=C.DAIRY,
        stance_reason="Dairy scores materially higher here.",
    )
    result = build_swot(opp, _finance())
    assert any(i.code == "opportunity.pivot.recommended" for i in result.items)


def test_higher_ranked_alternative_is_an_opportunity() -> None:
    proposed = _candidate(category=C.GROCERY, is_proposed=True, opportunity_score=60, rank=2)
    better = _candidate(category=C.DAIRY, is_proposed=False, opportunity_score=80, rank=1)
    opp = _opportunity(candidates=[better, proposed])
    result = build_swot(opp, _finance())
    assert any(i.code == "opportunity.alternative.dairy" for i in result.items)


def test_lower_ranked_alternative_is_not_an_opportunity() -> None:
    proposed = _candidate(category=C.GROCERY, is_proposed=True, opportunity_score=80, rank=1)
    worse = _candidate(category=C.DAIRY, is_proposed=False, opportunity_score=40, rank=2)
    opp = _opportunity(candidates=[proposed, worse])
    result = build_swot(opp, _finance())
    assert not any(i.code == "opportunity.alternative.dairy" for i in result.items)


# --- threats -----------------------------------------------------------


def test_worsening_stress_scenario_is_a_threat() -> None:
    result = build_swot(
        _opportunity(),
        _finance(
            stress_results=[
                StressResult(
                    name="revenue_down_20",
                    description="Sales run 20% below the base-case plan.",
                    status=FinancialFeasibilityStatus.CASH_FLOW_STRESS,
                )
            ]
        ),
    )
    assert any(i.code == "threat.finance.stress.revenue_down_20" for i in result.items)


def test_stress_scenario_that_stays_feasible_is_not_a_threat() -> None:
    result = build_swot(
        _opportunity(),
        _finance(
            stress_results=[
                StressResult(
                    name="revenue_down_20",
                    description="Sales run 20% below the base-case plan.",
                    status=FinancialFeasibilityStatus.FEASIBLE,
                )
            ]
        ),
    )
    assert not any(i.code == "threat.finance.stress.revenue_down_20" for i in result.items)


def test_breaking_point_is_a_threat() -> None:
    result = build_swot(_opportunity(), _finance(breaking_point="cash goes negative in month 8"))
    assert any(i.code == "threat.finance.breaking_point" for i in result.items)


# --- empty quadrants and truncation -------------------------------------


def test_empty_quadrants_get_an_explicit_note_not_padding() -> None:
    result = build_swot(_opportunity(), _finance())
    assert result.status is SwotStatus.OK
    for quadrant, count in _counts(result).items():
        if count == 0:
            assert quadrant in result.quadrant_notes
            assert result.quadrant_notes[quadrant]


def _counts(result) -> dict[SwotQuadrant, int]:  # type: ignore[no-untyped-def]
    counts = dict.fromkeys(SwotQuadrant, 0)
    for item in result.items:
        counts[item.quadrant] += 1
    return counts


def test_max_items_per_quadrant_truncates() -> None:
    market = _market(
        concerns=[
            _market_finding(f"small_catchment_{i}", FindingKind.CONCERN, f"concern {i}")
            for i in range(10)
        ]
    )
    cfg = SwotConfig(max_items_per_quadrant=2)
    result = build_swot(_opportunity(), _finance(), market=market, cfg=cfg)
    weaknesses = [i for i in result.items if i.quadrant is SwotQuadrant.WEAKNESS]
    assert len(weaknesses) == 2


# --- honesty / determinism -----------------------------------------------


def test_two_runs_are_byte_identical() -> None:
    market = _market(
        positive_signals=[_market_finding("x", FindingKind.POSITIVE, "a positive finding")]
    )
    opp = _opportunity()
    fin = _finance()
    a = build_swot(opp, fin, market=market)
    b = build_swot(opp, fin, market=market)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_no_item_or_note_contains_a_banned_phrase() -> None:
    market = _market(
        positive_signals=[
            _market_finding("p", FindingKind.POSITIVE, "no direct competitors were found")
        ],
        concerns=[
            _market_finding("high_competition", FindingKind.CONCERN, "competition is high"),
            _market_finding("small_catchment", FindingKind.CONCERN, "the catchment is small"),
        ],
        data_caveats=[_market_finding("d", FindingKind.DATA_CAVEAT, "coverage is thin")],
    )
    candidate = _candidate(
        is_proposed=True,
        opportunity_score=85,
        rank=1,
        capital_fit=CapitalFit.AFFORDABLE,
        components=[_component("market_opportunity", 90.0)],
    )
    opp = _opportunity(
        candidates=[candidate],
        stance=Stance.ALTERNATIVE_MATERIALLY_BETTER,
        recommended_pivot=C.DAIRY,
        stance_reason="a materially better alternative exists",
    )
    fin = _finance(
        findings=[FinanceFinding(code="funding_gap", message="a gap remains")],
        breaking_point="cash goes negative in month 8",
        stress_results=[
            StressResult(
                name="s",
                description="a downside scenario",
                status=FinancialFeasibilityStatus.CASH_FLOW_STRESS,
            )
        ],
    )
    structure = _structure(
        findings=[StructureFinding(code="loan_clipped_by_ceiling", message="the loan is capped")]
    )
    result = build_swot(opp, fin, market=market, structure=structure)

    all_text = " ".join(
        [i.text for i in result.items]
        + result.data_caveats
        + list(result.quadrant_notes.values())
        + result.caveats
    ).lower()
    for phrase in BANNED_PHRASES:
        assert phrase not in all_text, f"banned phrase {phrase!r} leaked into SWOT text"


def test_structuring_config_can_be_absent() -> None:
    """SWOT works with no scheme configured at all — never crashes."""
    result = build_swot(_opportunity(), _finance(), structure=None)
    assert result.status is SwotStatus.OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
