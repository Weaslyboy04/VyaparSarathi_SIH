"""Phase 2D overall-market-assessment models (CLAUDE.md §11, §12, §22).

Phase 2D fuses the Phase 2B competition metrics and the Phase 2C demand signals
and answers *"what does the observed evidence say about the market for **this**
proposed business **here**?"* — a label, never a score, never a recommendation
verb, and never a comparison across businesses (that is Phase 3 / §12).

Design points that live in the type shapes:

* The demand axis is :class:`CatchmentScale`, named for what was measured (a
  Census head-count), not "demand level" — a head-count is residents on paper,
  not purchasing power (§3.5, §30).
* The label is decided by an explicit :class:`LadderRung` sequence first, then a
  config matrix. ``label_basis`` records which rung or matrix cell decided it.
* ``assessment_data_confidence`` measures how well the market is *observed*,
  never whether the business will succeed (§22); it can never change the label.
* Findings are typed :class:`Finding` objects with machine-readable ``code`` and
  :class:`EvidenceRef` links back to the upstream results; no rule emits a bare
  string, and none carries a severity (severity is an implicit weight → §12).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.market.assessment_config import AssessmentConfig
from vyaparsarathi.market.metrics_models import CompetitionSignal
from vyaparsarathi.models.taxonomy import BusinessCategory


class MarketAssessmentStatus(StrEnum):
    """Could the assessment run? Mirrors the 2B / 2C status pattern."""

    OK = "ok"
    UNKNOWN_CATEGORY = "unknown_category"  # 2A/2B could not map the proposed business
    INVALID_RADIUS = "invalid_radius"  # 2B/2C analysis radius <= 0
    LOCATION_UNRESOLVED = "location_unresolved"  # 2C had no catchment centre
    SOURCE_UNAVAILABLE = "source_unavailable"  # 2C acquisition failed entirely
    INCONSISTENT_INPUTS = "inconsistent_inputs"  # 2B and 2C describe different catchments


class MarketAssessmentLabel(StrEnum):
    """What the evidence implies about the market. Market-state **nouns**, never
    action verbs (`proceed` / `pivot` / `avoid` are Phase 3+ / Phase 6)."""

    UNDERSERVED = "underserved"  # sizable catchment, little / no observed competition
    SERVED = "served"  # sizable catchment, substantial competition
    CROWDED = "crowded"  # small catchment, substantial competition
    THIN_MARKET = "thin_market"  # small catchment AND little competition
    MIXED = "mixed"  # mid-band on both axes, or offsetting axes
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # a ladder rung refused a call


class CatchmentScale(StrEnum):
    """Size of the local catchment. Derived from Census population when available,
    else from an activity/settlement proxy (capped at ``MODERATE``). **Not** a
    demand or purchasing-power measure."""

    UNKNOWN = "unknown"
    SMALL = "small"
    MODERATE = "moderate"
    LARGE = "large"


class ScaleTier(StrEnum):
    POPULATION = "population"  # Census persons — the only tier that may reach LARGE
    ACTIVITY_PROXY = "activity_proxy"  # settlement + anchor counts; capped at MODERATE
    NONE = "none"  # -> CatchmentScale.UNKNOWN


class LadderRung(StrEnum):
    """Which precedence rung decided the label (``matrix`` = no rung fired)."""

    UPSTREAM_STATUS = "upstream_status"
    INCONSISTENT_INPUTS = "inconsistent_inputs"
    SCALE_UNKNOWN = "scale_unknown"
    NOTHING_OBSERVED = "nothing_observed"
    ABSENCE_NOT_EVIDENCE = "absence_not_evidence"
    MATRIX = "matrix"


class FindingKind(StrEnum):
    POSITIVE = "positive"
    CONCERN = "concern"
    DATA_CAVEAT = "data_caveat"  # evidence-quality, structurally separate from concerns


class EvidenceRef(BaseModel):
    """A typed pointer at an upstream field the finding rests on, so a test can
    resolve the path and confirm the value."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["competition", "demand", "analysis"]
    field: str  # dotted path within that result, e.g. "catchment.persons"
    value: float | int | str | bool | None
    compared_to: float | None = None  # the config threshold the rule used, when any


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str  # stable, machine-readable, unique within a result
    kind: FindingKind
    message: str  # rendered from a fixed template in AssessmentConfig
    evidence: list[EvidenceRef] = Field(default_factory=list)


