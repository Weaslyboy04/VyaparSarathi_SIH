"""Overpass element -> :class:`RawOsmElement` (CLAUDE.md §6.1, §33).

Shared by every Overpass adapter (business discovery and demand acquisition) so
the node-vs-way-``center`` coordinate handling lives in exactly one place.
"""

from __future__ import annotations

from vyaparsarathi.sources.osm.models import RawOsmElement


def parse_element(item: dict) -> RawOsmElement | None:
    """Parse one raw Overpass JSON element. Returns ``None`` when the element is
    not a node/way/relation with a usable id."""
    element_type = item.get("type")
    element_id = item.get("id")
    if element_type not in {"node", "way", "relation"} or not isinstance(element_id, int):
        return None

    if element_type == "node":
        lat = item.get("lat")
        lon = item.get("lon")
    else:  # way / relation -> use the `center` from `out center`
        center = item.get("center") or {}
        lat = center.get("lat")
        lon = center.get("lon")

    return RawOsmElement(
        element_type=element_type,
        element_id=element_id,
        latitude=float(lat) if isinstance(lat, (int, float)) else None,
        longitude=float(lon) if isinstance(lon, (int, float)) else None,
        tags={str(k): str(v) for k, v in (item.get("tags") or {}).items()},
        raw=item,
    )
