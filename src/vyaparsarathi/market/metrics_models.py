"""Phase 2B competition-metrics models (CLAUDE.md §11, §22).

Phase 2A answers *which* discovered businesses compete with the proposed
business. Phase 2B answers *how concentrated* that competition is around the
proposed location — counts, distances, distance bands, a raw spatial density,
and a transparent competition signal.

Nothing here judges viability. ``data_confidence`` is the Phase 1
data-coverage signal carried through unchanged (CLAUDE.md §22): how well the
local market can be observed, not whether the business will succeed.

All models are Pydantic v2 and JSON-serializable. They reference the Phase 1 /
Phase 2A models; they do not redefine a business or competitor model.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.market.metrics_config import CompetitionMetricsConfig
from vyaparsarathi.models.taxonomy import BusinessCategory


class CompetitionSignal(StrEnum):
    """Transparent, configurable competition-concentration label (STEP 6).

    Derived only from the direct-competitor count and the raw spatial density
    against documented thresholds. **Not** a validated market-saturation score.
    """

    NONE = "none"  # no direct competitors identified
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


class CompetitionMetricsStatus(StrEnum):
    OK = "ok"
    UNKNOWN_CATEGORY = "unknown_category"  # Phase 2A could not map the proposed business
    INVALID_RADIUS = "invalid_radius"  # analysis radius <= 0; density undefined


class DistanceStats(BaseModel):
    """Summary of competitor distances for one group (direct, or direct+adjacent).

    ``count`` counts only competitors with a usable distance; ``missing_distance``
    counts competitors in the group whose ``distance_m`` was ``None`` and which
    are therefore excluded from every statistic below.
    """

    model_config = ConfigDict(extra="forbid")

    unit: Literal["m"] = "m"
    count: int = 0
    missing_distance: int = 0
    nearest_m: float | None = None
    farthest_m: float | None = None
    mean_m: float | None = None
    median_m: float | None = None


class DistanceBand(BaseModel):
    """Cumulative competitor count within ``max_distance_m`` of the query point."""

    model_config = ConfigDict(extra="forbid")

    max_distance_m: int
    direct_count: int = 0
    relevant_count: int = 0  # direct + adjacent
    exceeds_radius: bool = False  # band is wider than the analysis radius


class CompetitionDensity(BaseModel):
    """Raw spatial density of competitors over the circular search area.

    This is competitors per km2 of *search area*, **not** population-normalized
    market density (competitors per 1,000 people) — that needs demand data and
    is Phase 2C (STEP 10).
    """

    model_config = ConfigDict(extra="forbid")

    unit: Literal["per_km2"] = "per_km2"
    catchment_area_km2: float | None = None  # None when the radius is invalid
    direct_per_km2: float | None = None
    relevant_per_km2: float | None = None
    note: str = (
        "Competitors per km2 of circular search area; NOT population-normalized "
        "market density (Phase 2C)."
    )


class CompetitionMetricsResult(BaseModel):
    """Phase 2B output. A deterministic, JSON-serializable summary of competition
    concentration for the proposed business at the resolved location."""

    model_config = ConfigDict(extra="forbid")

    status: CompetitionMetricsStatus

    # --- query context (echoed from Phase 1 / Phase 2A) ---
    location_text: str | None = None
    proposed_category: BusinessCategory
    proposed_subtypes: list[str] = Field(default_factory=list)
    analysis_radius_m: int
    query_latitude: float | None = None
    query_longitude: float | None = None

    # --- competitor counts (our calculation) ---
    direct_count: int = 0
    adjacent_count: int = 0
    total_relevant_count: int = 0

    # --- distance metrics (our calculation) ---
    direct_distance: DistanceStats = Field(default_factory=DistanceStats)
    relevant_distance: DistanceStats = Field(default_factory=DistanceStats)
    distance_bands: list[DistanceBand] = Field(default_factory=list)

    # --- spatial density (our calculation) ---
    density: CompetitionDensity = Field(default_factory=CompetitionDensity)

    # --- transparent competition signal (our calculation, MVP heuristic) ---
    signal: CompetitionSignal = CompetitionSignal.NONE
    signal_reason: str = ""
    signal_basis: dict[str, float | None] = Field(default_factory=dict)

    # --- data coverage (pass-through from Phase 1; NOT viability) ---
    data_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    data_confidence_note: str = (
        "Phase 1 data-coverage signal: how well the local market is observed, "
        "NOT business viability (CLAUDE.md §22)."
    )

    # --- config echo + provenance ---
    config: CompetitionMetricsConfig = Field(default_factory=CompetitionMetricsConfig)
    warnings: list[str] = Field(default_factory=list)
