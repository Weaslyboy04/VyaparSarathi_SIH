"""Business-data source adapters (CLAUDE.md §4, §6).

One adapter per source. Adapters return **raw source objects only** — mapping
into :class:`~vyaparsarathi.models.business.NormalizedBusiness` happens in
:mod:`vyaparsarathi.normalization`, so no source-specific shape crosses this
boundary. Phase 1 ships the OSM / Overpass adapter.
"""

from vyaparsarathi.sources.base import BusinessSource

__all__ = ["BusinessSource"]
