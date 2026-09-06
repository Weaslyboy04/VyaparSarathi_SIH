"""Market engine — competitor identification, competition metrics, demand signals.

Phase 2A answers *which* discovered businesses compete with the proposed
business, and why (``analyze_competitors``). Phase 2B answers *how concentrated*
that competition is (``compute_competition_metrics``). Phase 2C answers *is there
evidence of local customer potential* (``compute_demand_signals``).

Every engine here is pure: it consumes only Phase 1 / Phase 2A / Phase 2C result
objects and never touches OSM, Nominatim, HTTP, or the database (enforced by
``tests/test_market_purity.py``). None judges business viability — that is Phase
2D and later.
"""

from vyaparsarathi.market.assessment import assess_market
from vyaparsarathi.market.assessment_config import DEFAULT_ASSESSMENT_CONFIG, AssessmentConfig
from vyaparsarathi.market.assessment_models import (
    CatchmentScale,
    Finding,
    MarketAssessmentLabel,
    MarketAssessmentResult,
    MarketAssessmentStatus,
    ScaleTier,
)
from vyaparsarathi.market.classifier import analyze_competitors, analyze_from_discovery
from vyaparsarathi.market.demand import compute_demand_signals
from vyaparsarathi.market.demand_config import DEFAULT_DEMAND_CONFIG, DemandConfig
from vyaparsarathi.market.demand_models import (
    ActivitySummary,
    CatchmentPopulation,
    DemandCoverage,
    DemandSignalsResult,
    DemandStatus,
)
from vyaparsarathi.market.metrics import compute_competition_metrics, metrics_from_discovery
from vyaparsarathi.market.metrics_config import DEFAULT_METRICS_CONFIG, CompetitionMetricsConfig
from vyaparsarathi.market.metrics_models import (
    CompetitionDensity,
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
    CompetitionSignal,
    DistanceBand,
    DistanceStats,
)
from vyaparsarathi.market.models import (
    ClassifiedCompetitor,
    CompetitorAnalysisResult,
    CompetitorAnalysisStatus,
    ProposedBusiness,
    Relationship,
)
from vyaparsarathi.market.proposed import proposed_from_category, resolve_proposed_business
from vyaparsarathi.market.relationships import relationship_for

__all__ = [
    "Relationship",
    "ProposedBusiness",
    "ClassifiedCompetitor",
    "CompetitorAnalysisResult",
    "CompetitorAnalysisStatus",
    "relationship_for",
    "proposed_from_category",
    "resolve_proposed_business",
    "analyze_competitors",
    "analyze_from_discovery",
    "CompetitionMetricsConfig",
    "DEFAULT_METRICS_CONFIG",
    "CompetitionMetricsResult",
    "CompetitionMetricsStatus",
    "CompetitionSignal",
    "CompetitionDensity",
    "DistanceBand",
    "DistanceStats",
    "compute_competition_metrics",
    "metrics_from_discovery",
    "compute_demand_signals",
    "DemandConfig",
    "DEFAULT_DEMAND_CONFIG",
    "DemandSignalsResult",
    "DemandStatus",
    "CatchmentPopulation",
    "ActivitySummary",
    "DemandCoverage",
    "assess_market",
    "AssessmentConfig",
    "DEFAULT_ASSESSMENT_CONFIG",
    "MarketAssessmentResult",
    "MarketAssessmentStatus",
    "MarketAssessmentLabel",
    "CatchmentScale",
    "ScaleTier",
    "Finding",
]
