"""Deterministic Structured SWOT (CLAUDE.md §3.5, §11, §12, §22, §30; Tier 1).
PURE. No LLM, no new facts, no new calculation — every item is drawn from an
already-computed Phase 2D / Phase 3 / Phase 4 / Tier 1 structuring result.

Grounding discipline (the reason this module exists as a *selector*, not a
*generator*):

* **Strengths** come only from `MarketAssessmentResult.positive_signals` and
  from `ScoreComponent`s that are actually `available`. Phase 2D's own ladder
  already refuses `no_direct_competitors_confident` below
  `min_confidence_for_absence_claim` (it emits `competitor_absence_low_coverage`
  — a `DATA_CAVEAT` — instead), so sourcing Strengths this way inherits
  "absence of competition is not evidence of demand" (CLAUDE.md §11) for
  free, with no extra logic here.
* **Weaknesses** come from `concerns` (excluding the two competitive-pressure
  codes routed to Threats instead), `FinanceFinding`s, missing drivers, and
  — only when a real split was structured — `StructureFinding`s.
* **Opportunities** come from the Phase 3 ranked alternatives and pivot,
  never invented.
* **Threats** come from stress scenarios that break the base case, the named
  breaking point, and the two competitive-pressure concern codes.
* **`data_caveats`** (evidence-quality notes) are never promoted into any
  quadrant — sparse evidence is reported as sparse, never converted into a
  strength, weakness, or certainty of any kind (CLAUDE.md §3.5, §22).

Every `text` is copied verbatim from an existing `Finding.message` /
`FinanceFinding.message` / `StructureFinding.message` / `ScoreComponent.reason`
/ an `outcomes.py` outcome message, or is a small templated string built only
from those same objects' own fields — never freshly authored narrative.
"""

from __future__ import annotations

