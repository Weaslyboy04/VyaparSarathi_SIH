"""When core financial drivers are absent, the DPR renders a prominent
"Financial assessment incomplete" section naming the exact missing inputs —
never a fabricated EMI / DSCR / rate (CLAUDE.md §15, §30; Phase 8). Offline.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from tests.dpr_pipeline import minimal_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import ValueOrigin
from vyaparsarathi.dpr.report_models import SectionStatus

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def test_incomplete_finance_is_flagged_and_named(tmp_path: Path) -> None:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)

    fin = doc.financial
    assert fin.status in (SectionStatus.PARTIAL, SectionStatus.EVIDENCE_GAP)
    assert fin.incomplete_note  # the standing "nothing assumed in their place" note
    assert "incomplete" in fin.gap_note.lower()
    assert fin.missing_core_drivers, "the exact missing drivers must be listed"

    # each driver appears in the report-wide evidence-gap roll-up
    joined = " ".join(doc.evidence_gaps).lower()
    for driver in fin.missing_core_drivers:
        assert driver.lower() in joined


def test_no_loan_servicing_number_is_invented(tmp_path: Path) -> None:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    fin = doc.financial

    # The four core drivers were not stated, so every figure that DEPENDS on
    # them stays an explicit gap — never a fabricated number.
    for pv in (
        fin.average_annual_dscr,
        fin.first_post_moratorium_dscr,
        fin.minimum_cash_balance,
        fin.cash_at_emi_start,
        fin.operating_break_even_month,
        fin.cash_break_even_month,
    ):
        assert pv.origin is ValueOrigin.NOT_AVAILABLE, f"{pv.label} was not left as a gap"
        assert pv.raw is None

    # The SIH capacity screen (from stated cash alone) MAY show declared-config
    # loan terms — but they must be labelled DECLARED_CONFIG (with the "not a
    # scheme rule" rationale), never user-provided, sourced, or an unlabelled
    # calculation.
    for pv in (fin.loan_principal, fin.interest_rate, fin.tenure, fin.moratorium):
        assert pv.origin in (ValueOrigin.NOT_AVAILABLE, ValueOrigin.DECLARED_CONFIG)
        if pv.origin is ValueOrigin.DECLARED_CONFIG:
            assert "scheme" in pv.rationale.lower() and "not" in pv.rationale.lower()
    assert fin.monthly_emi.origin in (ValueOrigin.NOT_AVAILABLE, ValueOrigin.CALCULATED)
    if fin.monthly_emi.origin is ValueOrigin.CALCULATED:
        assert fin.monthly_emi.inputs  # names the declared loan / rate / tenure it used


def test_capacity_screen_still_shows_when_only_cash_is_known(tmp_path: Path) -> None:
    """`finance/capacity.py` produces the SIH headline from Available Margin
    Capital alone — so the required-margin / indicated-loan figures are still
    present, labelled DECLARED_CONFIG, even with no viability verdict."""
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    fin = doc.financial
    assert fin.required_promoter_margin.origin is ValueOrigin.DECLARED_CONFIG
    assert fin.indicated_loan.origin is ValueOrigin.DECLARED_CONFIG
    assert (
        fin.feasibility_status.origin is not ValueOrigin.NOT_AVAILABLE or fin.missing_core_drivers
    )


def test_json_output_contains_the_gap_language(tmp_path: Path) -> None:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    blob = json.loads(doc.model_dump_json())
    text = json.dumps(blob).lower()
    assert "not available from current evidence" in text
    assert "financial assessment incomplete" in text
