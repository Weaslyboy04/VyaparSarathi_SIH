"""Priority 1/2 fixes from the SIH26091 judge feedback pass (CLAUDE.md §12,
§14): the entrepreneur's *stated available cash* must never be labelled as
if it were the amount actually being contributed to this project, and the
scheme's *capacity screen* (what the declared 10%/90% split says the stated
Available Margin Capital could support) must be shown alongside the actual
business-based financing, not silently dropped once real project-cost
figures exist. Offline; no engine re-run, only report rendering.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.dpr_pipeline import full_scenario_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import ValueOrigin

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def test_available_cash_is_not_labelled_as_a_contribution(tmp_path: Path) -> None:
    """The stated Rs 6.5L liquid cash is a ceiling on what the promoter
    COULD put in, not a record of what was actually contributed to THIS
    business's (much smaller) project cost — the label must say so."""
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    pc = doc.financial.promoter_contribution
    assert pc.label == "Available promoter cash (stated)"
    assert "contribution" not in pc.label.lower()


def test_capital_remaining_after_margin_is_reported(tmp_path: Path) -> None:
    """Once the actual required margin is known, the report must say how
    much of the stated cash is left over — not just show two numbers
    (Rs 6.5L stated cash, Rs 54,300 required margin) and let the reader
    wonder why they don't match."""
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    remaining = doc.financial.capital_remaining_after_margin
    assert remaining.origin is ValueOrigin.CALCULATED
    assert remaining.inputs  # names what it was computed from


def test_scheme_capacity_screen_shown_alongside_actual_financing(tmp_path: Path) -> None:
    """The entrepreneur's Rs 6.5L margin capital could theoretically support
    a much bigger project under the declared scheme than this particular
    business needs (~Rs 5.43L). Both figures must be visible — the capacity
    screen is not just a fallback for when the real structuring is missing."""
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    fin = doc.financial

    # the actual, business-based project cost is present
    assert fin.project_cost.origin in (ValueOrigin.CALCULATED, ValueOrigin.DECLARED_CONFIG)

    # the theoretical scheme-capacity screen is ALSO shown, not dropped just
    # because the real structuring succeeded
    assert fin.capacity_feasible_project_cost.origin is ValueOrigin.DECLARED_CONFIG
    assert "50,00,000" in fin.capacity_feasible_project_cost.display
    assert fin.capacity_required_margin.origin is ValueOrigin.DECLARED_CONFIG
    assert "5,00,000" in fin.capacity_required_margin.display
    assert fin.capacity_indicated_loan.origin is ValueOrigin.DECLARED_CONFIG
    assert "45,00,000" in fin.capacity_indicated_loan.display

    # the two are not confusable with each other
    assert fin.capacity_note
    assert "not a" in fin.capacity_note.lower() or "capacity" in fin.capacity_note.lower()


def test_repayment_schedule_is_wired_into_the_financial_section(tmp_path: Path) -> None:
    """Priority 8: the real business-based debt schedule (`fin.debt`) must
    reach the DPR as a quarterly repayment timeline, not stop at the single
    EMI figure — the moratorium quarters and the switch into repayment must
    both be visible."""
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    schedule = doc.financial.repayment_schedule
    assert schedule
    assert schedule[0].status == "Moratorium"
    assert any(q.status == "Repayment" for q in schedule)
    assert schedule[-1].closing_balance.display == "₹0"
