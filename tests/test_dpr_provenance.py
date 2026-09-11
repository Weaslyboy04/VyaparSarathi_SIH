"""Every DPR figure carries exactly one provenance origin, and the assembler
never invents a value (CLAUDE.md §23, §30; Phase 8 core rule). Offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.dpr_pipeline import full_scenario_turns, minimal_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import ProvenancedValue, ValueOrigin
from vyaparsarathi.dpr.report_models import DprDocument

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def _walk(model: object) -> list[ProvenancedValue]:
    from pydantic import BaseModel

    out: list[ProvenancedValue] = []

    def visit(obj: object) -> None:
        if isinstance(obj, ProvenancedValue):
            out.append(obj)
        elif isinstance(obj, BaseModel):
            for v in obj.__dict__.values():
                visit(v)
        elif isinstance(obj, dict):
            for v in obj.values():
                visit(v)
        elif isinstance(obj, list | tuple | set | frozenset):
            for v in obj:
                visit(v)

    visit(model)
    return out


@pytest.fixture
def full_doc(tmp_path: Path) -> DprDocument:
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    return assemble_report(session, generated_at=_GEN)


@pytest.fixture
def minimal_doc(tmp_path: Path) -> DprDocument:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    return assemble_report(session, generated_at=_GEN)


def test_every_provenanced_value_is_valid_and_never_blank(full_doc: DprDocument) -> None:
    pvs = _walk(full_doc)
    assert len(pvs) > 40
    for pv in pvs:
        assert pv.display.strip(), f"blank display on {pv.label}"
        # pydantic validators already enforce the origin/field invariants; re-assert
        # the load-bearing ones here so a regression is obvious.
        if pv.origin is ValueOrigin.SOURCED:
            assert pv.citation_id
        elif pv.origin is ValueOrigin.CALCULATED:
            assert pv.inputs
        elif pv.origin in (ValueOrigin.ASSUMED, ValueOrigin.DECLARED_CONFIG):
            assert pv.rationale.strip()
        elif pv.origin is ValueOrigin.NOT_AVAILABLE:
            assert pv.gap_reason is not None
            assert pv.raw is None and not pv.citation_id and not pv.inputs


def test_user_stated_cash_is_user_provided(full_doc: DprDocument) -> None:
    cap = full_doc.profile.available_margin_capital
    assert cap.origin is ValueOrigin.USER_PROVIDED
    assert "650000" in (cap.raw or "") or "6,50,000" in cap.display


def test_promoter_contribution_is_a_labelled_assumption(full_doc: DprDocument) -> None:
    pc = full_doc.financial.promoter_contribution
    assert pc.origin in (
        ValueOrigin.ASSUMED,
        ValueOrigin.DECLARED_CONFIG,
        ValueOrigin.USER_PROVIDED,
    )
    if pc.origin in (ValueOrigin.ASSUMED, ValueOrigin.DECLARED_CONFIG):
        assert pc.rationale.strip()


def test_sih_split_is_declared_config_not_a_scheme_fact(full_doc: DprDocument) -> None:
    for pv in (
        full_doc.financial.required_promoter_margin,
        full_doc.financial.indicated_loan,
        full_doc.financial.financing_scheme,
    ):
        if pv.origin is not ValueOrigin.NOT_AVAILABLE:
            assert pv.origin is ValueOrigin.DECLARED_CONFIG
            assert "not" in pv.rationale.lower() and "scheme" in pv.rationale.lower()


def test_calculations_name_their_inputs(full_doc: DprDocument) -> None:
    calcs = [pv for pv in _walk(full_doc) if pv.origin is ValueOrigin.CALCULATED]
    assert calcs
    assert all(pv.inputs for pv in calcs)


def test_missing_drivers_render_as_not_available(minimal_doc: DprDocument) -> None:
    fin = minimal_doc.financial
    # figures that genuinely need the unstated drivers stay gaps
    for pv in (fin.average_annual_dscr, fin.minimum_cash_balance, fin.cash_break_even_month):
        assert pv.origin is ValueOrigin.NOT_AVAILABLE
    # the declared SIH capacity screen may show a rate — but only as declared config
    assert fin.interest_rate.origin in (ValueOrigin.NOT_AVAILABLE, ValueOrigin.DECLARED_CONFIG)
    assert fin.missing_core_drivers
    assert fin.incomplete_note


def test_assumptions_section_separates_the_four_kinds(full_doc: DprDocument) -> None:
    a = full_doc.assumptions
    assert a.user_inputs
    assert a.calculated_results
    # declared configuration is present because the SIH split was applied
    assert a.declared_configuration
    # every gap is surfaced, not hidden
    assert a.unavailable_evidence == full_doc.evidence_gaps
    labels = {
        i.origin
        for group in (
            a.user_inputs,
            a.assumptions,
            a.calculated_results,
            a.source_facts,
            a.declared_configuration,
        )
        for i in group
    }
    assert "not_available" not in labels
