"""Citation rendering: every `citation_id` a value references resolves to a
real `Citation`, and sourced scheme parameters carry one (CLAUDE.md §19, §23;
Phase 8). Offline. Uses the shipped real (small) knowledge corpus so at least
one scheme parameter genuinely resolves.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.dpr_pipeline import full_scenario_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import ProvenancedValue, ValueOrigin
from vyaparsarathi.dpr.report_models import DprDocument
from vyaparsarathi.models.parameters import ResolutionStatus

_REPO = Path(__file__).resolve().parent.parent
_REAL_CORPUS = _REPO / "data" / "knowledge"
_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def _walk_pvs(model: object) -> list[ProvenancedValue]:
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
def doc(tmp_path: Path) -> DprDocument:
    session = run_pipeline(
        full_scenario_turns(), tmp_path=tmp_path, knowledge_corpus_dir=_REAL_CORPUS
    )
    return assemble_report(session, generated_at=_GEN)


def test_every_referenced_citation_id_resolves(doc: DprDocument) -> None:
    referenced = {
        pv.citation_id
        for pv in _walk_pvs(doc)
        if pv.origin is ValueOrigin.SOURCED and pv.citation_id
    }
    # every value that claims a citation must have one in the table
    assert referenced
    assert referenced <= set(doc.citations), (
        f"dangling citation ids: {referenced - set(doc.citations)}"
    )


def test_discovery_and_census_are_cited(doc: DprDocument) -> None:
    assert "DS" in doc.citations  # OpenStreetMap business sample
    assert "CEN" in doc.citations  # Census population extract
    assert "openstreetmap" in doc.citations["DS"].text.lower()
    assert "census" in doc.citations["CEN"].text.lower()
    # the DS citation is honest about being a partial sample
    assert "partial" in doc.citations["DS"].text.lower()


def test_a_resolved_scheme_parameter_carries_a_citation(doc: DprDocument) -> None:
    resolved = [
        line
        for line in (
            *doc.scheme_knowledge.resolved_parameters,
            *doc.scheme_knowledge.statutory_fees,
        )
        if line.status == ResolutionStatus.RESOLVED.value
    ]
    assert resolved, "the shipped corpus should resolve at least one scheme parameter"
    for line in resolved:
        assert line.citation_id and line.citation_id in doc.citations
        assert line.value.origin is ValueOrigin.SOURCED
        cite = doc.citations[line.citation_id]
        assert cite.tier  # source tier recorded
        assert cite.locator  # a section/page pointer, not just a title


def test_annexure_sources_are_sorted_and_self_contained(doc: DprDocument) -> None:
    ids = [c.citation_id for c in doc.annexures.sources]
    assert ids == sorted(ids)
    assert ids
    for c in doc.annexures.sources:
        assert c.text.strip()


def test_unresolved_scheme_parameters_say_no_evidence(doc: DprDocument) -> None:
    unresolved = [
        line
        for line in doc.scheme_knowledge.resolved_parameters
        if line.status != ResolutionStatus.RESOLVED.value
    ]
    for line in unresolved:
        assert line.value.origin is ValueOrigin.NOT_AVAILABLE
        assert line.name in doc.scheme_knowledge.no_evidence_parameters
