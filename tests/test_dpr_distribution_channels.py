"""Priority 5 wiring: the category-generic distribution-channel guidance
(CLAUDE.md §8, §30) must reach the assembled DPR, always carrying its
"generic, not location-specific" disclaimer. Offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.dpr_pipeline import full_scenario_turns, minimal_turns, run_pipeline
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.provenance import ValueOrigin
from vyaparsarathi.dpr.report_models import SectionStatus

_GEN = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def test_distribution_channels_render_for_a_resolved_category(tmp_path: Path) -> None:
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    sec = doc.distribution_channels
    assert sec.status is SectionStatus.RENDERED
    assert sec.category.origin is ValueOrigin.CALCULATED
    assert sec.primary_channel
    assert sec.secondary_channels
    assert sec.supply_channel
    assert sec.single_channel_risk_note
    assert sec.guidance_note
    assert "generic" in sec.guidance_note.lower() or "not specific" in sec.guidance_note.lower()


def test_distribution_channels_is_a_gap_without_a_resolved_category(tmp_path: Path) -> None:
    """`minimal_turns()` states a business but never runs discovery, so the
    proposed business text has not been mapped to a resolved category yet."""
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_GEN)
    sec = doc.distribution_channels
    if sec.category.origin is ValueOrigin.NOT_AVAILABLE:
        assert sec.status is SectionStatus.EVIDENCE_GAP
        assert not sec.primary_channel
