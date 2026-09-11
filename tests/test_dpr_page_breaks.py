"""Long-table / page-break resilience: the PDF stays valid and paginates
cleanly when a table is far longer than a page, and when a section is empty
(CLAUDE.md §25 Phase 8: "readable when sections are missing or long").
Offline.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

from pypdf import PdfReader
from reportlab.platypus import KeepTogether, Paragraph

from tests.dpr_pipeline import full_scenario_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import GapReason, pv_calc, pv_missing
from vyaparsarathi.dpr.render_pdf import _h2_block, _styles, render_pdf_bytes
from vyaparsarathi.dpr.report_models import CalcProvenanceLine, SlotHistoryLine, StressLine

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def _pages(pdf: bytes) -> int:
    return len(PdfReader(io.BytesIO(pdf)).pages)


def _base_doc(tmp_path: Path):
    return assemble_report(
        run_pipeline(full_scenario_turns(), tmp_path=tmp_path), generated_at=_GEN
    )


def test_a_very_long_annexure_table_paginates_without_error(tmp_path: Path) -> None:
    doc = _base_doc(tmp_path)
    base_pages = _pages(render_pdf_bytes(doc))

    long_history = tuple(
        SlotHistoryLine(
            slot="liquid_cash_inr",
            state="user_provided",
            value=str(100000 + i),
            raw_text=f"correction number {i} — a deliberately wordy stated value to force wrapping",
            set_on_turn=i,
            superseded=i < 249,
        )
        for i in range(250)
    )
    doc.annexures = doc.annexures.model_copy(update={"slot_history": long_history})

    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    long_pages = _pages(pdf)
    assert long_pages > base_pages + 2, (base_pages, long_pages)

    text = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    # the repeated header row of a LongTable appears on more than one page
    assert text.count("Stated-input history") == 1  # the section heading is not repeated
    assert text.count("Superseded") >= 2  # the table header row IS repeated across pages


def test_long_stress_and_calc_tables_render(tmp_path: Path) -> None:
    doc = _base_doc(tmp_path)
    stress = tuple(
        StressLine(
            name=f"Scenario {i}",
            description="a long-ish description of this stress scenario " * 3,
            applied=True,
            minimum_cash=pv_calc("Minimum cash", f"₹{i},000", inputs=("stress cash flow",)),
            negative_cash_months="none" if i % 2 else "7, 8, 9",
            average_dscr=pv_calc("Average DSCR", "1.35", inputs=("stress cash flow",)),
            outcome="Feasible" if i % 3 else "Cash flow stress",
        )
        for i in range(60)
    )
    calc = tuple(
        CalcProvenanceLine(
            result=f"metric {i}",
            display=pv_calc(f"metric {i}", str(i), inputs=(f"input.{i}",)),
            inputs=(f"input.{i}", "another.input"),
            engine="finance.assessment",
        )
        for i in range(120)
    )
    doc.financial = doc.financial.model_copy(update={"stress_scenarios": stress})
    doc.annexures = doc.annexures.model_copy(update={"calculation_provenance": calc})

    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    assert _pages(pdf) >= 6


def test_h2_block_keeps_heading_and_content_together() -> None:
    """A sub-heading must never be able to land alone at the bottom of a
    page, separated from the table/bullets it introduces — `_h2_block`
    wraps both in one `KeepTogether` so ReportLab treats them as a single
    unbreakable unit for pagination purposes."""
    st = _styles()
    content = Paragraph("some content", st["body"])
    result = _h2_block("A heading", content, st=st)
    assert len(result) == 1
    assert isinstance(result[0], KeepTogether)
    wrapped = result[0]._content  # KeepTogether's own flowable list
    assert len(wrapped) == 2
    assert isinstance(wrapped[0], Paragraph)
    assert wrapped[1] is content


def test_h2_block_flattens_a_list_of_flowables() -> None:
    """Call sites that build a bullet list (a `list[Flowable]`) must not
    need to unpack it themselves before calling `_h2_block`."""
    st = _styles()
    bullets = [Paragraph("one", st["body"]), Paragraph("two", st["body"])]
    result = _h2_block("A heading", bullets, st=st)
    wrapped = result[0]._content
    assert len(wrapped) == 3  # heading + 2 bullets, not heading + [list]


def test_report_renders_when_a_section_is_totally_empty(tmp_path: Path) -> None:
    doc = _base_doc(tmp_path)
    # blank out the risks/SWOT section content but keep it a valid section
    doc.risks_swot = doc.risks_swot.model_copy(
        update={
            "strengths": (),
            "weaknesses": (),
            "opportunities": (),
            "threats": (),
            "quadrant_notes": (),
            "structured_risks": (),
            "mitigations": (),
            "data_caveats": (),
        }
    )
    doc.opportunity = doc.opportunity.model_copy(
        update={
            "proposed_score": pv_missing(
                "Proposed-business opportunity score", reason=GapReason.NO_EVIDENCE
            ),
            "factors": (),
            "alternatives": (),
        }
    )
    pdf = render_pdf_bytes(doc)
    assert pdf.startswith(b"%PDF-")
    assert _pages(pdf) >= 4