from vyaparsarathi.conversation.outcomes import CAPITAL_FIT_OUTCOMES, FINANCE_OUTCOMES
from vyaparsarathi.conversation.swot_config import DEFAULT_SWOT_CONFIG, SwotConfig
from vyaparsarathi.conversation.swot_models import (
    SwotItem,
    SwotOrigin,
    SwotQuadrant,
    SwotResult,
    SwotStatus,
)
from vyaparsarathi.finance.assessment_models import (
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.finance.structuring_models import SchemeStructureResult, SchemeStructureStatus
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.opportunity_models import (
    CapitalFit,
    OpportunityAnalysisResult,
    OpportunityStatus,
    ScoredCandidate,
    Stance,
)

# Competitive-pressure concerns read as external risk (Threats), not an
# internal plan gap (Weakness) — every other `concerns` code lands as a
# Weakness. Not a duplicated ladder: this is a fixed, closed re-routing of
# two named Phase 2D codes, nothing is re-derived.
_THREAT_CONCERN_CODES = frozenset({"high_competition", "competitor_very_close"})

_EMPTY_QUADRANT_NOTES: dict[SwotQuadrant, str] = {
    SwotQuadrant.STRENGTH: (
        "No positive market signal, strongly-scoring opportunity factor, or feasible "
        "financial status was available yet — this reflects thin evidence, not a "
        "judgement that this business has none."
    ),
    SwotQuadrant.WEAKNESS: "No market concern, missing driver, or financial finding was surfaced.",
    SwotQuadrant.OPPORTUNITY: "No higher-ranked local alternative or pivot was found.",
    SwotQuadrant.THREAT: "No stress scenario or named breaking point was available yet.",
}


def _proposed_candidate(opportunity: OpportunityAnalysisResult) -> ScoredCandidate | None:
    return next((c for c in opportunity.candidates if c.is_proposed), None)


def _strengths(
    opportunity: OpportunityAnalysisResult,
    finance: FinancialAssessmentResult,
    market: MarketAssessmentResult | None,
    cfg: SwotConfig,
) -> list[SwotItem]:
    items: list[SwotItem] = []
    if market is not None:
        for f in market.positive_signals:
            items.append(
                SwotItem(
                    code=f"strength.market.{f.code}",
                    quadrant=SwotQuadrant.STRENGTH,
                    text=f.message,
                    origin=SwotOrigin.MARKET_ASSESSMENT,
                    source_ref=f"market_assessment.positive_signals.{f.code}",
                )
            )

    proposed = _proposed_candidate(opportunity)
    if proposed is not None:
        for comp in proposed.components:
            if (
                comp.available
                and comp.value is not None
                and comp.value >= cfg.strong_component_threshold
            ):
                items.append(
                    SwotItem(
                        code=f"strength.opportunity.{comp.name}",
                        quadrant=SwotQuadrant.STRENGTH,
                        text=comp.reason,
                        origin=SwotOrigin.OPPORTUNITY,
                        source_ref=f"opportunity.candidates[proposed].components.{comp.name}",
                    )
                )
        if proposed.capital_fit is CapitalFit.AFFORDABLE:
            text = (
                proposed.capital_fit_reason or CAPITAL_FIT_OUTCOMES[CapitalFit.AFFORDABLE].message
            )
            items.append(
                SwotItem(
                    code="strength.opportunity.capital_fit_affordable",
                    quadrant=SwotQuadrant.STRENGTH,
                    text=text,
                    origin=SwotOrigin.OPPORTUNITY,
                    source_ref="opportunity.candidates[proposed].capital_fit",
                )
            )

    if finance.status is FinancialFeasibilityStatus.FEASIBLE:
        items.append(
            SwotItem(
                code="strength.finance.feasible",
                quadrant=SwotQuadrant.STRENGTH,
                text=FINANCE_OUTCOMES[FinancialFeasibilityStatus.FEASIBLE].message,
                origin=SwotOrigin.FINANCE,
                source_ref="finance.status",
            )
        )
    return items


def _weaknesses(
    opportunity: OpportunityAnalysisResult,
    finance: FinancialAssessmentResult,
    market: MarketAssessmentResult | None,
    structure: SchemeStructureResult | None,
) -> list[SwotItem]:
    items: list[SwotItem] = []
    if market is not None:
        for f in market.concerns:
            if f.code in _THREAT_CONCERN_CODES:
                continue  # routed to Threats instead — see _threats
            items.append(
                SwotItem(
                    code=f"weakness.market.{f.code}",
                    quadrant=SwotQuadrant.WEAKNESS,
                    text=f.message,
                    origin=SwotOrigin.MARKET_ASSESSMENT,
                    source_ref=f"market_assessment.concerns.{f.code}",
                )
            )

    proposed = _proposed_candidate(opportunity)
    if proposed is not None:
        for name in proposed.components_missing:
            factor = name.replace("_", " ")
            items.append(
                SwotItem(
                    code=f"weakness.opportunity.missing.{name}",
                    quadrant=SwotQuadrant.WEAKNESS,
                    text=f"No data was available for the {factor} factor of the opportunity score.",
                    origin=SwotOrigin.OPPORTUNITY,
                    source_ref="opportunity.candidates[proposed].components_missing",
                )
            )

    for finding in finance.findings:
        items.append(
            SwotItem(
                code=f"weakness.finance.{finding.code}",
                quadrant=SwotQuadrant.WEAKNESS,
                text=finding.message,
                origin=SwotOrigin.FINANCE,
                source_ref=f"finance.findings.{finding.code}",
            )
        )
    for i, driver in enumerate(finance.missing_core_drivers):
        items.append(
            SwotItem(
                code=f"weakness.finance.missing_driver.{i}",
                quadrant=SwotQuadrant.WEAKNESS,
                text=f"Missing: {driver}",
                origin=SwotOrigin.FINANCE,
                source_ref=f"finance.missing_core_drivers[{i}]",
            )
        )

    if structure is not None and structure.status is SchemeStructureStatus.STRUCTURED:
        for structure_finding in structure.findings:
            items.append(
                SwotItem(
                    code=f"weakness.structure.{structure_finding.code}",
                    quadrant=SwotQuadrant.WEAKNESS,
                    text=structure_finding.message,
                    origin=SwotOrigin.STRUCTURE,
                    source_ref=f"structure.findings.{structure_finding.code}",
                )
            )
    return items


def _opportunities(opportunity: OpportunityAnalysisResult) -> list[SwotItem]:
    items: list[SwotItem] = []
    pivot = opportunity.recommended_pivot
    if opportunity.stance is Stance.ALTERNATIVE_MATERIALLY_BETTER and pivot is not None:
        pivot_label = pivot.value.replace("_", " ")
        text = (
            opportunity.stance_reason
            or f"A materially better local alternative was found: {pivot_label}."
        )
        items.append(
            SwotItem(
                code="opportunity.pivot.recommended",
                quadrant=SwotQuadrant.OPPORTUNITY,
                text=text,
                origin=SwotOrigin.OPPORTUNITY,
                source_ref="opportunity.recommended_pivot",
            )
        )

    proposed = _proposed_candidate(opportunity)
    proposed_rank = proposed.rank if proposed is not None else None
    if proposed_rank is not None:
        for cand in opportunity.candidates:
            if cand.is_proposed or cand.rank is None:
                continue
            if cand.rank < proposed_rank and cand.opportunity_score is not None:
                items.append(
                    SwotItem(
                        code=f"opportunity.alternative.{cand.category.value}",
                        quadrant=SwotQuadrant.OPPORTUNITY,
                        text=(
                            f"{cand.category.value.replace('_', ' ')} ranks higher in this "
                            f"location (opportunity score {cand.opportunity_score}/100)."
                        ),
                        origin=SwotOrigin.OPPORTUNITY,
                        source_ref=f"opportunity.candidates[{cand.category.value}].rank",
                    )
                )
    return items


def _threats(
    finance: FinancialAssessmentResult, market: MarketAssessmentResult | None
) -> list[SwotItem]:
    items: list[SwotItem] = []
    if market is not None:
        for f in market.concerns:
            if f.code not in _THREAT_CONCERN_CODES:
                continue
            items.append(
                SwotItem(
                    code=f"threat.market.{f.code}",
                    quadrant=SwotQuadrant.THREAT,
                    text=f.message,
                    origin=SwotOrigin.MARKET_ASSESSMENT,
                    source_ref=f"market_assessment.concerns.{f.code}",
                )
            )

    for sr in finance.stress_results:
        if not sr.applied or sr.status is None or sr.status is FinancialFeasibilityStatus.FEASIBLE:
            continue
        items.append(
            SwotItem(
                code=f"threat.finance.stress.{sr.name}",
                quadrant=SwotQuadrant.THREAT,
                text=sr.description,
                origin=SwotOrigin.STRESS,
                source_ref=f"finance.stress_results.{sr.name}",
            )
        )
    if finance.breaking_point:
        items.append(
            SwotItem(
                code="threat.finance.breaking_point",
                quadrant=SwotQuadrant.THREAT,
                text=finance.breaking_point,
                origin=SwotOrigin.FINANCE,
                source_ref="finance.breaking_point",
            )
        )
    return items


def _data_caveats(market: MarketAssessmentResult | None) -> list[str]:
    if market is None:
        return []
    caveats = [f.message for f in market.data_caveats]
    caveats.append(market.assessment_data_confidence_note)
    return caveats


def build_swot(
    opportunity: OpportunityAnalysisResult,
    finance: FinancialAssessmentResult,
    *,
    market: MarketAssessmentResult | None = None,
    structure: SchemeStructureResult | None = None,
    cfg: SwotConfig = DEFAULT_SWOT_CONFIG,
) -> SwotResult:
    if opportunity.status is OpportunityStatus.NO_EVIDENCE:
        return SwotResult(
            status=SwotStatus.NO_EVIDENCE,
            quadrant_notes=dict(_EMPTY_QUADRANT_NOTES),
            caveats=list(cfg.caveats),
            config=cfg,
            warnings=["no candidate had a usable market reading; SWOT could not be built"],
        )

    by_quadrant: dict[SwotQuadrant, list[SwotItem]] = {
        SwotQuadrant.STRENGTH: _strengths(opportunity, finance, market, cfg),
        SwotQuadrant.WEAKNESS: _weaknesses(opportunity, finance, market, structure),
        SwotQuadrant.OPPORTUNITY: _opportunities(opportunity),
        SwotQuadrant.THREAT: _threats(finance, market),
    }

    items: list[SwotItem] = []
    quadrant_notes: dict[SwotQuadrant, str] = {}
    for quadrant in SwotQuadrant:
        quadrant_items = by_quadrant[quadrant][: cfg.max_items_per_quadrant]
        if not quadrant_items:
            quadrant_notes[quadrant] = _EMPTY_QUADRANT_NOTES[quadrant]
        items.extend(quadrant_items)

    return SwotResult(
        status=SwotStatus.OK,
        items=items,
        data_caveats=_data_caveats(market),
        quadrant_notes=quadrant_notes,
        caveats=list(cfg.caveats),
        config=cfg,
    )


__all__ = ["build_swot"]
