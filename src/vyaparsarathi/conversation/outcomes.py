"""Total status -> outcome maps, one per Phase 1-5 result enum (CLAUDE.md §22,
§25 Phase 6, plan §"Error handling"). `tests/test_conversation_status_map.py`
iterates every member of every enum listed in `OUTCOME_TABLES` and asserts it
is present — adding a status to any upstream enum then fails this suite
loudly instead of silently falling into an `else` branch somewhere in
`render.py`.

Every message here is fixed, human-authored prose (CLAUDE.md §32: "not ...
we built a chatbot" — no interpretation is generated here, only selected).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.severity import Severity
from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus
from vyaparsarathi.finance.structuring_models import SchemeStructureStatus
from vyaparsarathi.market.assessment_models import MarketAssessmentStatus
from vyaparsarathi.market.demand_models import DemandStatus
from vyaparsarathi.market.metrics_models import CompetitionMetricsStatus
from vyaparsarathi.market.models import CompetitorAnalysisStatus
from vyaparsarathi.market.opportunity_models import CapitalFit, OpportunityStatus
from vyaparsarathi.models.parameters import ResolutionStatus
from vyaparsarathi.models.results import DiscoveryStatus


class OutcomeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    severity: Severity
    message: str


DISCOVERY_OUTCOMES: dict[DiscoveryStatus, OutcomeSpec] = {
    DiscoveryStatus.OK: OutcomeSpec(
        severity=Severity.INFO, message="Local businesses were found for this area."
    ),
    DiscoveryStatus.NO_RESULTS: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "OpenStreetMap shows no matching businesses here. Absence from OSM is not "
            "absence in reality — coverage is often sparse in small villages."
        ),
    ),
    DiscoveryStatus.LOCATION_AMBIGUOUS: OutcomeSpec(
        severity=Severity.BLOCKED,
        message="Several places match that location; please choose one.",
    ),
    DiscoveryStatus.LOCATION_NOT_FOUND: OutcomeSpec(
        severity=Severity.BLOCKED,
        message="That location could not be found; try a more specific place name.",
    ),
    DiscoveryStatus.SOURCE_UNAVAILABLE: OutcomeSpec(
        severity=Severity.ERROR,
        message="The map data source (OpenStreetMap/Overpass) is unavailable right now.",
    ),
}

ANALYSIS_OUTCOMES: dict[CompetitorAnalysisStatus, OutcomeSpec] = {
    CompetitorAnalysisStatus.OK: OutcomeSpec(
        severity=Severity.INFO, message="Nearby businesses were classified against the proposal."
    ),
    CompetitorAnalysisStatus.UNKNOWN_CATEGORY: OutcomeSpec(
        severity=Severity.BLOCKED,
        message="The proposed business could not be matched to a known category.",
    ),
}

METRICS_OUTCOMES: dict[CompetitionMetricsStatus, OutcomeSpec] = {
    CompetitionMetricsStatus.OK: OutcomeSpec(
        severity=Severity.INFO, message="Competition metrics were computed."
    ),
    CompetitionMetricsStatus.UNKNOWN_CATEGORY: OutcomeSpec(
        severity=Severity.BLOCKED, message="Competition metrics need a resolved business category."
    ),
    CompetitionMetricsStatus.INVALID_RADIUS: OutcomeSpec(
        severity=Severity.ERROR, message="The analysis radius was invalid."
    ),
}

DEMAND_OUTCOMES: dict[DemandStatus, OutcomeSpec] = {
    DemandStatus.OK: OutcomeSpec(severity=Severity.INFO, message="Local demand signals were read."),
    DemandStatus.NO_POPULATION_DATA: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "No Census 2011 population record exists for this area; demand is estimated "
            "from settlement/activity proxies only, capped at a moderate scale."
        ),
    ),
    DemandStatus.POPULATION_NOT_GEOLOCATED: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "Census population exists for this area but could not be placed on the map; "
            "the catchment population is unknown, not zero."
        ),
    ),
    DemandStatus.NO_SETTLEMENTS_FOUND: OutcomeSpec(
        severity=Severity.DEGRADED, message="No known settlement was found in this catchment."
    ),
    DemandStatus.LOCATION_UNRESOLVED: OutcomeSpec(
        severity=Severity.BLOCKED, message="Demand analysis needs a resolved location first."
    ),
    DemandStatus.SOURCE_UNAVAILABLE: OutcomeSpec(
        severity=Severity.ERROR, message="Every demand data source failed for this area."
    ),
    DemandStatus.INVALID_RADIUS: OutcomeSpec(
        severity=Severity.ERROR, message="The analysis radius was invalid."
    ),
}

MARKET_ASSESSMENT_OUTCOMES: dict[MarketAssessmentStatus, OutcomeSpec] = {
    MarketAssessmentStatus.OK: OutcomeSpec(
        severity=Severity.INFO,
        message="Competition and demand were combined into a market reading.",
    ),
    MarketAssessmentStatus.UNKNOWN_CATEGORY: OutcomeSpec(
        severity=Severity.BLOCKED, message="The market reading needs a resolved business category."
    ),
    MarketAssessmentStatus.INVALID_RADIUS: OutcomeSpec(
        severity=Severity.ERROR, message="The analysis radius was invalid."
    ),
    MarketAssessmentStatus.LOCATION_UNRESOLVED: OutcomeSpec(
        severity=Severity.BLOCKED, message="The market reading needs a resolved location."
    ),
    MarketAssessmentStatus.SOURCE_UNAVAILABLE: OutcomeSpec(
        severity=Severity.ERROR, message="Underlying data sources were unavailable."
    ),
    MarketAssessmentStatus.INCONSISTENT_INPUTS: OutcomeSpec(
        severity=Severity.ERROR,
        message=(
            "Internal wiring inconsistency (competition and demand described different catchments)."
        ),
    ),
}

OPPORTUNITY_OUTCOMES: dict[OpportunityStatus, OutcomeSpec] = {
    OpportunityStatus.OK: OutcomeSpec(
        severity=Severity.INFO, message="Candidate businesses were scored and ranked."
    ),
    OpportunityStatus.NO_CANDIDATES: OutcomeSpec(
        severity=Severity.DEGRADED, message="No candidate businesses were available to score."
    ),
    OpportunityStatus.NO_EVIDENCE: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="No candidate had a usable market reading; scoring could not proceed.",
    ),
}

FINANCE_OUTCOMES: dict[FinancialFeasibilityStatus, OutcomeSpec] = {
    FinancialFeasibilityStatus.FEASIBLE: OutcomeSpec(
        severity=Severity.INFO, message="The plan clears financing, debt service and stress tests."
    ),
    FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH: OutcomeSpec(
        severity=Severity.INFO,
        message="The plan is feasible but sensitive to a named stress scenario.",
    ),
    FinancialFeasibilityStatus.FINANCING_GAP: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="There is a gap between the funding needed and what is committed.",
    ),
    FinancialFeasibilityStatus.CASH_FLOW_STRESS: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="Cash flow is projected to run tight or negative at some point.",
    ),
    FinancialFeasibilityStatus.UNSERVICEABLE: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="The proposed debt cannot be serviced from projected cash flow.",
    ),
    FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE: OutcomeSpec(
        severity=Severity.BLOCKED,
        message="Not enough financial information was given to run a calculation.",
    ),
}

STRUCTURE_OUTCOMES: dict[SchemeStructureStatus, OutcomeSpec] = {
    SchemeStructureStatus.STRUCTURED: OutcomeSpec(
        severity=Severity.INFO,
        message="A financing structure was derived from the declared scheme split.",
    ),
    SchemeStructureStatus.NOT_CONFIGURED: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="No financing structure is declared for this deployment yet.",
    ),
    SchemeStructureStatus.INSUFFICIENT_EVIDENCE: OutcomeSpec(
        severity=Severity.BLOCKED,
        message="Project cost could not be derived yet, so no financing structure was built.",
    ),
}

RESOLUTION_OUTCOMES: dict[ResolutionStatus, OutcomeSpec] = {
    ResolutionStatus.RESOLVED: OutcomeSpec(
        severity=Severity.INFO, message="An official figure was found for this parameter."
    ),
    ResolutionStatus.NO_EVIDENCE: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "No official document is loaded for this parameter — a figure you actually "
            "quote can still be modelled as your own stated figure."
        ),
    ),
    ResolutionStatus.CONFLICTING: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="Sources disagree on this parameter; both readings are shown, neither is picked.",
    ),
    ResolutionStatus.STALE_ONLY: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="Only an out-of-date figure is available for this parameter.",
    ),
    ResolutionStatus.CONDITIONS_UNRESOLVED: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "A figure was found but carries conditions this system does not evaluate "
            "eligibility for."
        ),
    ),
}

CAPITAL_FIT_OUTCOMES: dict[CapitalFit, OutcomeSpec] = {
    CapitalFit.UNKNOWN: OutcomeSpec(
        severity=Severity.DEGRADED,
        message="Stated capital could not be screened against this business.",
    ),
    CapitalFit.AFFORDABLE: OutcomeSpec(
        severity=Severity.INFO,
        message="Stated capital is at or above the typical figure for this business.",
    ),
    CapitalFit.STRETCH: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "Stated capital is below the typical figure — an indicative screen, never a verdict."
        ),
    ),
    CapitalFit.OUT_OF_REACH: OutcomeSpec(
        severity=Severity.DEGRADED,
        message=(
            "Stated capital is below the indicative minimum — an indicative screen, "
            "never a verdict."
        ),
    ),
}

OUTCOME_TABLES: dict[type, dict] = {
    DiscoveryStatus: DISCOVERY_OUTCOMES,
    CompetitorAnalysisStatus: ANALYSIS_OUTCOMES,
    CompetitionMetricsStatus: METRICS_OUTCOMES,
    DemandStatus: DEMAND_OUTCOMES,
    MarketAssessmentStatus: MARKET_ASSESSMENT_OUTCOMES,
    OpportunityStatus: OPPORTUNITY_OUTCOMES,
    FinancialFeasibilityStatus: FINANCE_OUTCOMES,
    SchemeStructureStatus: STRUCTURE_OUTCOMES,
    ResolutionStatus: RESOLUTION_OUTCOMES,
    CapitalFit: CAPITAL_FIT_OUTCOMES,
}


def outcome_for(status: object) -> OutcomeSpec:
    table = OUTCOME_TABLES.get(type(status))
    if table is None:  # pragma: no cover — defensive; every real status has a table
        return OutcomeSpec(severity=Severity.INFO, message=str(status))
    found = table.get(status)
    if found is None:  # pragma: no cover — closed by the totality test
        return OutcomeSpec(severity=Severity.ERROR, message=f"unrecognised status: {status!r}")
    return found


__all__ = ["OUTCOME_TABLES", "OutcomeSpec", "outcome_for"]
