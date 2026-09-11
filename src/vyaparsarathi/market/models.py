"""Phase 2A competitor-analysis models (CLAUDE.md §11).

All models are Pydantic v2 and JSON-serializable. A classified result keeps a
*reference* to the Phase 1 :class:`NormalizedBusiness` (the canonical record) —
it does not define a second business model.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.business import NormalizedBusiness
from vyaparsarathi.models.results import DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory


class Relationship(StrEnum):
    """How an existing business relates to the proposed one."""

    DIRECT = "direct"  # same primary customer need / offering
    ADJACENT = "adjacent"  # partial overlap / substitute
    IRRELEVANT = "irrelevant"  # different primary customer need
    UNKNOWN = "unknown"  # only used when the proposed category itself is unknown


class CompetitorAnalysisStatus(StrEnum):
    OK = "ok"
    UNKNOWN_CATEGORY = "unknown_category"  # proposed business could not be mapped


class ProposedBusiness(BaseModel):
    """The entrepreneur's proposed business, mapped onto the internal taxonomy.

    ``subtypes`` are lowercase product/keyword tokens (e.g. ``["pulses"]``) that
    refine an otherwise coarse ``category``. ``resolved`` is ``False`` when the
    input could not be mapped confidently — callers must then ask for
    clarification rather than proceed.
    """

    model_config = ConfigDict(extra="forbid")

    category: BusinessCategory
    subtypes: list[str] = Field(default_factory=list)
    raw_text: str | None = None
    resolved: bool = True
    note: str | None = None  # how it resolved, or why it did not
    # Populated only when `resolved` is False: the best 2-4 category
    # candidates a loosened fuzzy pass still suggests (either because
    # several conflicted, or because none cleared the strict auto-resolve
    # threshold/margin) — lets the conversation ask the entrepreneur to
    # pick one instead of silently falling through to UNKNOWN.
    candidate_categories: list[str] = Field(default_factory=list)


class ClassifiedCompetitor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business: NormalizedBusiness
    distance_m: float | None = None  # carried from the Phase 1 BusinessHit
    relationship: Relationship
    reason: str
    matched_subtypes: list[str] = Field(default_factory=list)


class CompetitorAnalysisResult(BaseModel):
    """Phase 2A output. Consumed by Phase 2B to compute competition metrics."""

    model_config = ConfigDict(extra="forbid")

    status: CompetitorAnalysisStatus
    proposed: ProposedBusiness
    location_text: str | None = None
    discovery_status: DiscoveryStatus | None = None

    direct_competitors: list[ClassifiedCompetitor] = Field(default_factory=list)
    adjacent_competitors: list[ClassifiedCompetitor] = Field(default_factory=list)
    irrelevant: list[ClassifiedCompetitor] = Field(default_factory=list)

    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
