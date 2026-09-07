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


class FinancialInputError(VyaparError):
    """A caller passed a malformed financial input (CLAUDE.md §4.2, §15) — e.g.
    a `float` where a money or rate field requires an exact `Decimal`. This is
    a programming error, not a data-quality gap: the pure financial engine
    otherwise never raises across its boundary (a missing driver is reported
    as `INSUFFICIENT_FINANCIAL_EVIDENCE`, not an exception)."""


class LlmUnavailableError(VyaparError):
    """The LLM provider could not be reached after exhausting retries, or a
    `ScriptedLlmProvider` ran out of canned responses (CLAUDE.md §25 Phase
    6). Caught by `llm/orchestrator.py`, logged, appended as a session
    warning, and the deterministic path continues — never escapes past the
    orchestrator (mirrors `SourceUnavailableError`)."""


class LlmPayloadError(VyaparError):
    """The LLM's response could not be parsed into a structured
    `TurnUnderstanding` / `NarrativeDraft` after the one bounded repair
    attempt (CLAUDE.md §25 Phase 6). Caught by `llm/orchestrator.py`; the
    conversation falls back to a deterministic question or the template
    renderer — it never stalls (mirrors `SourcePayloadError`)."""


class KnowledgeCorpusError(VyaparError):
    """The Phase 5 knowledge-corpus build/verify tools (``scripts/
    build_knowledge_corpus.py``, ``scripts/build_parameter_registry.py
    --verify``) found the corpus internally inconsistent — e.g. a committed
    row whose ``evidence_quote`` no longer matches its cited chunk, or a
    tampered ``text_sha256``. ETL-only: it never crosses the request seam.
    ``sources/knowledge/loader.py`` never raises this (or anything else) at
    request time — a bad row there is dropped and counted in
    `KnowledgeAcquisitionReport`, exactly like `SourceUnavailableError` is
    never raised past `discovery/service.py`."""
