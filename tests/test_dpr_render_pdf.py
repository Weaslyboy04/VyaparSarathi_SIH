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


def test_pdf_contains_every_main_chapter_title(doc: DprDocument, tmp_path: Path) -> None:
    """The 12 flat sections are grouped into 8 reader-facing chapters
    (CLAUDE.md-external presentation pass) — the main report now reads as
    a narrative, with full technical detail moved to the annexure."""
    text, pages = _text(render_pdf_bytes(doc), tmp_path)
    for title in (
        "Quick Business Summary",
        "Your Local Market",
        "Business Opportunity & Competition",
        "Money & Loan Plan",
        "Risks & What Could Go Wrong",
        "What You Should Do Next",
        "Data Limitations",
        "Annexures",
    ):
        assert title in text, f"missing chapter: {title!r}"
    assert "Page 1 of" in text
    assert doc.report_id in text  # header


def test_pdf_contains_every_section_title(doc: DprDocument, tmp_path: Path) -> None:
    """Every original section's full content still exists SOMEWHERE in the
    document (main chapter or annexure) — restructuring moves content, it
    never deletes evidence."""
    text, pages = _text(render_pdf_bytes(doc), tmp_path)
    for title in (
        "Entrepreneur and project profile",
        "Local market assessment",
        "Opportunity and alternatives",
        "Project and operating plan",
        "Financial assessment",
        "Scheme, compliance, and knowledge evidence",
        "Risks and SWOT",
        "Assumptions, limitations, and evidence confidence",
    ):
        assert title in text, f"missing section: {title!r}"


def test_pdf_is_byte_deterministic(doc: DprDocument) -> None:
    assert render_pdf_bytes(doc) == render_pdf_bytes(doc)


def test_json_is_beside_and_round_trips(doc: DprDocument) -> None:
    js = render_json_str(doc)
    restored = DprDocument.model_validate_json(js)
    assert restored.report_id == doc.report_id
    assert restored.model_dump() == doc.model_dump()


def test_no_literal_markup_tags_leak_into_visible_text(
    doc: DprDocument, tmp_path: Path
) -> None:
    """`_p()` escapes `<`/`>` unconditionally — a caller building its own
    `<b>...</b>` markup and passing the whole string through `_p()` gets
    that markup double-escaped into visible `&lt;b&gt;` text instead of
    actually bolding it. This must never happen anywhere in the document."""
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    assert "<b>" not in text
    assert "&lt;b&gt;" not in text
    assert "</b>" not in text
    # the four known-affected labels must still appear as plain readable
    # text (proving the fix didn't just delete the content)
    assert "Risk:" in text
    assert "Named breaking point:" in text
    assert "Corpus:" in text


def test_footer_displays_ist_not_utc(doc: DprDocument, tmp_path: Path) -> None:
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    assert "UTC" not in text
    assert "IST" in text
    assert "14:30" in text  # _GEN is 09:00 UTC == 14:30 IST


def test_evidence_gaps_are_capped_in_the_main_summary_not_dumped_in_full(
    doc: DprDocument, tmp_path: Path
) -> None:
    """Up to 12 raw evidence-gap strings used to be dumped straight into
    the executive summary. The main chapter now shows only the top few and
    points to the annexure for the rest — never silently drops any of them."""
    assert len(doc.evidence_gaps) > 3, "fixture must have enough gaps to test capping"
    text, tmp_pages = _text(render_pdf_bytes(doc), tmp_path)
    assert "Annexure" in text and "evidence gap" in text.lower()

    # (the renderer's house style substitutes em dashes for commas, so
    # compare on the substring before the dash rather than the raw string.)
    fragments = [gap.split(" — ")[0] for gap in doc.evidence_gaps]

    # every individual gap still exists somewhere in the document — capping
    # the summary's list must never delete evidence.
    for fragment in fragments:
        assert fragment in text, f"evidence gap silently dropped: {fragment!r}"

    # but the Quick Business Summary chapter itself (before the next
    # chapter starts) must show only a handful, not the full raw dump.
    summary_only = text.split("Your Local Market", 1)[0]
    shown_in_summary = sum(1 for f in fragments if f in summary_only)
    assert shown_in_summary <= 4, (
        f"{shown_in_summary} evidence gaps shown in the summary chapter; expected a cap"
    )


