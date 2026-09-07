"""A pure Okapi BM25 scorer, stdlib only (CLAUDE.md §4.1, §19).

No embedding/vector dependency is installed in this project, and CLAUDE.md
§4.1 asks that a new one be justified before being added. At the corpus size
Phase 5 targets (tens of documents, low thousands of chunks — see
`docs/phase-5.md`'s "Known limitations"), a plain BM25 scan over the
metadata-filtered candidate set is fast enough to run per query rather than
maintain as a standing index; `knowledge/retrieval.py` calls this fresh for
every request.

This module is never imported by `knowledge/resolver.py` — retrieval quality
can only ever affect which passages are shown, never which value a
parameter resolves to (see `resolver.py`'s module docstring).
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping, Sequence


def bm25_scores(
    query_tokens: Sequence[str],
    documents: Mapping[str, Sequence[str]],
    *,
    k1: float,
    b: float,
) -> dict[str, float]:
    """Okapi BM25 score for every document in `documents` against
    `query_tokens`. `documents` maps an opaque id to its token list. Returns
    only ids with a strictly positive score — a document sharing no query
    term scores exactly 0 and is omitted, not returned as a tie at the
    bottom. Pure: no I/O, no clock, no randomness."""
    if not query_tokens or not documents:
        return {}

    n = len(documents)
    doc_lengths = {doc_id: len(tokens) for doc_id, tokens in documents.items()}
    avg_doc_len = sum(doc_lengths.values()) / n if n else 0.0

    term_doc_freq: Counter[str] = Counter()
    doc_term_counts: dict[str, Counter[str]] = {}
    for doc_id, tokens in documents.items():
        counts = Counter(tokens)
        doc_term_counts[doc_id] = counts
        for term in counts:
            term_doc_freq[term] += 1

    query_terms = set(query_tokens)
    idf: dict[str, float] = {}
    for term in query_terms:
        n_q = term_doc_freq.get(term, 0)
        # The "+1" (BM25+ / Lucene-style) smoothing keeps IDF non-negative
        # even for a term appearing in every document.
        idf[term] = math.log(1.0 + (n - n_q + 0.5) / (n_q + 0.5))

    scores: dict[str, float] = {}
    for doc_id, counts in doc_term_counts.items():
        doc_len = doc_lengths[doc_id]
        norm = 1.0 - b + b * (doc_len / avg_doc_len if avg_doc_len else 0.0)
        total = 0.0
        for term in query_terms:
            freq = counts.get(term, 0)
            if freq == 0:
                continue
            total += idf[term] * (freq * (k1 + 1.0)) / (freq + k1 * norm)
        if total > 0.0:
            scores[doc_id] = total
    return scores


__all__ = ["bm25_scores"]
