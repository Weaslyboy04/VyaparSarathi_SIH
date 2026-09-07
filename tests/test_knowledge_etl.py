"""`scripts/build_knowledge_corpus.py` + `scripts/build_parameter_registry.py`
— the offline ETL, tested the way `tests/test_finance_demos.py` imports
`scripts/phase4_demo.py` (CLAUDE.md §23, §33). Pure & offline; `main()` here
only ever touches `tmp_path`, never the real `data/knowledge/`."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest
from scripts import build_knowledge_corpus, build_parameter_registry

from vyaparsarathi.errors import KnowledgeCorpusError
from vyaparsarathi.sources.knowledge.loader import PARAM_CSV_FIELDNAMES, FileCorpusStore

build_chunks = build_knowledge_corpus.build_chunks
build_corpus_main = build_knowledge_corpus.main
propose_candidates = build_parameter_registry.propose_candidates
build_registry_main = build_parameter_registry.main
_cmd_verify = build_parameter_registry._cmd_verify
_parse_args = build_parameter_registry._parse_args

# ======================================================================
# build_chunks — pure, section-aware, deterministic
# ======================================================================


def test_build_chunks_splits_on_headings() -> None:
    text = (
        "# 1 Intro\n\nFirst section text.\n\n## 1.1 Detail\n\nSecond section text, more detailed.\n"
    )
    chunks = build_chunks("doc-a", text, max_words=100)
    assert [c.locator.section for c in chunks] == ["1", "1.1"]
    assert chunks[0].heading_path == ("1 Intro",)
    assert chunks[1].heading_path == ("1 Intro", "1.1 Detail")


def test_build_chunks_captures_page_and_tag_markers() -> None:
    text = (
        "# 1 Intro\n\n[[page:12]]\n[[topics: scheme_financing]]\n"
        "[[schemes: test-scheme]]\n\nSection text with markers applied.\n"
    )
    chunks = build_chunks("doc-a", text, max_words=100)
    assert len(chunks) == 1
    assert chunks[0].locator.page_from == 12
    assert chunks[0].topics[0].value == "scheme_financing"
    assert chunks[0].schemes == ("test-scheme",)


def test_build_chunks_splits_long_sections_into_paragraph_chunks() -> None:
    para = "word " * 20  # 20 words per paragraph
    text = "# 1 Intro\n\n" + f"{para}\n\n{para}\n\n{para}\n"
    # three 20-word paragraphs, cap 50: (1) 20 (2) 20+20=40<=50 combines (3)
    # 40+20=60>50 splits -> paragraphs 1+2 in one chunk, paragraph 3 alone.
    chunks = build_chunks("doc-a", text, max_words=50)
    assert len(chunks) == 2
    assert all(c.locator.section == "1" for c in chunks)
    assert {c.locator.paragraph_index for c in chunks} == {0, 1}
    assert chunks[0].text.count("word") == 40
    assert chunks[1].text.count("word") == 20


def test_build_chunks_never_splits_inside_a_single_paragraph() -> None:
    # Two oversized paragraphs, each alone bigger than max_words, must still
    # each become their own whole chunk rather than being cut mid-paragraph.
    para = "word " * 60
    text = "# 1 Intro\n\n" + f"{para}\n\n{para}\n"
    chunks = build_chunks("doc-a", text, max_words=50)
    assert len(chunks) == 2
    assert all(c.text.count("word") == 60 for c in chunks)


def test_build_chunks_is_deterministic() -> None:
    text = "# 1 Intro\n\nSome stable text.\n\n## 1.1 More\n\nMore stable text.\n"
    a = build_chunks("doc-a", text, max_words=100)
    b = build_chunks("doc-a", text, max_words=100)
    assert [c.model_dump(mode="json") for c in a] == [c.model_dump(mode="json") for c in b]


def test_build_chunks_disambiguates_colliding_locator_refs() -> None:
    # Two headings with the same leading number produce the same base ref;
    # the second must not silently overwrite the first's chunk_id.
    text = "# 1 First\n\nFirst text.\n\n# 1 Duplicate Heading Number\n\nSecond text.\n"
    chunks = build_chunks("doc-a", text, max_words=100)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_build_chunks_on_empty_text_produces_nothing() -> None:
    assert build_chunks("doc-a", "", max_words=100) == []
    assert build_chunks("doc-a", "# 1 Empty Heading\n\n", max_words=100) == []


# ======================================================================
# propose_candidates — pure pattern matching, unreviewed output
# ======================================================================


def test_propose_candidates_finds_an_interest_rate_and_tenure() -> None:
    text = (
        "# 1\n\nThe rate of interest chargeable shall be 9.5% per annum "
        "for a tenure of 48 months.\n"
    )
    chunks = build_chunks("doc-a", text, max_words=200)
    rows = propose_candidates(chunks)
    names = {r["name"] for r in rows}
    assert "interest_rate_pct" in names
    assert "loan_tenure_months" in names


def test_propose_candidates_rows_are_unsigned() -> None:
    text = "# 1\n\nThe rate of interest chargeable shall be 9.5% per annum.\n"
    chunks = build_chunks("doc-a", text, max_words=200)
    rows = propose_candidates(chunks)
    assert rows
    for row in rows:
        assert row["reviewed_by"] == ""
        assert row["reviewed_on"] == ""
        assert row["value"] == ""  # the reviewer must supply this


def test_propose_candidates_finds_nothing_without_a_keyword() -> None:
    text = "# 1\n\nNothing relevant is mentioned in this section at all.\n"
    chunks = build_chunks("doc-a", text, max_words=200)
    assert propose_candidates(chunks) == []


def test_propose_candidate_columns_match_the_loader_schema() -> None:
    text = "# 1\n\nThe rate of interest chargeable shall be 9.5% per annum.\n"
    chunks = build_chunks("doc-a", text, max_words=200)
    rows = propose_candidates(chunks)
    assert rows
    assert set(rows[0]) == set(PARAM_CSV_FIELDNAMES)


# ======================================================================
# build_knowledge_corpus.py main() — end to end on tmp_path
# ======================================================================


def _write_raw_document(raw_dir: Path, document_id: str, *, text: str) -> None:
    doc_dir = raw_dir / document_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text(
        json.dumps(
            {
                "title": "Synthetic ETL test document",
                "publisher": "Fictional test publisher",
                "tier": "govt_primary",
                "jurisdiction": {"level": "national"},
                "published_on": "2025-01-01",
                "retrieved_at": "2026-01-15T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (doc_dir / "text.txt").write_text(text, encoding="utf-8")


def test_build_corpus_main_writes_documents_and_chunks(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    out_dir = tmp_path / "out"
    _write_raw_document(
        raw_dir, "doc-a", text="# 1 Intro\n\nThe rate of interest chargeable shall be 9.5%.\n"
    )
    rc = build_corpus_main(
        [
            "--raw-dir",
            str(raw_dir),
            "--out-dir",
            str(out_dir),
            "--built-at",
            "2026-01-15T00:00:00+00:00",
        ]
    )
    assert rc == 0
    store = FileCorpusStore(out_dir)
    report = store.report()
    assert report.documents_loaded == 1
    assert report.chunks_loaded == 1


def test_build_corpus_main_reports_failure_on_empty_raw_dir(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rc = build_corpus_main(
        [
            "--raw-dir",
            str(raw_dir),
            "--out-dir",
            str(tmp_path / "out"),
            "--built-at",
            "2026-01-15T00:00:00+00:00",
        ]
    )
    assert rc == 1


# ======================================================================
# build_parameter_registry.py main() — verify catches tampering
# ======================================================================


def _built_corpus(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    out_dir = tmp_path / "out"
    _write_raw_document(
        raw_dir,
        "doc-a",
        text="# 1 Intro\n\n[[schemes: test-scheme]]\n\n"
        "The rate of interest chargeable shall be 9.5% per annum.\n",
    )
    assert (
        build_corpus_main(
            [
                "--raw-dir",
                str(raw_dir),
                "--out-dir",
                str(out_dir),
                "--built-at",
                "2026-01-15T00:00:00+00:00",
            ]
        )
        == 0
    )
    return out_dir


def _write_signed_row(out_dir: Path, *, value_token: str, value: str) -> None:
    row = dict.fromkeys(PARAM_CSV_FIELDNAMES, "")
    row.update(
        {
            "parameter_id": "doc-a:interest_rate_pct:1",
            "name": "interest_rate_pct",
            "value": value,
            "unit": "percent_per_annum",
            "value_token": value_token,
            "normalization": "percent_as_annual_rate",
            "evidence_quote": "The rate of interest chargeable shall be 9.5% per annum.",
            "document_id": "doc-a",
            "chunk_id": "doc-a#s1",
            "locator_section": "1",
            "tier": "govt_primary",
            "appl_jurisdiction_level": "national",
            "appl_scheme": "test-scheme",
            "is_benchmark": "false",
            "reviewed_by": "test",
            "reviewed_on": "2026-01-15",
        }
    )
    with (out_dir / "parameters.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(PARAM_CSV_FIELDNAMES))
        writer.writeheader()
        writer.writerow(row)


def test_verify_passes_on_a_correctly_signed_row(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="9.5")
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 0


def test_verify_fails_on_a_tampered_value(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    # value_token/evidence_quote say 9.5%, but value claims 99.9 — tampered.
    _write_signed_row(out_dir, value_token="9.5%", value="99.9")
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 1


def test_verify_fails_on_an_unknown_chunk_reference(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="9.5")
    content = (out_dir / "parameters.csv").read_text(encoding="utf-8")
    (out_dir / "parameters.csv").write_text(
        content.replace("doc-a#s1", "doc-a#s99-nonexistent"), encoding="utf-8"
    )
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 1


def test_verify_reports_clean_on_a_freshly_built_empty_corpus(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)  # no parameters.csv written at all
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 0


def test_cmd_verify_itself_raises_on_tampering(tmp_path: Path) -> None:
    # main()'s try/except is what turns this into exit code 1 for a CLI
    # caller; this confirms the underlying mechanism a future refactor could
    # otherwise silently break is really KnowledgeCorpusError, not a printed
    # warning that nothing acts on.
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="99.9")
    args = _parse_args(["verify", "--corpus-dir", str(out_dir)])
    with pytest.raises(KnowledgeCorpusError):
        _cmd_verify(args)


def test_propose_writes_a_csv_that_the_loader_schema_accepts(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    proposed_path = tmp_path / "proposed.csv"
    rc = build_registry_main(["propose", "--corpus-dir", str(out_dir), "--out", str(proposed_path)])
    assert rc == 0
    assert proposed_path.exists()
    with proposed_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == list(PARAM_CSV_FIELDNAMES)
        rows = list(reader)
    assert rows
    assert all(row["reviewed_by"] == "" for row in rows)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