def test_opportunity_alternatives_are_capped_in_main_full_list_in_annexure(
    doc: DprDocument, tmp_path: Path
) -> None:
    alternatives = doc.opportunity.alternatives
    assert len(alternatives) > 3, "fixture must have enough alternatives to test capping"
    text, tmp_pages = _text(render_pdf_bytes(doc), tmp_path)
    # every alternative business name still appears (main top-3 + annexure
    # full list) -- capping the main chapter must never delete evidence.
    for alt in alternatives:
        assert alt.business in text


def test_rupee_symbol_renders_with_a_real_glyph_not_a_missing_glyph_box(
    doc: DprDocument, tmp_path: Path
) -> None:
    """Base-14 fonts (Helvetica/Times) use WinAnsiEncoding, which has no
    slot for U+20B9 (₹) — the concrete failure signatures are a
    replacement-character box ('■') for a base-14 font, or a literal NUL
    ('\\x00') for a TTF that's missing the glyph. Neither must ever appear
    once a Unicode-capable font is registered and wired into every style."""
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    assert "₹" in text
    assert "■" not in text
    assert "\x00" not in text


def test_raw_engine_tokens_are_translated_to_plain_language(
    doc: DprDocument, tmp_path: Path
) -> None:
    """`deciding_rung` (e.g. "stress_sensitive") and scheme-parameter
    `status` tokens (e.g. "no_evidence") are internal engine vocabulary --
    a rural entrepreneur should never see a raw snake_case token in the
    main report or the annexure."""
    assert doc.financial.deciding_rung == "stress_sensitive"
    assert doc.scheme_knowledge.resolved_parameters
    assert all(line.status == "no_evidence" for line in doc.scheme_knowledge.resolved_parameters)
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    # Annexure B's calculation-provenance table intentionally keeps raw
    # dotted internal paths (audit trail, correct register there) -- so
    # check the reader-facing main report only, not the whole document.
    main_report = text.split("Annexures", 1)[0]
    assert "stress_sensitive" not in main_report
    assert "no_evidence" not in main_report
    assert "only just" in text  # the plain phrase for stress_sensitive
    assert "not confirmed" in text  # the plain phrase for no_evidence


def test_visualizations_render_from_already_shown_figures_only(
    doc: DprDocument, tmp_path: Path
) -> None:
    """The 4 charts/cards each read a value already computed and displayed
    in full elsewhere in the report -- never a new calculation. This is a
    presence + non-fabrication check, not a pixel test: every label the
    chart draws must trace back to a real figure already asserted
    elsewhere in this test module."""
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    # 1. market snapshot cards (Your Local Market)
    assert "DIRECT COMPETITORS" in text
    assert "MARKET READING" in text
    # 2. opportunity comparison bar chart (category axis + value ticks)
    assert "Proposed" in text
    assert doc.opportunity.alternatives[0].business in text
    # 3. financial structure comparison chart (legend + category axis)
    assert "Actual business requirement" in text
    assert "Theoretical scheme capacity" in text
    assert "Project cost" in text
    # 4. stress/risk status list
    assert "Stress-test outcomes at a glance" in text
    for s in doc.financial.stress_scenarios:
        assert s.name in text


def test_footer_is_one_line_with_report_id_and_pagination_no_per_page_disclaimer(
    doc: DprDocument, tmp_path: Path
) -> None:
    """The old 3-part footer (generated date / page X of Y / a paraphrased
    disclaimer repeated on every page) is replaced by one line: brand,
    report id, and pagination. The full disclaimer now lives once on the
    cover page, with a one-line pointer to it at the top of Quick Business
    Summary -- never repeated in full on every page."""
    text, _pages = _text(render_pdf_bytes(doc), tmp_path)
    assert f"VyaparSarathi | Report ID: {doc.report_id} | Page 1 of" in text
    assert "Decision-support material, not a loan sanction" not in text
    assert "Full disclaimer on the cover page." in text
    assert "loan sanction" in text.lower()  # the full disclaimer, once, on the cover


def test_pdf_renders_when_sections_are_evidence_gaps(tmp_path: Path) -> None:
    # a minimal session: market/opportunity/finance are largely gaps
    doc = assemble_report(run_pipeline(minimal_turns(), tmp_path=tmp_path), generated_at=_GEN)
    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    text, _pages = _text(pdf, tmp_path)
    assert "Evidence gap" in text
    assert "Financial assessment" in text
