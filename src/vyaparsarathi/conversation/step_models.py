"""`StepId` -> the pydantic model class its artifact payload deserializes
into (CLAUDE.md §25 Phase 6). PURE — `market/`, `finance/` and `knowledge/`
are pure engine packages (no I/O), so importing their *model* classes here
does not violate `conversation/`'s purity test; only `vyaparsarathi.{llm,
sources,geocoding,database,discovery}` are forbidden (see
`tests/test_conversation_purity.py`).

Shared by `llm/tools.py` (which reconstructs a typed object from
`StepArtifact.payload` to pass into the next engine call) and
`conversation/bundle.py` (which does the same to build `Fact` cards) so the
mapping is declared exactly once.
"""

from __future__ import annotations

from pydantic import BaseModel

from vyaparsarathi.conversation.recommendation_models import RecommendationResult
from vyaparsarathi.conversation.session_models import StepId
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.demand_models import DemandSignalsResult
from vyaparsarathi.market.metrics_models import CompetitionMetricsResult
from vyaparsarathi.market.models import CompetitorAnalysisResult, ProposedBusiness
from vyaparsarathi.market.opportunity_models import FinancialFitInput, OpportunityAnalysisResult
from vyaparsarathi.models.demand import DemandEvidence
from vyaparsarathi.models.finance import FinancialPlanInput
from vyaparsarathi.models.opportunity import OpportunityEvidence
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence
from vyaparsarathi.models.results import DiscoveryResult

STEP_RESULT_MODEL: dict[StepId, type[BaseModel]] = {
    StepId.RESOLVE_PROPOSED: ProposedBusiness,
    StepId.DISCOVER: DiscoveryResult,
    StepId.ANALYZE: CompetitorAnalysisResult,
    StepId.METRICS: CompetitionMetricsResult,
    StepId.DEMAND_EVIDENCE: DemandEvidence,
    StepId.DEMAND_SIGNALS: DemandSignalsResult,
    StepId.ASSESS_MARKET: MarketAssessmentResult,
    StepId.OPPORTUNITY_EVIDENCE: OpportunityEvidence,
    StepId.OPPORTUNITY: OpportunityAnalysisResult,
    StepId.FINANCE_KNOWLEDGE: FinanceKnowledgeEvidence,
    StepId.BUILD_PLAN: FinancialPlanInput,
    StepId.BIND_PLAN: FinancialPlanInput,
    StepId.ASSESS_FINANCE: FinancialAssessmentResult,
    StepId.FINANCIAL_FIT: FinancialFitInput,
    StepId.RECOMMEND: RecommendationResult,
}

__all__ = ["STEP_RESULT_MODEL"]
