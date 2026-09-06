"""Typed exceptions (CLAUDE.md §33).

The discovery orchestrator catches these, records a warning, and returns a
degraded :class:`DiscoveryResult` — it never crashes and never fabricates data.
"""

from __future__ import annotations

from collections.abc import Sequence


class VyaparError(Exception):
    """Base class for all project errors."""


class ConfigError(VyaparError):
    """Invalid or missing configuration."""


class HttpError(VyaparError):
    """An outbound HTTP request failed after exhausting retries."""


class GeocodingError(VyaparError):
    """The location text could not be resolved to coordinates."""


class LocationAmbiguousError(GeocodingError):
    """Multiple plausible places matched; the caller must disambiguate.

    Carries the candidate list so the CLI / caller can present choices instead
    of silently picking one (CLAUDE.md §2 step 2, §26.1).
    """

    def __init__(self, query: str, candidates: Sequence[object]) -> None:
        super().__init__(f"Location {query!r} is ambiguous: {len(candidates)} candidates matched.")
        self.query = query
        self.candidates = list(candidates)


class SourceUnavailableError(VyaparError):
    """A business-data source (e.g. Overpass) could not be reached."""


class SourcePayloadError(VyaparError):
    """A source returned a response we could not parse."""


class NormalizationError(VyaparError):
    """A raw source record could not be mapped into the internal model."""
