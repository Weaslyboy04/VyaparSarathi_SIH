"""Typed, read-only access to a finished session's step artifacts (CLAUDE.md
§25 Phase 8). PURE.

`load_artifacts` reconstructs each `StepArtifact.payload` into the pydantic
model class that produced it (`conversation/step_models.py::STEP_RESULT_MODEL`
— the same reconstruction `conversation/bundle.py` and `llm/tools.py` already
do), so the assembler reads strongly-typed objects and never re-runs an
engine. A step with no artifact is simply `None`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel

from vyaparsarathi.conversation.recommendation_models import RecommendationResult
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.conversation.swot_models import SwotResult
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.finance.capacity import SchemeCapacityResult
from vyaparsarathi.finance.structuring_models import SchemeStructureResult
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.demand_models import DemandSignalsResult
from vyaparsarathi.market.metrics_models import CompetitionMetricsResult
from vyaparsarathi.market.models import CompetitorAnalysisResult, ProposedBusiness
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult
from vyaparsarathi.models.finance import FinancialPlanInput
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence
from vyaparsarathi.models.results import DiscoveryResult


@dataclass(frozen=True)
class ArtifactSet:
    proposed: ProposedBusiness | None
    discovery: DiscoveryResult | None
    analysis: CompetitorAnalysisResult | None
    metrics: CompetitionMetricsResult | None
    demand: DemandSignalsResult | None
    market: MarketAssessmentResult | None
    opportunity: OpportunityAnalysisResult | None
    knowledge: FinanceKnowledgeEvidence | None
    plan: FinancialPlanInput | None  # BIND_PLAN (falls back to BUILD_PLAN)
    scheme_capacity: SchemeCapacityResult | None
    structure: SchemeStructureResult | None
    finance: FinancialAssessmentResult | None
    recommendation: RecommendationResult | None
    swot: SwotResult | None


_M = TypeVar("_M", bound=BaseModel)


def _load(session: ConversationSession, step: StepId, model_cls: type[_M]) -> _M | None:
    art = session.artifacts.get(step)
    if art is None:
        return None
    return model_cls.model_validate(art.payload)


def load_artifacts(session: ConversationSession) -> ArtifactSet:
    plan = _load(session, StepId.BIND_PLAN, FinancialPlanInput) or _load(
        session, StepId.BUILD_PLAN, FinancialPlanInput
    )
    return ArtifactSet(
        proposed=_load(session, StepId.RESOLVE_PROPOSED, ProposedBusiness),
        discovery=_load(session, StepId.DISCOVER, DiscoveryResult),
        analysis=_load(session, StepId.ANALYZE, CompetitorAnalysisResult),
        metrics=_load(session, StepId.METRICS, CompetitionMetricsResult),
        demand=_load(session, StepId.DEMAND_SIGNALS, DemandSignalsResult),
        market=_load(session, StepId.ASSESS_MARKET, MarketAssessmentResult),
        opportunity=_load(session, StepId.OPPORTUNITY, OpportunityAnalysisResult),
        knowledge=_load(session, StepId.FINANCE_KNOWLEDGE, FinanceKnowledgeEvidence),
        plan=plan,
        scheme_capacity=_load(session, StepId.SCHEME_CAPACITY, SchemeCapacityResult),
        structure=_load(session, StepId.STRUCTURE_FINANCE, SchemeStructureResult),
        finance=_load(session, StepId.ASSESS_FINANCE, FinancialAssessmentResult),
        recommendation=_load(session, StepId.RECOMMEND, RecommendationResult),
        swot=_load(session, StepId.SWOT, SwotResult),
    )


__all__ = ["ArtifactSet", "load_artifacts"]
