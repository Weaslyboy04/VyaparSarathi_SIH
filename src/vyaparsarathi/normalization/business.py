"""Map a :class:`RawOsmElement` into a :class:`NormalizedBusiness`."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from vyaparsarathi.categories.osm_map import map_osm_tags
from vyaparsarathi.errors import NormalizationError
from vyaparsarathi.models.business import NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.normalization.text import normalize_name
from vyaparsarathi.sources.osm.models import RawOsmElement
from vyaparsarathi.utils.time import utcnow

# Tag keys that can carry a display name, best first.
_NAME_KEYS = ("name", "name:en", "int_name", "official_name", "brand", "operator")
# addr:* components assembled into a single address string, in order.
_ADDR_PARTS = (
    "addr:housenumber",
    "addr:street",
    "addr:suburb",
    "addr:village",
    "addr:city",
    "addr:district",
    "addr:state",
    "addr:postcode",
)


class NormalizedOsm(BaseModel):
    """A normalized business plus the unmapped tag hint (for logging)."""

    business: NormalizedBusiness
    unmapped_tag: str | None = None


def _pick_name(tags: dict[str, str]) -> str | None:
    for key in _NAME_KEYS:
        value = tags.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _build_address(tags: dict[str, str]) -> str | None:
    if tags.get("addr:full"):
        return tags["addr:full"].strip()
    parts = [tags[k].strip() for k in _ADDR_PARTS if tags.get(k)]
    return ", ".join(parts) if parts else None


def _data_quality(
    *, has_name: bool, category: BusinessCategory, has_address: bool, is_point: bool
) -> float:
    score = 0.15  # a usable coordinate is guaranteed by the adapter
    if has_name:
        score += 0.40
    if category is not BusinessCategory.UNKNOWN:
        score += 0.30
    if has_address:
        score += 0.15
    if not is_point:
        score -= 0.05  # way/relation centroid is less precise than a node
    return round(max(0.0, min(1.0, score)), 3)


def normalize_osm_element(element: RawOsmElement, *, now: datetime | None = None) -> NormalizedOsm:
    if not element.has_coordinates():
        raise NormalizationError(f"{element.source_id} has no coordinate")

    timestamp = now or utcnow()
    name = _pick_name(element.tags)
    category, matched_or_hint = map_osm_tags(element.tags)
    address = _build_address(element.tags)
    unmapped_tag = matched_or_hint if category is BusinessCategory.UNKNOWN else None

    business = NormalizedBusiness(
        name=name,
        normalized_name=normalize_name(name),
        category=category,
        latitude=float(element.latitude),  # type: ignore[arg-type]
        longitude=float(element.longitude),  # type: ignore[arg-type]
        address=address,
        source=SourceName.OSM,
        source_id=element.source_id,
        raw=element.raw,
        first_seen=timestamp,
        last_updated=timestamp,
        data_quality=_data_quality(
            has_name=name is not None,
            category=category,
            has_address=address is not None,
            is_point=element.element_type == "node",
        ),
        provenance=[
            ProvenanceEntry(
                source=SourceName.OSM,
                source_id=element.source_id,
                retrieved_at=timestamp,
            )
        ],
    )
    return NormalizedOsm(business=business, unmapped_tag=unmapped_tag)
