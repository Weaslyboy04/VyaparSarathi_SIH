"""Discovery output containers (CLAUDE.md §22, §26.1).

``confidence`` is a **data-coverage** signal (how well we can see the local
market), never a viability judgement (CLAUDE.md §22).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.business import BusinessHit
from vyaparsarathi.models.place import PlaceCandidate, ResolvedPlace
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName


class DiscoveryStatus(StrEnum):
    OK = "ok"
    NO_RESULTS = "no_results"  # location resolved, sources returned nothing usable
    LOCATION_AMBIGUOUS = "location_ambiguous"  # caller must pick from `candidates`
    LOCATION_NOT_FOUND = "location_not_found"
    SOURCE_UNAVAILABLE = "source_unavailable"  # all sources / mirrors failed


class SourceCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: SourceName
    endpoint_used: str | None = None
    mirror_fallback_used: bool = False
    raw_records: int = 0
    normalized: int = 0
    dropped_no_coordinates: int = 0
    dropped_other: int = 0
    # "shop=foo" -> count, for taxonomy expansion (CLAUDE.md §8).
    unmapped_tags: dict[str, int] = Field(default_factory=dict)


class CoverageSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    per_source: list[SourceCoverage] = Field(default_factory=list)
    total_before_dedup: int = 0
    total_after_dedup: int = 0
    duplicates_merged: int = 0
    uncertain_pairs: int = 0


class DiscoveryResult(BaseModel):
    """What a Phase 1 discovery run returns. Always returned; never raises past
    the service boundary (CLAUDE.md §6.1, §26.1)."""

    model_config = ConfigDict(extra="forbid")

    status: DiscoveryStatus
    query_text: str
    category: BusinessCategory
    requested_radius_m: int

    query: DiscoveryQuery | None = None
    resolved_place: ResolvedPlace | None = None
    candidates: list[PlaceCandidate] = Field(default_factory=list)

    businesses: list[BusinessHit] = Field(default_factory=list)
    sources_queried: list[SourceName] = Field(default_factory=list)
    coverage: CoverageSummary = Field(default_factory=CoverageSummary)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
