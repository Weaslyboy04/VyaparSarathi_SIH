"""Phase 2C demand-signal result models (CLAUDE.md §5, §11, §22).

Phase 2C answers *"is there evidence of local customer potential?"* — never
viability, opportunity, or a recommendation (Phase 2D+). ``demand_data_confidence``
measures how well the *evidence* is observed, never how much demand exists.

The result is grouped OBSERVED (source facts) vs CALCULATED (our arithmetic) —
a deliberate divergence from Phase 2B's flat shape — so the
source-fact / calculated-signal distinction (CLAUDE.md §5) is structural.
There is **no** demand-score field: adding one later is additive; shipping a fake
one now is not.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.market.demand_config import DemandConfig
from vyaparsarathi.models.demand import ActivityHit, ActivityKind, SettlementHit


class DemandStatus(StrEnum):
    OK = "ok"  # >=1 settlement in the catchment carries a population record
    NO_POPULATION_DATA = "no_population_data"  # no Census 2011 record for this area at all
    # Census 2011 population EXISTS for this area but no row could be placed on the
    # map (no village-boundary coordinates for the state) — distinct from "no data".
    POPULATION_NOT_GEOLOCATED = "population_not_geolocated"
    NO_SETTLEMENTS_FOUND = "no_settlements_found"  # no known settlement at all
    LOCATION_UNRESOLVED = "location_unresolved"  # no catchment centre from Phase 1
    SOURCE_UNAVAILABLE = "source_unavailable"  # every acquisition layer failed
    INVALID_RADIUS = "invalid_radius"  # radius <= 0


class CatchmentPopulation(BaseModel):
    """The census-code-unique population sum for the catchment. ``persons`` is
    ``None`` — never ``0`` — when no village-level figure is available."""

    model_config = ConfigDict(extra="forbid")

    unit: Literal["persons"] = "persons"
    persons: int | None = None
    households: int | None = None
    is_floor: bool | None = None  # True when coverage < 1.0
    settlements_found: int = 0
    settlements_with_population: int = 0
    population_coverage: float | None = Field(default=None, ge=0.0, le=1.0)
    duplicates_suppressed: list[str] = Field(default_factory=list)  # census codes counted once
    reference_years: list[int] = Field(default_factory=list)

    # Sensitivity of the all-or-nothing boundary rule.
    boundary_settlements: list[str] = Field(default_factory=list)
    boundary_persons: int | None = None
    boundary_share: float | None = Field(default=None, ge=0.0, le=1.0)

    catchment_area_km2: float | None = None
    density_persons_per_km2: float | None = None

    note: str = (
        "Sum of Census 2011 village figures for settlements whose centre lies in "
        "the radius, each census code counted once. A lower bound where coverage "
        "< 1.0. The all-or-nothing boundary rule's bias direction is indeterminate; "
        "see boundary_settlements."
    )


class ActivitySummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total_points: int = 0
    counts_by_kind: dict[ActivityKind, int] = Field(default_factory=dict)
    nearest_m_by_kind: dict[ActivityKind, float] = Field(default_factory=dict)
    note: str = (
        "Counts of public-service anchors within the radius. Proxies for settled "
        "population and daily activity; NOT a measure of purchasing demand."
    )


class DemandCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    signals_available: list[str] = Field(default_factory=list)
    signals_unavailable: list[str] = Field(default_factory=list)
    population_confidence: float | None = None
    settlement_confidence: float | None = None
    activity_confidence: float | None = None
    source_reference_years: dict[str, int] = Field(default_factory=dict)
    geography_mismatch: bool = False  # only district/state data was available
    census_extract_covers_query_area: bool = False
    # Population records exist for this area but carry no usable coordinate — the
    # third state, distinct from "available" and "unavailable".
    population_records_ungeolocated_in_area: int = 0
    population_available_not_geolocated: bool = False


class DemandSignalsResult(BaseModel):
    """Phase 2C output. Deterministic, JSON-serializable."""

    model_config = ConfigDict(extra="forbid")

    status: DemandStatus

    # --- query context ---
    location_text: str | None = None
    analysis_radius_m: int = 0
    query_latitude: float | None = None
    query_longitude: float | None = None
    state: str | None = None
    district: str | None = None

    # --- OBSERVED (source facts) ---
    settlements: list[SettlementHit] = Field(default_factory=list)
    activity_points: list[ActivityHit] = Field(default_factory=list)
    osm_tagged_population_total: int | None = None  # quarantined secondary signal

    # --- CALCULATED (our arithmetic) ---
    catchment: CatchmentPopulation = Field(default_factory=CatchmentPopulation)
    activity: ActivitySummary = Field(default_factory=ActivitySummary)
    competitors_per_1000_people: float | None = None
    competitors_per_1000_people_note: str = (
        "Direct competitors (Phase 2B) per 1,000 catchment residents. When the "
        "population is a floor this ratio is an UPPER bound on saturation."
    )

    # --- evidence quality, NOT demand magnitude ---
    demand_data_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    demand_data_confidence_note: str = (
        "Phase 1/2C data-coverage signal: how well the local demand picture is "
        "observed, NOT business viability (CLAUDE.md §22)."
    )
    demand_data_confidence_basis: dict[str, float | None] = Field(default_factory=dict)
    coverage: DemandCoverage = Field(default_factory=DemandCoverage)

    interpretation_guidance: list[str] = Field(default_factory=list)
    config: DemandConfig = Field(default_factory=DemandConfig)
    warnings: list[str] = Field(default_factory=list)
