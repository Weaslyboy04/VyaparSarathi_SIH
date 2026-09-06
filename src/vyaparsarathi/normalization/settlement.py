"""Map raw sources into the Phase 2C domain models (CLAUDE.md §7).

The one place OSM tag names and census column names are turned into
:class:`Settlement` / :class:`ActivityPoint`. Nothing source-specific crosses
out of here.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from vyaparsarathi.categories.osm_place_tags import map_activity_tags, map_place_tags
from vyaparsarathi.models.business import ProvenanceEntry
from vyaparsarathi.models.demand import (
    ActivityPoint,
    GeographyLevel,
    PopulationRecord,
    Settlement,
    SettlementType,
)
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.normalization.text import normalize_name
from vyaparsarathi.sources.census.loader import CensusVillageRow
from vyaparsarathi.sources.osm.models import RawOsmElement
from vyaparsarathi.utils.time import utcnow

_NAME_KEYS = ("name", "name:en", "int_name", "official_name")

# Provenance quality tiers (CLAUDE.md §19: authoritative sources rank higher).
CENSUS_TIER_QUALITY = 0.85  # a complete enumeration, but 2011


class NormalizedSettlement(BaseModel):
    """A settlement plus the unmapped ``place=*`` value seen (for logging)."""

    settlement: Settlement
    unmapped_tag: str | None = None


def _pick_name(tags: dict[str, str]) -> str | None:
    for key in _NAME_KEYS:
        value = tags.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _osm_tagged_population(tags: dict[str, str]) -> int | None:
    raw = tags.get("population")
    if raw is None:
        return None
    digits = raw.replace(",", "").strip()
    if digits.isdigit():
        return int(digits)
    return None


def _settlement_quality(*, has_name: bool, place_type: SettlementType, is_point: bool) -> float:
    score = 0.2  # a usable coordinate
    if has_name:
        score += 0.4
    if place_type is not SettlementType.UNKNOWN:
        score += 0.2
    if not is_point:
        score -= 0.05
    return round(max(0.0, min(1.0, score)), 3)


def normalize_settlement(
    element: RawOsmElement, *, now: datetime | None = None
) -> NormalizedSettlement | None:
    """Return a :class:`NormalizedSettlement` for a habitation ``place=*``
    element, or ``None`` when the element is not settlement we model."""
    if not element.has_coordinates():
        return None
    place_type, hint = map_place_tags(element.tags)
    if place_type is None:
        return None

    timestamp = now or utcnow()
    name = _pick_name(element.tags)
    settlement = Settlement(
        name=name,
        normalized_name=normalize_name(name),
        place_type=place_type,
        latitude=float(element.latitude),  # type: ignore[arg-type]
        longitude=float(element.longitude),  # type: ignore[arg-type]
        census_code=None,
        population=None,
        osm_tagged_population=_osm_tagged_population(element.tags),
        provenance=[
            ProvenanceEntry(
                source=SourceName.OSM,
                source_id=element.source_id,
                retrieved_at=timestamp,
            )
        ],
        data_quality=_settlement_quality(
            has_name=name is not None,
            place_type=place_type,
            is_point=element.element_type == "node",
        ),
    )
    unmapped = hint if place_type is SettlementType.UNKNOWN else None
    return NormalizedSettlement(settlement=settlement, unmapped_tag=unmapped)


def normalize_activity(element: RawOsmElement) -> ActivityPoint | None:
    """Return an :class:`ActivityPoint` for one of the four anchor kinds, else
    ``None``."""
    if not element.has_coordinates():
        return None
    kind, _hint = map_activity_tags(element.tags)
    if kind is None:
        return None
    return ActivityPoint(
        kind=kind,
        name=_pick_name(element.tags),
        latitude=float(element.latitude),  # type: ignore[arg-type]
        longitude=float(element.longitude),  # type: ignore[arg-type]
        source=SourceName.OSM,
        source_id=element.source_id,
    )


def settlement_from_census_row(row: CensusVillageRow, *, retrieved_at: datetime) -> Settlement:
    """Build a :class:`Settlement` from a **geolocated** census extract row
    (``coordinate_status == "matched"``). ``source = GOVT``; dataset identity is
    carried on the :class:`PopulationRecord` (CLAUDE.md §23)."""
    if row.latitude is None or row.longitude is None:
        raise ValueError(f"census row {row.census_code} has no usable coordinate")
    provenance = ProvenanceEntry(
        source=SourceName.GOVT,
        source_id=row.census_code,
        retrieved_at=retrieved_at,
    )
    population = PopulationRecord(
        persons=row.persons,
        households=row.households,
        geography_level=GeographyLevel.VILLAGE,
        dataset=row.dataset,
        reference_year=row.reference_year,
        provenance=provenance,
        quality=CENSUS_TIER_QUALITY,
    )
    return Settlement(
        name=row.name,
        normalized_name=normalize_name(row.name),
        place_type=SettlementType.VILLAGE,
        latitude=row.latitude,
        longitude=row.longitude,
        census_code=row.census_code,
        population=population,
        osm_tagged_population=None,
        provenance=[provenance],
        data_quality=CENSUS_TIER_QUALITY,
    )
