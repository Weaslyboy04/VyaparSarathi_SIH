"""`dpr/fingerprint.py` and the report's determinism guarantee (CLAUDE.md
§23, §28; Phase 8: "the same artifacts generate the same report content").
Offline.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from tests.dpr_pipeline import full_scenario_turns, minimal_turns, run_pipeline, slot
from vyaparsarathi.conversation.session_models import SlotName
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.fingerprint import input_fingerprint, report_id_for
from vyaparsarathi.models.parameters import ValueNormalization

_D1 = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
_D2 = datetime(2031, 1, 2, 18, 30, tzinfo=UTC)


def test_fingerprint_is_stable_for_the_same_session(tmp_path: Path) -> None:
    a = run_pipeline(full_scenario_turns(), tmp_path=tmp_path / "a")
    b = run_pipeline(full_scenario_turns(), tmp_path=tmp_path / "b")
    assert input_fingerprint(a) == input_fingerprint(b)


def test_fingerprint_changes_when_a_stated_value_changes(tmp_path: Path) -> None:
    base = run_pipeline(minimal_turns(), tmp_path=tmp_path / "base")
    changed_turns = [
        (
            slot(
                SlotName.PROPOSED_BUSINESS_TEXT,
                "a pulses grocery store",
                "pulses grocery store",
                ValueNormalization.AS_STATED,
            ),
            slot(
                SlotName.LOCATION_TEXT,
                "in Bhagwanpur, Bihar",
                "Bhagwanpur, Bihar",
                ValueNormalization.AS_STATED,
            ),
            slot(
                SlotName.LIQUID_CASH_INR,
                "I have 9 lakh",
                "9 lakh",
                ValueNormalization.LAKH_TO_INR,
            ),
        )
    ]
    changed = run_pipeline(changed_turns, tmp_path=tmp_path / "changed")
    assert input_fingerprint(base) != input_fingerprint(changed)


def test_report_id_format() -> None:
    assert report_id_for("abcdef0123456789cafe").startswith("DPR-")
    assert report_id_for("abcdef0123456789cafe") == "DPR-ABCDEF0123456789"


def test_report_content_is_identical_across_generation_dates(tmp_path: Path) -> None:
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    d1 = assemble_report(session, generated_at=_D1)
    d2 = assemble_report(session, generated_at=_D2)

    assert d1.report_id == d2.report_id
    assert d1.input_fingerprint == d2.input_fingerprint

    a = d1.model_dump(mode="json")
    b = d2.model_dump(mode="json")
    for blob in (a, b):
        blob.pop("generated_at")
        blob["cover"].pop("generated_on")
        blob["metadata"].pop("generated_at")
    assert a == b


def test_report_id_matches_the_fingerprint(tmp_path: Path) -> None:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    doc = assemble_report(session, generated_at=_D1)
    assert doc.report_id == report_id_for(doc.input_fingerprint)
    assert doc.report_id == report_id_for(input_fingerprint(session))
