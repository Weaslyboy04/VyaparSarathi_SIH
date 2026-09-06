"""The normalized business model (CLAUDE.md §7) and query-relative wrapper."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.utils.time import utcnow


class ProvenanceEntry(BaseModel):
    """One contributing observation of a business (CLAUDE.md §3.4, §23).

    A merged record carries more than one of these; originals are never lost.
    """

    model_config = ConfigDict(extra="forbid")

    source: SourceName
    source_id: str  # e.g. "node/123456789"
    retrieved_at: datetime


class NormalizedBusiness(BaseModel):
    """Source-agnostic business record. Every source maps into exactly this.

    ``distance_m`` is intentionally absent: it is relative to a query point, not
    a property of the business, and lives on :class:`BusinessHit` (CLAUDE.md §7,
    §30).
    """

    model_config = ConfigDict(extra="forbid")

    internal_id: UUID = Field(default_factory=uuid4)
    name: str | None
    normalized_name: str
    category: BusinessCategory
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    address: str | None = None

    source: SourceName
    source_id: str
    raw: dict = Field(default_factory=dict, repr=False)

    first_seen: datetime = Field(default_factory=utcnow)
    last_updated: datetime = Field(default_factory=utcnow)

    data_quality: float = Field(ge=0.0, le=1.0)
    provenance: list[ProvenanceEntry] = Field(default_factory=list)

    @property
    def source_count(self) -> int:
        return len({(p.source, p.source_id) for p in self.provenance}) or 1


class BusinessHit(BaseModel):
    """A business plus its distance from the current discovery query point."""

    model_config = ConfigDict(extra="forbid")

    business: NormalizedBusiness
    distance_m: float = Field(ge=0.0)
