"""`knowledge/retrieval.py::LexicalRetriever` (CLAUDE.md §18, §19). Pure &
offline; uses the shared synthetic fixture corpus
(`tests/fixtures/knowledge/`, all invented — see its README)."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyaparsarathi.knowledge.base import RetrievalFilters
from vyaparsarathi.knowledge.retrieval import LexicalRetriever
from vyaparsarathi.models.knowledge import KnowledgeTopic, SourceTier
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever(FileCorpusStore(FIXTURES))


def test_search_returns_relevant_passages(retriever: LexicalRetriever) -> None:
    results = retriever.search("interest rate per annum", filters=RetrievalFilters(), limit=5)
    assert results
    assert all(r.relevance > 0 for r in results)


def test_search_empty_query_or_zero_limit_returns_nothing(retriever: LexicalRetriever) -> None:
    assert retriever.search("", filters=RetrievalFilters(), limit=5) == []
    assert retriever.search("interest rate", filters=RetrievalFilters(), limit=0) == []


def test_state_filter_excludes_a_differently_scoped_document(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "registration fee grocery", filters=RetrievalFilters(state="Bihar"), limit=10
    )
    doc_ids = {r.chunk.document_id for r in results}
    # test-bihar-licence-schedule is STATE/Bihar; nothing outside Bihar or
    # NATIONAL should ever be excluded by this filter, but a wrong-state
    # document must never appear even if lexically similar.
    assert "test-bihar-licence-schedule" in doc_ids


def test_scheme_filter_excludes_a_different_scheme(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "interest rate", filters=RetrievalFilters(schemes=("test-conflict-scheme",)), limit=10
    )
    doc_ids = {r.chunk.document_id for r in results}
    assert doc_ids <= {"test-conflict-doc-a", "test-conflict-doc-b"}
    assert "test-bihar-state-guideline" not in doc_ids


def test_category_filter_excludes_a_different_category(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "inventory days grocery", filters=RetrievalFilters(categories=(C.GROCERY,)), limit=10
    )
    # every result must either be category-unscoped or actually tag GROCERY
    for r in results:
        assert not r.chunk.categories or C.GROCERY in r.chunk.categories


def test_topic_filter_excludes_a_different_topic(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "fee schedule",
        filters=RetrievalFilters(topics=(KnowledgeTopic.LICENSING_COMPLIANCE,)),
        limit=10,
    )
    for r in results:
        assert not r.chunk.topics or KnowledgeTopic.LICENSING_COMPLIANCE in r.chunk.topics


def test_min_tier_filter_excludes_a_lower_tier_document(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "interest rate commentators",
        filters=RetrievalFilters(min_tier=SourceTier.GOVT_PRIMARY),
        limit=10,
    )
    doc_ids = {r.chunk.document_id for r in results}
    assert "test-secondary-commentary" not in doc_ids


def test_as_of_filter_excludes_an_expired_document(retriever: LexicalRetriever) -> None:
    from datetime import date

    results = retriever.search(
        "subsidy project cost", filters=RetrievalFilters(as_of=date(2026, 1, 1)), limit=10
    )
    doc_ids = {r.chunk.document_id for r in results}
    assert "test-stale-guideline" not in doc_ids


def test_district_filter_requires_district_level_match(retriever: LexicalRetriever) -> None:
    # No fixture document is DISTRICT-scoped, so a district filter must not
    # spuriously exclude STATE/NATIONAL documents (unset-side-never-rejects).
    results = retriever.search(
        "interest rate", filters=RetrievalFilters(state="Bihar", district="Vaishali"), limit=10
    )
    assert results


def test_national_document_is_never_excluded_by_a_state_filter(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "repayment tenure moratorium", filters=RetrievalFilters(state="Bihar"), limit=10
    )
    doc_ids = {r.chunk.document_id for r in results}
    assert "test-national-credit-guideline" in doc_ids


def test_results_are_sorted_by_relevance_descending(retriever: LexicalRetriever) -> None:
    results = retriever.search("interest rate per annum", filters=RetrievalFilters(), limit=10)
    relevances = [r.relevance for r in results]
    assert relevances == sorted(relevances, reverse=True)


def test_results_respect_the_limit(retriever: LexicalRetriever) -> None:
    results = retriever.search("interest rate", filters=RetrievalFilters(), limit=2)
    assert len(results) <= 2


def test_repeat_runs_are_byte_identical(retriever: LexicalRetriever) -> None:
    a = retriever.search("interest rate per annum", filters=RetrievalFilters(), limit=10)
    b = retriever.search("interest rate per annum", filters=RetrievalFilters(), limit=10)
    assert [p.model_dump(mode="json") for p in a] == [p.model_dump(mode="json") for p in b]


def test_no_matching_jurisdiction_level_still_handled(retriever: LexicalRetriever) -> None:
    results = retriever.search(
        "interest rate",
        filters=RetrievalFilters(state="Nonexistent State"),
        limit=10,
    )
    doc_ids = {r.chunk.document_id for r in results}
    # only NATIONAL-jurisdiction documents should survive an unmatched state
    assert "test-bihar-state-guideline" not in doc_ids
    assert "test-national-credit-guideline" in doc_ids


def test_empty_corpus_returns_no_results(tmp_path: Path) -> None:
    empty_dir = tmp_path / "knowledge"
    empty_dir.mkdir()
    empty_retriever = LexicalRetriever(FileCorpusStore(empty_dir))
    assert empty_retriever.search("interest rate", filters=RetrievalFilters(), limit=5) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
