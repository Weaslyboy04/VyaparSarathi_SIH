"""`scripts/phase8_demo.py` — the SIH demo report. Asserts the demo tells the
CLAUDE.md §32 story (catch a poor decision, point to a better one) and that
the CLI service never overwrites silently. Offline.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts.phase8_demo import (
    DEMO_GENERATED_AT,
    build_demo_document,
    build_demo_session,
    generate,
)

from vyaparsarathi.conversation.session_models import StepId
from vyaparsarathi.dpr.errors import DprOutputExistsError
from vyaparsarathi.dpr.render_pdf import render_pdf_bytes


def test_demo_session_reaches_a_full_recommendation() -> None:
    session = build_demo_session()
    for step in (
        StepId.DISCOVER,
        StepId.ASSESS_MARKET,
        StepId.OPPORTUNITY,
        StepId.ASSESS_FINANCE,
        StepId.RECOMMEND,
        StepId.SWOT,
    ):
        assert step in session.artifacts, f"demo session missing {step.value}"


def test_demo_document_tells_the_pivot_story() -> None:
    doc = build_demo_document()
    assert doc.report_id.startswith("DPR-")
    # a better local alternative was surfaced
    assert doc.opportunity.recommended_pivot.origin.value != "not_available"
    assert doc.executive_summary.recommendation.display.lower() in {
        "pivot",
        "adjust",
        "proceed with caution",
        "proceed",
        "insufficient evidence",
    }
    # the financial assessment is a real Phase 4 verdict on the stated plan
    assert doc.financial.feasibility_status.origin.value == "calculated"
    # the shipped corpus is described honestly as limited
    assert "3 document" in doc.scheme_knowledge.corpus_note
    assert "limited" in doc.scheme_knowledge.corpus_note.lower()
    # something is genuinely sourced (the discovery sample + census at least)
    assert {"DS", "CEN"} <= set(doc.citations)


def test_demo_document_is_deterministic() -> None:
    a = build_demo_document()
    b = build_demo_document()
    assert a.report_id == b.report_id
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    assert render_pdf_bytes(a) == render_pdf_bytes(b)


def test_generate_writes_both_files_and_refuses_silent_overwrite(tmp_path: Path) -> None:
    artifacts = generate(tmp_path, overwrite=False)
    assert artifacts.pdf_path.exists() and artifacts.json_path.exists()
    assert artifacts.pdf_path.read_bytes().startswith(b"%PDF-")
    assert artifacts.json_path.read_text(encoding="utf-8").startswith("{")
    assert artifacts.report_id == artifacts.document.report_id

    with pytest.raises(DprOutputExistsError):
        generate(tmp_path, overwrite=False)

    again = generate(tmp_path, overwrite=True)
    assert again.report_id == artifacts.report_id


def test_demo_report_id_is_pinned() -> None:
    # a change here means the demo's evidence changed — update deliberately.
    assert build_demo_document().report_id == "DPR-5CFFC02EE224B5E4"
    assert DEMO_GENERATED_AT.tzinfo is not None
