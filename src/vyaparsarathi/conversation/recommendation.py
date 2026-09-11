"""The Phase 3 x Phase 4 recommendation combiner (CLAUDE.md §4, §25 Phase 6).
PURE — a fully enumerated 5 (`Stance`) x 6 (`FinancialFeasibilityStatus`) =
30-cell decision table, asserted exhaustively in
`tests/test_recommendation_combiner.py`. Confidence values
(`market_data_confidence`, `assumption_share`, coverage confidences) are
never read here — CLAUDE.md §22: they can change *how sure* a reader should
be, never *what* the verdict is.
"""

from __future__ import annotations

from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
)
from vyaparsarathi.conversation.recommendation_models import RecommendationResult, Verdict
from vyaparsarathi.finance.assessment_models import (
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult, Stance

_INSUFFICIENT_STANCES = frozenset({Stance.NO_PROPOSAL_TO_COMPARE, Stance.NO_RECOMMENDATION})

_CAVEAT = (
    "This is a comparison of options and a financial screen, not a prediction of "
    "success. It does not replace a bank's own credit appraisal or a scheme's "
    "eligibility rules (CLAUDE.md §1, §32)."
)


def combine(
    opportunity: OpportunityAnalysisResult,
    market: MarketAssessmentResult | None,
    finance: FinancialAssessmentResult,
    *,
    cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG,
) -> RecommendationResult:
    """Combine a Phase 3 stance and a Phase 4 feasibility status into one
    `Verdict`. `market` is accepted for a consistent call shape and future
    narrative use; it is optional evidence the combiner itself does not need
    to decide anything (Phase 3's `stance` already rests on it)."""
    del cfg  # accepted for interface consistency; nothing here is tunable yet
    del market

    stance = opportunity.stance
    status = finance.status

    # rung 1: either input insufficient -> INSUFFICIENT_EVIDENCE, full stop.
    if (
        stance in _INSUFFICIENT_STANCES
        or status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    ):
        reason = "Not enough market or financial evidence was available to make a call yet."
        return RecommendationResult(
            verdict=Verdict.INSUFFICIENT_EVIDENCE,
            reason=reason,
            stance=stance,
            financial_status=status,
            caveats=[_CAVEAT],
        )

    # rung 2: a materially better alternative always wins, before financing is judged.
    if stance is Stance.ALTERNATIVE_MATERIALLY_BETTER:
        pivot_name = (
            opportunity.recommended_pivot.value
            if opportunity.recommended_pivot is not None
            else "an alternative"
        )
        return RecommendationResult(
            verdict=Verdict.PIVOT,
            reason=(
                f"{pivot_name} scores materially higher on local market evidence than "
                "the proposed business — a potential opportunity, not a decided call. "
                "Trade experience and licensing/compliance requirements are not checked "
                "in this assessment; verify both locally before committing."
            ),
            stance=stance,
            financial_status=status,
            recommended_pivot=opportunity.recommended_pivot,
            caveats=[_CAVEAT],
        )

    # rung 3: an unserviceable debt structure is never a straight PROCEED.
    if status is FinancialFeasibilityStatus.UNSERVICEABLE:
        return RecommendationResult(
            verdict=Verdict.ADJUST,
            reason="The proposed debt cannot be serviced from projected cash flow as structured.",
            stance=stance,
            financial_status=status,
            caveats=[_CAVEAT],
        )

    if status in (
        FinancialFeasibilityStatus.FINANCING_GAP,
        FinancialFeasibilityStatus.CASH_FLOW_STRESS,
    ):
        return RecommendationResult(
            verdict=Verdict.ADJUST,
            reason="The financial plan needs adjustment before this is ready to fund.",
            stance=stance,
            financial_status=status,
            caveats=[_CAVEAT],
        )

    # From here status is FEASIBLE or FEASIBLE_WITH_STRETCH.
    if stance is Stance.PROPOSED_IS_BEST:
        if status is FinancialFeasibilityStatus.FEASIBLE:
            return RecommendationResult(
                verdict=Verdict.PROCEED,
                reason=(
                    "The proposed business ranks best locally and the financial plan "
                    "clears its checks."
                ),
                stance=stance,
                financial_status=status,
                caveats=[_CAVEAT],
            )
        return RecommendationResult(
            verdict=Verdict.PROCEED_WITH_CAUTION,
            reason="The plan is financially feasible but sensitive to a named stress scenario.",
            stance=stance,
            financial_status=status,
            caveats=[_CAVEAT],
        )

    # stance is ALTERNATIVES_COMPARABLE: even a clean financial pass does not
    # make the proposal unambiguously the right choice.
    return RecommendationResult(
        verdict=Verdict.PROCEED_WITH_CAUTION,
        reason="The financial plan clears its checks, but comparable alternatives exist locally.",
        stance=stance,
        financial_status=status,
        caveats=[_CAVEAT],
    )


__all__ = ["combine"]
