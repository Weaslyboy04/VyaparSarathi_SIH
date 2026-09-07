"""Phase 5 acquisition — the only knowledge layer that touches disk (CLAUDE.md
§18, §33). Never the network: the corpus is a committed, offline artifact set
(`sources/knowledge/loader.py`), ingested by an operator ahead of time (see
`data/knowledge/SOURCES.md`), not fetched live.

Turns a `ParameterQuery` plus a loaded
`~vyaparsarathi.knowledge.base.CorpusStore` into a
`FinanceKnowledgeEvidence` — the seam that crosses into the pure
`knowledge/resolver.py` and `knowledge/plan_binding.py`. Mirrors
`discovery/demand_acquisition.py` / `discovery/opportunity_acquisition.py`:
the clock is read exactly once, at the boundary
(`FinanceKnowledgeEvidence.acquired_at`), and any retrieval failure degrades
to `passages=[]` plus a warning rather than raising.
"""

from __future__ import annotations

from vyaparsarathi.knowledge.base import CorpusStore, RetrievalFilters, Retriever
from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.resolver import resolve_parameters
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence, ParameterQuery
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)


def acquire_finance_knowledge(
    query: ParameterQuery,
    *,
    corpus: CorpusStore,
    retriever: Retriever | None = None,
    retrieval_query_text: str = "",
    retrieval_limit: int = 0,
    cfg: KnowledgeConfig = DEFAULT_KNOWLEDGE_CONFIG,
    clock: Clock = utcnow,
) -> FinanceKnowledgeEvidence:
    """Resolve every name in `query` against `corpus`, and (when a retriever
    and non-empty query text are supplied) gather supporting passages for
    display/citation. Never raises past this boundary; a corpus that is
    absent or empty still returns a well-formed `FinanceKnowledgeEvidence`
    with every resolution `NO_EVIDENCE` — the same "degrade, never crash"
    contract as `sources/knowledge/loader.py::FileCorpusStore`.

    Resolution never depends on retrieval succeeding or even running: the
    resolutions list is built first, from `corpus` alone, so a retriever
    failure can only ever shrink `passages`, never change a resolved value
    (CLAUDE.md §18, §30 — the type-level guarantee `knowledge/resolver.py`
    documents)."""
    documents = {d.document_id: d for d in corpus.documents()}
    resolutions = resolve_parameters(query, corpus.parameters(), documents, cfg=cfg)

    warnings: list[str] = []
    report = corpus.report()
    if not report.corpus_present:
        warnings.append(
            "no knowledge corpus is configured; every parameter in this query is "
            "reported NO_EVIDENCE, not an assumed value"
        )

    passages = []
    if retriever is not None and retrieval_query_text and retrieval_limit > 0:
        try:
            filters = RetrievalFilters(
                state=query.state,
                district=query.district,
                schemes=(query.scheme,) if query.scheme else (),
                categories=(query.category,) if query.category else (),
                as_of=query.as_of,
            )
            passages = retriever.search(
                retrieval_query_text, filters=filters, limit=retrieval_limit
            )
        except Exception as exc:  # noqa: BLE001 — a retriever failure degrades, never crashes
            logger.warning("passage retrieval failed: %s", exc)
            warnings.append(f"passage retrieval failed: {exc}")

    return FinanceKnowledgeEvidence(
        query=query,
        resolutions=resolutions,
        passages=passages,
        acquisition=report,
        warnings=warnings,
        acquired_at=clock(),
    )


__all__ = ["acquire_finance_knowledge"]
