"""Overpass source adapter: :class:`DiscoveryQuery` -> raw OSM elements.

Returns raw records + fetch statistics only. No normalization here (CLAUDE.md
§6.1, §7).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.osm.client import OverpassClient, build_overpass_ql
from vyaparsarathi.sources.osm.models import RawOsmElement
from vyaparsarathi.sources.osm.parse import parse_element
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class OverpassFetch(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    elements: list[RawOsmElement] = Field(default_factory=list)
    raw_count: int = 0
    dropped_no_coordinates: int = 0
    endpoint_used: str | None = None
    mirror_fallback_used: bool = False
    query_ql: str = ""


class OverpassSource:
    name = SourceName.OSM

    def __init__(
        self,
        settings: Settings | None = None,
        client: OverpassClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or OverpassClient(self._settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OverpassSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(self, query: DiscoveryQuery, selectors: list[tuple[str, str]]) -> OverpassFetch:
        ql = build_overpass_ql(
            selectors,
            query.latitude,
            query.longitude,
            query.radius_m,
            self._settings.overpass_query_timeout_s,
        )
        elements_raw, endpoint, fallback = self._client.run(ql)
        logger.info(
            "Overpass returned %d raw element(s) from %s%s",
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
            logger.info("dropped %d OSM element(s) with no usable coordinate", dropped)

        return OverpassFetch(
            elements=parsed,
            raw_count=len(elements_raw),
            dropped_no_coordinates=dropped,
            endpoint_used=endpoint,
            mirror_fallback_used=fallback,
            query_ql=ql,
        )
