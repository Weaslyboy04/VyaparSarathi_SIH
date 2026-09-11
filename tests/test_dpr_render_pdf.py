"""`dpr/render_pdf.py` / `render_json.py`: a valid, deterministic PDF with the
expected sections, and the structured JSON beside it (CLAUDE.md §25 Phase 8).
Offline — reads the produced PDF back with `pypdf`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pypdf import PdfReader

from tests.dpr_pipeline import full_scenario_turns, minimal_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.render_json import render_json_str
from vyaparsarathi.dpr.render_pdf import render_pdf_bytes
from vyaparsarathi.dpr.report_models import DprDocument

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


@pytest.fixture
def doc(tmp_path: Path) -> DprDocument:
    return assemble_report(
        run_pipeline(full_scenario_turns(), tmp_path=tmp_path), generated_at=_GEN
    )


def _text(pdf: bytes, tmp_path: Path) -> tuple[str, int]:
    p = tmp_path / "r.pdf"
    p.write_bytes(pdf)
    reader = PdfReader(str(p))
    joined = "\n".join(page.extract_text() or "" for page in reader.pages)
    return joined, len(reader.pages)


def test_pdf_is_valid_and_non_trivial(doc: DprDocument, tmp_path: Path) -> None:
    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    assert pdf.rstrip().endswith(b"%%EOF")
    assert len(pdf) > 15_000
    reader = PdfReader(__import__("io").BytesIO(pdf))
    assert len(reader.pages) >= 4


def test_pdf_contains_every_section_title(doc: DprDocument, tmp_path: Path) -> None:
    text, pages = _text(render_pdf_bytes(doc), tmp_path)
    for title in (
        "Executive summary",
        "Entrepreneur and project profile",
        "Local market assessment",
        "Opportunity and alternatives",
        "Project and operating plan",
        "Financial assessment",
        "Scheme, compliance, and knowledge evidence",
        "Risks and SWOT",
        "Assumptions, limitations, and evidence confidence",
        "Annexures",
    ):
        assert title in text, f"missing section: {title!r}"
    assert "Decision-support" in text  # footer disclaimer strip
    assert "Page 1 of" in text
    assert doc.report_id in text  # header


def test_pdf_is_byte_deterministic(doc: DprDocument) -> None:
    assert render_pdf_bytes(doc) == render_pdf_bytes(doc)


def test_json_is_beside_and_round_trips(doc: DprDocument) -> None:
    js = render_json_str(doc)
    restored = DprDocument.model_validate_json(js)
    assert restored.report_id == doc.report_id
    assert restored.model_dump() == doc.model_dump()


def test_pdf_renders_when_sections_are_evidence_gaps(tmp_path: Path) -> None:
    # a minimal session: market/opportunity/finance are largely gaps
    doc = assemble_report(run_pipeline(minimal_turns(), tmp_path=tmp_path), generated_at=_GEN)
    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    text, _pages = _text(pdf, tmp_path)
    assert "Evidence gap" in text
    assert "Financial assessment" in text
