"""`sources/knowledge/loader.py::FileCorpusStore` (CLAUDE.md §6.1, §23, §33).
Pure & offline — reads only committed fixture files, never the network."""

from __future__ import annotations

from pathlib import Path

import pytest

from vyaparsarathi.models.parameters import ParameterName
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"


def test_missing_directory_degrades_without_raising(tmp_path: Path) -> None:
    store = FileCorpusStore(tmp_path / "does-not-exist")
    report = store.report()
    assert report.corpus_present is False
    assert store.documents() == []
    assert store.chunks() == []
    assert store.parameters() == []


def test_empty_but_present_directory_degrades_to_zero_counts(tmp_path: Path) -> None:
    empty_dir = tmp_path / "knowledge"
    empty_dir.mkdir()
    store = FileCorpusStore(empty_dir)
    report = store.report()
    assert report.corpus_present is True
    assert report.documents_loaded == 0
    assert report.chunks_loaded == 0
    assert report.parameters_loaded == 0
    assert report.parse_errors == 0


def test_fixture_corpus_loads_expected_counts() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert report.corpus_present is True
    assert report.documents_loaded == 10
    assert report.chunks_loaded == 12
    # 12 authored rows minus the one SECONDARY-tier row rejected by the tier
    # floor = 11 that reach `.parameters()`.
    assert report.parameters_loaded == 11
    assert report.corpus_manifest_version == "test-fixture-1"
    assert report.corpus_built_at is not None


def test_unverified_quote_is_dropped_and_counted() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert report.parameters_rejected_unverified_quote == 1
    ids = {p.parameter_id for p in store.parameters()}
    assert "test-quote-mismatch-guideline:moratorium_months:1" not in ids


def test_unknown_chunk_is_dropped_and_counted() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert report.parameters_rejected_unknown_chunk == 1
    ids = {p.parameter_id for p in store.parameters()}
    assert "test-national-credit-guideline:subsidy_pct:99" not in ids


def test_below_tier_floor_is_dropped_and_counted() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert report.parameters_rejected_tier_floor == 1
    ids = {p.parameter_id for p in store.parameters()}
    assert "test-secondary-commentary:interest_rate_pct:1" not in ids
    # No SECONDARY-tier row of any kind ever reaches the loaded set.
    assert all(
        p.name is not ParameterName.INTEREST_RATE_PCT or p.tier.value != "secondary"
        for p in store.parameters()
    )


def test_malformed_unit_column_counts_as_a_parse_error_not_a_crash() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert report.parse_errors >= 1
    ids = {p.parameter_id for p in store.parameters()}
    assert "test-bihar-state-guideline:interest_rate_pct:99" not in ids


def test_documents_by_tier_tally_matches_the_fixture() -> None:
    store = FileCorpusStore(FIXTURES)
    report = store.report()
    assert sum(report.documents_by_tier.values()) == report.documents_loaded


def test_document_lookup_by_id() -> None:
    store = FileCorpusStore(FIXTURES)
    doc = store.document("test-bihar-state-guideline")
    assert doc is not None
    assert doc.jurisdiction.state == "Bihar"
    assert store.document("does-not-exist") is None


def test_chunk_lookup_by_id() -> None:
    store = FileCorpusStore(FIXTURES)
    chunk = store.chunk("test-bihar-licence-schedule#s1.2")
    assert chunk is not None
    assert "Rs. 500" in chunk.text
    assert store.chunk("does-not-exist") is None


def test_a_valid_parameter_survives_with_its_full_citation_trail() -> None:
    store = FileCorpusStore(FIXTURES)
    licence_fee = next(p for p in store.parameters() if p.name is ParameterName.LICENCE_FEE_INR)
    assert licence_fee.document_id == "test-bihar-licence-schedule"
    assert licence_fee.value == 500
    assert licence_fee.applicability.jurisdiction.state == "Bihar"


def test_corpus_store_never_raises_on_a_directory_of_garbage(tmp_path: Path) -> None:
    garbage_dir = tmp_path / "knowledge"
    garbage_dir.mkdir()
    (garbage_dir / "documents.jsonl").write_text(
        "not json at all\n{also not json\n", encoding="utf-8"
    )
    (garbage_dir / "chunks.jsonl").write_text("{}\n", encoding="utf-8")
    (garbage_dir / "parameters.csv").write_text("not,a,valid,header\n1,2,3,4\n", encoding="utf-8")
    (garbage_dir / "manifest.json").write_text("not json", encoding="utf-8")

    store = FileCorpusStore(garbage_dir)
    report = store.report()
    assert report.corpus_present is True
    assert report.documents_loaded == 0
    assert report.chunks_loaded == 0
    assert report.parameters_loaded == 0
    assert report.parse_errors > 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