class ScaleBasis(BaseModel):
    """Everything that went into :class:`CatchmentScale`, so the level is fully
    reconstructible (2B ``signal_basis`` precedent)."""

    model_config = ConfigDict(extra="forbid")

    tier: ScaleTier
    persons: int | None = None
    persons_is_lower_bound: bool = False  # from demand.catchment.is_floor
    population_coverage: float | None = None
    settlements_found: int = 0
    settlements_with_population: int = 0
    activity_points: int = 0
    distinct_activity_kinds: int = 0
    thresholds: dict[str, float] = Field(default_factory=dict)
    capped_by_tier: bool = False  # True when the proxy tier held the level down
    reason: str = ""


class CompetitionSummary(BaseModel):
    """A flattened view of the 2B fields the assessment used — not a copy of the
    whole result."""

    model_config = ConfigDict(extra="forbid")

    signal: CompetitionSignal
    direct_count: int = 0
    adjacent_count: int = 0
    nearest_direct_m: float | None = None
    direct_per_km2: float | None = None
    data_confidence: float = 0.0


class DemandSummary(BaseModel):
    """A flattened view of the 2C fields the assessment used."""

    model_config = ConfigDict(extra="forbid")

    persons: int | None = None
    persons_is_lower_bound: bool = False
    population_coverage: float | None = None
    settlements_found: int = 0
    activity_points: int = 0
    distinct_activity_kinds: int = 0
    population_available_not_geolocated: bool = False
    demand_data_confidence: float = 0.0


class MarketAssessmentResult(BaseModel):
    """Phase 2D output. Deterministic, JSON-serializable. Carries no numeric
    market/opportunity score by design (§12)."""

    model_config = ConfigDict(extra="forbid")

    status: MarketAssessmentStatus

    # --- what was assessed (echoed) ---
    proposed_category: BusinessCategory
    proposed_subtypes: list[str] = Field(default_factory=list)
    location_text: str | None = None
    analysis_radius_m: int = 0

    # --- the two axes, each with its basis ---
    catchment_scale: CatchmentScale = CatchmentScale.UNKNOWN
    scale_basis: ScaleBasis = Field(default_factory=lambda: ScaleBasis(tier=ScaleTier.NONE))
    competition_signal: CompetitionSignal = CompetitionSignal.NONE
    competition_summary: CompetitionSummary | None = None
    demand_summary: DemandSummary | None = None

    # --- the interpretation ---
    label: MarketAssessmentLabel = MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    label_reason: str = ""
    label_basis: dict[str, str | None] = Field(default_factory=dict)  # rung / matrix key
    persons_per_direct_competitor: float | None = None  # legible inverse of 2C's ratio

    positive_signals: list[Finding] = Field(default_factory=list)
    concerns: list[Finding] = Field(default_factory=list)
    data_caveats: list[Finding] = Field(default_factory=list)

    # --- evidence quality, NOT viability ---
    assessment_data_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    assessment_data_confidence_note: str = (
        "How well the local market is OBSERVED (the weaker of the Phase 2B and "
        "Phase 2C data confidences, times a tier penalty), NOT whether the "
        "business will succeed (CLAUDE.md §22). It never changes the label."
    )
    assessment_data_confidence_basis: dict[str, float | None] = Field(default_factory=dict)

    assessment_caveats: list[str] = Field(default_factory=list)  # fixed, config-authored
    config: AssessmentConfig = Field(default_factory=AssessmentConfig)
    warnings: list[str] = Field(default_factory=list)
