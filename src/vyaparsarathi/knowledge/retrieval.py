"""Metadata-filtered lexical passage retrieval (CLAUDE.md §19).

`LexicalRetriever` implements `knowledge.base.Retriever`: it filters a
corpus's chunks by `RetrievalFilters` **first** (CLAUDE.md §19: "filtering
by metadata ... over pure nearest-neighbour"), then ranks the survivors with
BM25 (`knowledge/lexical.py`), with a `rapidfuzz` fallback — a dependency
already in the project (`normalization/text.py`, `dedup/deduplicator.py`) —
for transliteration/OCR variants when BM25 alone returns fewer than `limit`
hits. Ties are broken by `(-relevance, tier_rank, chunk_id)` so results are
byte-stable across repeated runs.

This module is never imported by `knowledge/resolver.py`, and it never
selects a `SourcedParameter` — it only ever returns `RetrievedPassage`
objects for display/citation (CLAUDE.md §18).
"""

from __future__ import annotations

from rapidfuzz import fuzz

from vyaparsarathi.knowledge.base import CorpusStore, RetrievalFilters
from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.lexical import bm25_scores
from vyaparsarathi.knowledge.tokenize import tokenize
from vyaparsarathi.models.knowledge import (
    DocumentChunk,
    DocumentRecord,
    JurisdictionLevel,
    RetrievedPassage,
    SourceTier,
)

# The same fixed authority ordering knowledge/resolver.py uses for
# precedence rule 1 — reused here only to order retrieval results and to
# apply RetrievalFilters.min_tier, never to select a value.
_TIER_AUTHORITY_ORDER: tuple[SourceTier, ...] = (
    SourceTier.GOVT_PRIMARY,
    SourceTier.REGULATOR,
    SourceTier.PUBLIC_SECTOR_INSTITUTION,
    SourceTier.INDUSTRY_BODY,
    SourceTier.SECONDARY,
)
_TIER_RANK: dict[SourceTier, int] = {t: i for i, t in enumerate(_TIER_AUTHORITY_ORDER)}


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def _jurisdiction_matches(doc: DocumentRecord, filters: RetrievalFilters) -> bool:
    jur = doc.jurisdiction
    if jur.level is JurisdictionLevel.NATIONAL:
        return True
    if filters.state is None:
        return True
    if _norm(jur.state) != _norm(filters.state):
        return False
    if jur.level is JurisdictionLevel.STATE:
        return True
    if filters.district is None:
        return True
    return _norm(jur.district) == _norm(filters.district)


def _tags_compatible(chunk_values: tuple[str, ...], filter_values: tuple[str, ...]) -> bool:
    """An unset side (empty tuple) never excludes; only an explicit
    disagreement (both non-empty, no overlap) does — the same permissive
    principle `knowledge/resolver.py`'s filters use."""
    if not chunk_values or not filter_values:
        return True
    return bool(set(chunk_values) & set(filter_values))


def _within_effective_window(doc: DocumentRecord, filters: RetrievalFilters) -> bool:
    if filters.as_of is None:
        return True
    if doc.effective_to is not None and filters.as_of > doc.effective_to:
        return False
    if doc.effective_from is not None and filters.as_of < doc.effective_from:
        return False
    return True


def _tier_meets_floor(doc: DocumentRecord, filters: RetrievalFilters) -> bool:
    if filters.min_tier is None:
        return True
    return _TIER_RANK.get(doc.tier, len(_TIER_AUTHORITY_ORDER)) <= _TIER_RANK.get(
        filters.min_tier, len(_TIER_AUTHORITY_ORDER)
    )


