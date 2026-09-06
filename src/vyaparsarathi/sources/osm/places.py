"""Overpass fetch for Phase 2C demand acquisition: settlements + activity anchors.

Same data source as business discovery, different tag selectors — so this reuses
:func:`build_overpass_ql`, :class:`OverpassClient` (mirror fallback, retry, cache)
and :func:`parse_element` verbatim, and returns the same :class:`OverpassFetch`
container. It only issues the query; it does not normalize (CLAUDE.md §7).
"""

from __future__ import annotations

from collections.abc import Sequence

from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.client import OverpassClient, build_overpass_ql
from vyaparsarathi.sources.osm.models import RawOsmElement
from vyaparsarathi.sources.osm.parse import parse_element
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


def fetch_demand_elements(
    client: OverpassClient,
    *,
    latitude: float,
    longitude: float,
    radius_m: int,
    selectors: Sequence[tuple[str, str]],
    timeout_s: int,
) -> OverpassFetch:
    """Run the single demand union query and return raw elements + fetch stats.

    Raises :class:`~vyaparsarathi.errors.SourceUnavailableError` (from
    ``client.run``) only when every Overpass endpoint fails.
    """
    ql = build_overpass_ql(selectors, latitude, longitude, radius_m, timeout_s)
    elements_raw, endpoint, fallback = client.run(ql)
    logger.info(
        "Overpass (demand) returned %d raw element(s) from %s%s",
        len(elements_raw),
        endpoint,
        " (mirror fallback)" if fallback else "",
    )

    parsed: list[RawOsmElement] = []
    dropped = 0
    for item in elements_raw:
        element = parse_element(item)
        if element is None:
            continue
        if not element.has_coordinates():
            dropped += 1
            continue
        parsed.append(element)

    if dropped:
        logger.info("dropped %d demand element(s) with no usable coordinate", dropped)

    return OverpassFetch(
        elements=parsed,
        raw_count=len(elements_raw),
        dropped_no_coordinates=dropped,
        endpoint_used=endpoint,
        mirror_fallback_used=fallback,
        query_ql=ql,
    )
