"""Normalized domain models and the internal taxonomy (CLAUDE.md §5, §7, §8).

These Pydantic models are the *only* contract between components. Source-specific
shapes (OSM tags, Nominatim payloads) must be mapped into these before crossing a
layer boundary.
"""

from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.place import PlaceCandidate, ResolvedPlace
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import (
    CoverageSummary,
    DiscoveryResult,
    DiscoveryStatus,
    SourceCoverage,
)
from vyaparsarathi.models.taxonomy import (
    CATEGORY_COMPATIBILITY,
    BusinessCategory,
    SourceName,
    categories_compatible,
)

__all__ = [
    "BusinessCategory",
    "SourceName",
    "CATEGORY_COMPATIBILITY",
    "categories_compatible",
    "ProvenanceEntry",
    "NormalizedBusiness",
    "BusinessHit",
    "PlaceCandidate",
    "ResolvedPlace",
    "DiscoveryQuery",
    "SourceCoverage",
    "CoverageSummary",
    "DiscoveryResult",
    "DiscoveryStatus",
]
