"""Protocol contracts for the Phase 5 corpus and retriever (CLAUDE.md §3.6,
§18, §19).

`CorpusStore` and `Retriever` are ``typing.Protocol``\\ s, not ABCs — matching
`sources/base.py::BusinessSource`, `database/repository.py::BusinessRepository`
and `geocoding/base.py::Geocoder`: any implementation with the right shape
satisfies the contract, so `sources/knowledge/loader.py::FileCorpusStore` can
later be joined or replaced by a Postgres-backed store, and
`knowledge/retrieval.py::LexicalRetriever` by a vector-backed one, without
touching a caller.

There is deliberately **no** `ParameterResolver` protocol here: resolving a
parameter is one fixed, deterministic precedence ladder
(`knowledge/resolver.py::resolve_parameters`), not something with multiple
legitimate implementations the way a corpus backend or a retrieval strategy
is — so it stays a plain function, in the same style as
`market/opportunity.py::score_opportunities` or
`finance/assessment.py::assess_financials`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.models.knowledge import (
    DocumentChunk,
    DocumentRecord,
    KnowledgeAcquisitionReport,
    KnowledgeTopic,
    RetrievedPassage,
    SourceTier,
)
from vyaparsarathi.models.parameters import SourcedParameter
from vyaparsarathi.models.taxonomy import BusinessCategory


class CorpusStore(Protocol):
    """Read-only access to the committed knowledge corpus. Implementations
    load once and degrade — an empty result plus a report — rather than raise
    (CLAUDE.md §6.1, §33; see `sources/knowledge/loader.py::FileCorpusStore`)."""

    def documents(self) -> Sequence[DocumentRecord]: ...

    def chunks(self) -> Sequence[DocumentChunk]: ...

    def parameters(self) -> Sequence[SourcedParameter]: ...

    def document(self, document_id: str) -> DocumentRecord | None: ...

    def chunk(self, chunk_id: str) -> DocumentChunk | None: ...

    def report(self) -> KnowledgeAcquisitionReport: ...


class RetrievalFilters(BaseModel):
    """The metadata prefilter `knowledge/retrieval.py` applies before any
    lexical scoring runs (CLAUDE.md §19: "filtering by metadata ... over pure
    nearest-neighbour"). Every field is optional; an unset field never
    excludes a passage on that axis."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str | None = None
    district: str | None = None
    schemes: tuple[str, ...] = ()
    categories: tuple[BusinessCategory, ...] = ()
    topics: tuple[KnowledgeTopic, ...] = ()
    min_tier: SourceTier | None = None  # a chunk's own documents' tier, when known
    as_of: date | None = None


class Retriever(Protocol):
    """Supplies passages for display/citation only — never the mechanism by
    which a number reaches a calculation (CLAUDE.md §18). A retriever ranking
    a passage poorly can degrade what a caller sees alongside a resolved
    parameter; it can never change the parameter's value, because
    `knowledge/resolver.py::resolve_parameters` never calls this protocol."""

    def search(
        self, query_text: str, *, filters: RetrievalFilters, limit: int
    ) -> list[RetrievedPassage]: ...


__all__ = ["CorpusStore", "RetrievalFilters", "Retriever"]
