"""Local-demand domain models (CLAUDE.md §5, §7, §11, §22).

Phase 2C evidence. A :class:`Settlement` is the settlement analogue of
:class:`~vyaparsarathi.models.business.NormalizedBusiness`; a
:class:`PopulationRecord` is one sourced population figure with its provenance.
:class:`DemandEvidence` is the single object that crosses the
acquisition -> engine seam: it must stay JSON round-trippable and must carry no
source-specific vocabulary (no OSM tags, no census column names).

``distance_m`` lives on the ``*Hit`` wrappers, never on the entity itself — it is
relative to a query point, not a property of the settlement (CLAUDE.md §7, §30).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.business import ProvenanceEntry
from vyaparsarathi.models.taxonomy import SourceName


class SettlementType(StrEnum):
    """Coarse settlement class, mapped from OSM ``place=*`` (CLAUDE.md §8)."""

    CITY = "city"
    TOWN = "town"
    VILLAGE = "village"
    HAMLET = "hamlet"
    SUBURB = "suburb"
    NEIGHBOURHOOD = "neighbourhood"
    UNKNOWN = "unknown"


class ActivityKind(StrEnum):
    """Public-service activity anchors — MVP is deliberately four kinds only.

    These are proxies for settled population and daily footfall, sited by
    administrative norms rather than by commerce. They are **not** a measure of
    purchasing demand (CLAUDE.md §11, STEP 6).
    """

    SCHOOL = "school"
    MARKETPLACE = "marketplace"  # market / mandi / weekly haat
    BANK = "bank"  # bank branch or ATM
    TRANSPORT_STOP = "transport_stop"  # bus stop/station or railway station
    UNKNOWN = "unknown"


class GeographyLevel(StrEnum):
    """The geography a figure describes. Only ``VILLAGE`` counts as local demand;
    ``DISTRICT`` / ``STATE`` figures are context and are never substituted for a
    local estimate (CLAUDE.md §11, STEP 10)."""

    SETTLEMENT = "settlement"
    VILLAGE = "village"
    SUBDISTRICT = "subdistrict"
    DISTRICT = "district"
    STATE = "state"


class PopulationRecord(BaseModel):
    """One sourced population figure. ``persons`` and ``households`` are tracked
    separately and never blended into a single number (CLAUDE.md §13, §14)."""

    model_config = ConfigDict(extra="forbid")

    persons: int | None = Field(default=None, ge=0)
    households: int | None = Field(default=None, ge=0)
    geography_level: GeographyLevel
    dataset: str  # e.g. "census_2011_pca_village"
    reference_year: int | None = None  # the year the DATA describes, not retrieval
    provenance: ProvenanceEntry
    quality: float = Field(ge=0.0, le=1.0)  # source-tier signal


class Settlement(BaseModel):
    """A source-agnostic settlement record. Every source maps into exactly this."""

    model_config = ConfigDict(extra="forbid")

    internal_id: UUID = Field(default_factory=uuid4)
    name: str | None
    normalized_name: str
    place_type: SettlementType
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)

    # Authoritative identity when known — also the catchment-sum uniqueness key.
    census_code: str | None = None

    population: PopulationRecord | None = None
    # OSM `population=*` tag: quarantined lower-tier signal. NEVER enters the
    # catchment total; surfaced separately so its provenance stays distinct.
    osm_tagged_population: int | None = Field(default=None, ge=0)

    provenance: list[ProvenanceEntry] = Field(default_factory=list)
    data_quality: float = Field(ge=0.0, le=1.0)


class SettlementHit(BaseModel):
    """A settlement plus its distance from the current query point."""

    model_config = ConfigDict(extra="forbid")

    settlement: Settlement
    distance_m: float = Field(ge=0.0)


class ActivityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ActivityKind
    name: str | None = None
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    source: SourceName
    source_id: str


class ActivityHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activity_point: ActivityPoint
    distance_m: float = Field(ge=0.0)


class DemandAcquisitionReport(BaseModel):
    """What happened while gathering the evidence — the coverage analogue of
    :class:`~vyaparsarathi.models.results.SourceCoverage`."""

    model_config = ConfigDict(extra="forbid")

    osm_endpoint_used: str | None = None
    osm_mirror_fallback_used: bool = False
    osm_elements_raw: int = 0
    osm_settlements_parsed: int = 0
    osm_activity_parsed: int = 0
    osm_dropped_no_coordinates: int = 0
    unmapped_place_tags: dict[str, int] = Field(default_factory=dict)

    census_file_present: bool = False
    census_rows_in_extract: int = 0
    census_rows_in_radius: int = 0  # geolocated rows whose point is in the radius
    # Rows with population for the query's state/district that carry no usable
    # coordinate: "population data available but not geographically matchable".
    census_population_ungeolocated_in_area: int = 0
    census_states_in_extract: list[str] = Field(default_factory=list)
    census_extract_covers_query_area: bool = False

    # Typed acquisition exceptions, stringified — they cross the seam as data,
    # never as raised exceptions (CLAUDE.md §33).
    errors: list[str] = Field(default_factory=list)


class DemandEvidence(BaseModel):
    """The acquisition -> engine seam. Pure input to
    :func:`vyaparsarathi.market.demand.compute_demand_signals`."""

    model_config = ConfigDict(extra="forbid")

    latitude: float | None = None
    longitude: float | None = None
    radius_m: int = 0
    location_text: str | None = None
    state: str | None = None
    district: str | None = None

    settlements: list[SettlementHit] = Field(default_factory=list)
    activity_points: list[ActivityHit] = Field(default_factory=list)

    acquisition: DemandAcquisitionReport = Field(default_factory=DemandAcquisitionReport)
    warnings: list[str] = Field(default_factory=list)

    # The only wall-clock read in the whole phase, captured once at the boundary
    # so the engine stays deterministic (CLAUDE.md §28).
    acquired_at: datetime
