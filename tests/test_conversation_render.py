"""`conversation/render.py`'s Tier 1 sections — `"scheme_structure"` and
`"swot"` (CLAUDE.md §5.4, §25 Phase 6). Confirms both are built purely from
`bundle.py` `Fact.render` strings, so every numeral in them passes
`grounding.py::check_section` against the same bundle by construction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.conversation.artifacts import record_artifact
from vyaparsarathi.conversation.bundle import build_bundle
from vyaparsarathi.conversation.grounding import check_section
from vyaparsarathi.conversation.planner import NextAction, NextActionKind
from vyaparsarathi.conversation.render import render_reply
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.conversation.swot_models import (
    SwotItem,
    SwotOrigin,
    SwotQuadrant,
    SwotResult,
    SwotStatus,
)
from vyaparsarathi.finance.assessment_models import (
    FinanceLadderRung,
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.finance.structuring_models import SchemeStructureResult, SchemeStructureStatus
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _session_with_structure_and_swot() -> ConversationSession:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now, turn_index=1)

    structure = SchemeStructureResult(
        status=SchemeStructureStatus.STRUCTURED,
        scheme_name="Test Declared Structure",
        category=C.GROCERY,
        project_cost_inr=Decimal("323000.00"),
        required_promoter_margin_inr=Decimal("32300.00"),
        indicated_loan_inr=Decimal("290700.00"),
        margin_shortfall_inr=Decimal("5000.00"),
    )
    session = record_artifact(
        session,
        StepId.STRUCTURE_FINANCE,
        payload=structure.model_dump(mode="json"),
        payload_type="x",
        fingerprint="fp1",
        turn_index=1,
    )

    finance = FinancialAssessmentResult(
        status=FinancialFeasibilityStatus.FEASIBLE,
        rung=FinanceLadderRung.CLEARS_ALL,
        category=C.GROCERY,
    )
    session = record_artifact(
        session,
        StepId.ASSESS_FINANCE,
        payload=finance.model_dump(mode="json"),
        payload_type="x",
        fingerprint="fp2",
        turn_index=1,
    )

    swot = SwotResult(
        status=SwotStatus.OK,
        items=[
            SwotItem(
                code="strength.finance.feasible",
                quadrant=SwotQuadrant.STRENGTH,
                text="The plan clears financing, debt service and stress tests.",
                origin=SwotOrigin.FINANCE,
                source_ref="finance.status",
            ),
            SwotItem(
                code="weakness.structure.margin_shortfall_against_liquid_cash",
                quadrant=SwotQuadrant.WEAKNESS,
                text="The required promoter margin exceeds stated liquid cash.",
                origin=SwotOrigin.STRUCTURE,
                source_ref="structure.findings.margin_shortfall_against_liquid_cash",
            ),
        ],
    )
    session = record_artifact(
        session,
        StepId.SWOT,
        payload=swot.model_dump(mode="json"),
        payload_type="x",
        fingerprint="fp3",
        turn_index=1,
    )
    return session


def test_scheme_structure_section_renders_the_split() -> None:
    session = _session_with_structure_and_swot()
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_FINAL))
    assert "scheme_structure" in narrative.sections
    text = narrative.sections["scheme_structure"]
    assert "Test Declared Structure" in text
    assert "32300" in text or "32,300" in text
    assert "290700" in text or "290,700" in text
    assert narrative.generated_by["scheme_structure"] == "template"


def test_swot_section_lists_every_item() -> None:
    session = _session_with_structure_and_swot()
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_FINAL))
    assert "swot" in narrative.sections
    text = narrative.sections["swot"]
    assert "Strength" in text
    assert "Weakness" in text
    assert "clears financing" in text


def test_every_numeral_in_the_new_sections_is_grounded_in_its_own_bundle() -> None:
    session = _session_with_structure_and_swot()
    bundle = build_bundle(session)
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_FINAL))

    structure_keys = [f.key for f in bundle.facts if f.key.startswith("structure.")]
    result = check_section(narrative.sections["scheme_structure"], bundle, structure_keys)
    assert result.accepted, result.reason

    swot_keys = [f.key for f in bundle.facts if f.key.startswith("swot.")]
    result = check_section(narrative.sections["swot"], bundle, swot_keys)
    assert result.accepted, result.reason


def test_no_discovery_results_surfaces_the_coverage_caution_not_a_failure() -> None:
    """CLAUDE.md §11: absence from the data must never be reported as
    evidence of zero competition. When Overpass/OSM returns nothing usable,
    the delivered advisory says so plainly, without framing it as a system
    failure."""
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now, turn_index=1)
    discovery = DiscoveryResult(
        status=DiscoveryStatus.NO_RESULTS,
        query_text="Bhagwanpur, Bihar",
        category=C.GROCERY,
        requested_radius_m=5000,
    )
    session = record_artifact(
        session,
        StepId.DISCOVER,
        payload=discovery.model_dump(mode="json"),
        payload_type="x",
        fingerprint="fp0",
        turn_index=1,
    )
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_PARTIAL))
    assert "market_coverage_gap" in narrative.sections
    text = narrative.sections["market_coverage_gap"]
    assert "could not observe enough nearby-business data" in text
    assert "does not mean there are no competitors" in text


def test_discovery_ok_status_never_shows_the_coverage_caution() -> None:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now, turn_index=1)
    discovery = DiscoveryResult(
        status=DiscoveryStatus.OK,
        query_text="Bhagwanpur, Bihar",
        category=C.GROCERY,
        requested_radius_m=5000,
    )
    session = record_artifact(
        session,
        StepId.DISCOVER,
        payload=discovery.model_dump(mode="json"),
        payload_type="x",
        fingerprint="fp0",
        turn_index=1,
    )
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_PARTIAL))
    assert "market_coverage_gap" not in narrative.sections


def test_no_structure_or_swot_artifact_omits_both_sections() -> None:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now)
    lines, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_PARTIAL))
    assert "scheme_structure" not in narrative.sections
    assert "swot" not in narrative.sections


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