def _filter_chunks(
    chunks: list[DocumentChunk], documents: dict[str, DocumentRecord], filters: RetrievalFilters
) -> list[tuple[DocumentChunk, DocumentRecord]]:
    kept: list[tuple[DocumentChunk, DocumentRecord]] = []
    for chunk in chunks:
        doc = documents.get(chunk.document_id)
        if doc is None:
            continue
        if not _jurisdiction_matches(doc, filters):
            continue
        if not _tags_compatible(chunk.schemes, filters.schemes):
            continue
        if not _tags_compatible(chunk.categories, filters.categories):
            continue
        if not _tags_compatible(chunk.topics, filters.topics):
            continue
        if not _within_effective_window(doc, filters):
            continue
        if not _tier_meets_floor(doc, filters):
            continue
        kept.append((chunk, doc))
    return kept


def _citation(chunk: DocumentChunk, doc: DocumentRecord) -> str:
    return f"{doc.title} — {doc.publisher} ({chunk.locator.as_ref()})"


class LexicalRetriever:
    """Implements `knowledge.base.Retriever` over a `CorpusStore`. Rebuilds
    its BM25 candidate set fresh per query (no standing index) — see this
    module's docstring for why that is the right tradeoff at Phase 5's
    target corpus size."""

    def __init__(
        self, corpus: CorpusStore, *, cfg: KnowledgeConfig = DEFAULT_KNOWLEDGE_CONFIG
    ) -> None:
        self._corpus = corpus
        self._cfg = cfg

    def search(
        self, query_text: str, *, filters: RetrievalFilters, limit: int
    ) -> list[RetrievedPassage]:
        if limit <= 0 or not query_text.strip():
            return []

        documents = {d.document_id: d for d in self._corpus.documents()}
        candidates = _filter_chunks(list(self._corpus.chunks()), documents, filters)
        if not candidates:
            return []

        query_tokens = tokenize(query_text)
        chunk_tokens = {chunk.chunk_id: tokenize(chunk.text) for chunk, _ in candidates}
        scores = bm25_scores(query_tokens, chunk_tokens, k1=self._cfg.bm25_k1, b=self._cfg.bm25_b)

        by_id = {chunk.chunk_id: (chunk, doc) for chunk, doc in candidates}
        matched_ids = set(scores)

        passages: list[RetrievedPassage] = []
        for chunk_id, relevance in scores.items():
            chunk, doc = by_id[chunk_id]
            passages.append(
                RetrievedPassage(
                    chunk=chunk,
                    relevance=relevance,
                    matched_terms=tuple(sorted(set(query_tokens) & set(chunk_tokens[chunk_id]))),
                    tier=doc.tier,
                    citation=_citation(chunk, doc),
                )
            )
        passages.sort(key=lambda p: (-p.relevance, _TIER_RANK.get(p.tier, 99), p.chunk.chunk_id))

        if len(passages) < limit:
            passages.extend(self._fuzzy_fallback(query_text, candidates, matched_ids, limit))

        return passages[:limit]

    def _fuzzy_fallback(
        self,
        query_text: str,
        candidates: list[tuple[DocumentChunk, DocumentRecord]],
        already_matched: set[str],
        limit: int,
    ) -> list[RetrievedPassage]:
        """Catches transliteration/OCR variants BM25's exact-token overlap
        misses. Ranked strictly below every true BM25 hit (never mixes the
        two incompatible score scales) and sorted by chunk_id for
        determinism among ties."""
        query_lower = query_text.lower()
        found: list[RetrievedPassage] = []
        for chunk, doc in candidates:
            if chunk.chunk_id in already_matched:
                continue
            ratio = fuzz.token_set_ratio(query_lower, chunk.text.lower())
            if ratio >= self._cfg.fuzzy_fallback_min_ratio:
                found.append(
                    RetrievedPassage(
                        chunk=chunk,
                        relevance=ratio / 100.0,
                        matched_terms=(),
                        tier=doc.tier,
                        citation=_citation(chunk, doc),
                    )
                )
        found.sort(key=lambda p: (-p.relevance, p.chunk.chunk_id))
        return found[: max(0, limit - len(already_matched))]


__all__ = ["LexicalRetriever"]
