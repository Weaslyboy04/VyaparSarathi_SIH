"""Protocol every business-data source adapter implements (CLAUDE.md §4, §6)."""

from __future__ import annotations

from typing import Protocol

from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.taxonomy import SourceName


class BusinessSource(Protocol):
    name: SourceName

    def fetch(self, query: DiscoveryQuery, selectors: list[tuple[str, str]]) -> object:
        """Return a source-specific fetch result (raw records + fetch stats).

        Must not raise for "zero results"; must raise a
        :class:`~vyaparsarathi.errors.SourceUnavailableError` /
        :class:`~vyaparsarathi.errors.SourcePayloadError` when the source could
        not be reached or understood.
        """
        ...
