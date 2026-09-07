"""`knowledge/lexical.py::bm25_scores` + `knowledge/tokenize.py::tokenize`
(CLAUDE.md §4.1, §19). Pure & offline; no vector/embedding dependency."""

from __future__ import annotations

import pytest

from vyaparsarathi.knowledge.lexical import bm25_scores
from vyaparsarathi.knowledge.tokenize import tokenize


def test_tokenize_lowercases_strips_punctuation_and_drops_stopwords() -> None:
    tokens = tokenize("The Rate of Interest, per annum!")
    assert "the" not in tokens
    assert "of" not in tokens
    assert "per" not in tokens
    assert "rate" in tokens
    assert "interest" in tokens
    assert "annum" in tokens


def test_tokenize_folds_non_ascii() -> None:
    tokens = tokenize("Bhagwanpur café")
    assert all(t.isascii() for t in tokens)


def test_tokenize_empty_input_returns_empty_list() -> None:
    assert tokenize("") == []
    assert tokenize(None) == []  # type: ignore[arg-type]


def test_bm25_scores_ranks_more_relevant_documents_higher() -> None:
    documents = {
        "doc-a": tokenize("The rate of interest is 10.5 percent per annum in Bihar."),
        "doc-b": tokenize("This document discusses unrelated agricultural subsidy matters."),
        "doc-c": tokenize("Interest rate interest rate interest rate per annum."),
    }
    scores = bm25_scores(tokenize("interest rate per annum"), documents, k1=1.5, b=0.75)
    assert "doc-b" not in scores
    assert scores["doc-c"] > scores["doc-a"]


def test_bm25_scores_empty_query_or_corpus_returns_empty() -> None:
    assert bm25_scores([], {"doc-a": ["a", "b"]}, k1=1.5, b=0.75) == {}
    assert bm25_scores(["a"], {}, k1=1.5, b=0.75) == {}


def test_bm25_scores_document_sharing_no_term_is_omitted_not_zero() -> None:
    documents = {"doc-a": ["interest", "rate"], "doc-b": ["unrelated", "words"]}
    scores = bm25_scores(["interest"], documents, k1=1.5, b=0.75)
    assert "doc-b" not in scores
    assert scores["doc-a"] > 0.0


def test_bm25_scores_is_deterministic() -> None:
    documents = {
        "doc-a": tokenize("The rate of interest is 10.5 percent per annum."),
        "doc-b": tokenize("A different document about something else entirely."),
    }
    query = tokenize("interest rate")
    a = bm25_scores(query, documents, k1=1.5, b=0.75)
    b = bm25_scores(query, documents, k1=1.5, b=0.75)
    assert a == b


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
