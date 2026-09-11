"""`sources/knowledge/loader.py::FileCorpusStore` (CLAUDE.md §6.1, §23, §33).
Pure & offline — reads only committed fixture files, never the network."""

from __future__ import annotations

import csv
import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vyaparsarathi.models.knowledge import (
    ChunkLocator,
    DocumentChunk,
    DocumentRecord,
    Jurisdiction,
    JurisdictionLevel,
    SourceTier,
)
from vyaparsarathi.models.parameters import ParameterName
from vyaparsarathi.sources.knowledge.loader import PARAM_CSV_FIELDNAMES, FileCorpusStore

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


# ======================================================================
# defense-in-depth: a row inconsistent with its own document is dropped
# ======================================================================


def _write_minimal_corpus(
    tmp_path: Path,
    *,
    doc_tier: SourceTier,
    doc_jurisdiction: Jurisdiction,
    param_tier: str,
    appl_jurisdiction_level: str,
    appl_state: str,
) -> Path:
    corpus_dir = tmp_path / "corpus"
    corpus_dir.mkdir()
    text = "The rate of interest chargeable shall be 9.5% per annum."
    document = DocumentRecord(
        document_id="doc-x",
        title="Synthetic test document",
        publisher="Fictional test publisher",
        tier=doc_tier,
        jurisdiction=doc_jurisdiction,
        retrieved_at=datetime(2026, 1, 15, tzinfo=UTC),
        content_sha256="a" * 64,
    )
    chunk = DocumentChunk(
        chunk_id="doc-x#s1",
        document_id="doc-x",
        locator=ChunkLocator(section="1"),
        text=text,
        token_count=len(text.split()),
        text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )
    (corpus_dir / "documents.jsonl").write_text(document.model_dump_json() + "\n", encoding="utf-8")
    (corpus_dir / "chunks.jsonl").write_text(chunk.model_dump_json() + "\n", encoding="utf-8")

    row = dict.fromkeys(PARAM_CSV_FIELDNAMES, "")
    row.update(
        {
            "parameter_id": "doc-x:interest_rate_pct:1",
            "name": "interest_rate_pct",
            "value": "9.5",
            "unit": "percent_per_annum",
            "value_token": "9.5%",
            "normalization": "percent_as_annual_rate",
            "evidence_quote": text,
            "document_id": "doc-x",
            "chunk_id": "doc-x#s1",
            "locator_section": "1",
            "tier": param_tier,
            "appl_jurisdiction_level": appl_jurisdiction_level,
            "appl_jurisdiction_state": appl_state,
            "is_benchmark": "false",
            "extractor_model": "test-extractor",
            "verifier_model": "test-verifier",
            "verified_on": "2026-01-15",
        }
    )
    with (corpus_dir / "parameters.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(PARAM_CSV_FIELDNAMES))
        writer.writeheader()
        writer.writerow(row)
    return corpus_dir


def test_tier_mismatch_between_parameter_and_document_is_dropped_and_counted(
    tmp_path: Path,
) -> None:
    # Both public_sector_institution and govt_primary pass INTEREST_RATE_PCT's
    # tier floor, so this isolates the tier-MATCHES-document check from the
    # separate tier-floor check.
    corpus_dir = _write_minimal_corpus(
        tmp_path,
        doc_tier=SourceTier.PUBLIC_SECTOR_INSTITUTION,
        doc_jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        param_tier="govt_primary",
        appl_jurisdiction_level="state",
        appl_state="Bihar",
    )
    store = FileCorpusStore(corpus_dir)
    report = store.report()
    assert report.parameters_rejected_tier_mismatch == 1
    assert store.parameters() == []


def test_jurisdiction_exceeding_document_scope_is_dropped_and_counted(tmp_path: Path) -> None:
    # A same-rank but disjoint state (Karnataka) claimed by a Bihar-scoped
    # document's parameter — the strict "not just broader" reading.
    corpus_dir = _write_minimal_corpus(
        tmp_path,
        doc_tier=SourceTier.GOVT_PRIMARY,
        doc_jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        param_tier="govt_primary",
        appl_jurisdiction_level="state",
        appl_state="Karnataka",
    )
    store = FileCorpusStore(corpus_dir)
    report = store.report()
    assert report.parameters_rejected_jurisdiction_scope == 1
    assert store.parameters() == []


def test_jurisdiction_within_document_scope_is_accepted(tmp_path: Path) -> None:
    # Sanity check for the two tests above: a matching state is NOT rejected.
    corpus_dir = _write_minimal_corpus(
        tmp_path,
        doc_tier=SourceTier.GOVT_PRIMARY,
        doc_jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        param_tier="govt_primary",
        appl_jurisdiction_level="state",
        appl_state="Bihar",
    )
    store = FileCorpusStore(corpus_dir)
    report = store.report()
    assert report.parameters_rejected_tier_mismatch == 0
    assert report.parameters_rejected_jurisdiction_scope == 0
    assert len(store.parameters()) == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
