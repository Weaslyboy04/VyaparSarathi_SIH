"""`scripts/phase6_chat.py`'s `/dpr` command — generate a Detailed Project
Report from a live chat session (CLAUDE.md §25 Phase 8). Fully offline: the
REPL runs with `--offline` and no LLM, and DPR assembly touches no engine,
clock (a fixed `now` is injected), or network.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import scripts.phase6_chat as chat

from tests.dpr_pipeline import full_scenario_turns, run_pipeline
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.dpr import DprOutputExistsError
from vyaparsarathi.dpr.report_models import DprDocument, SectionStatus

_NOW = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)


def _repo_with_session(tmp_path: Path) -> tuple[InMemorySessionRepository, str]:
    session = run_pipeline(full_scenario_turns(), tmp_path=tmp_path)
    repo = InMemorySessionRepository()
    repo.save(session)
    return repo, session.session_id


# --- build_dpr / _dpr_paths (unit) ------------------------------------


def test_build_dpr_writes_a_valid_pdf_and_json(tmp_path: Path) -> None:
    repo, sid = _repo_with_session(tmp_path)
    arts = chat.build_dpr(repo, sid, out_dir=tmp_path / "out", now=_NOW)

    assert arts.pdf_path.exists() and arts.json_path.exists()
    assert arts.pdf_path.read_bytes().startswith(b"%PDF-")
    assert arts.pdf_byte_count > 10_000
    doc = DprDocument.model_validate_json(arts.json_path.read_text(encoding="utf-8"))
    assert doc.report_id == arts.report_id == arts.document.report_id


def test_default_name_is_unique_per_call(tmp_path: Path) -> None:
    repo, sid = _repo_with_session(tmp_path)
    a = chat.build_dpr(repo, sid, out_dir=tmp_path, now=_NOW)
    b = chat.build_dpr(repo, sid, out_dir=tmp_path, now=_NOW.replace(microsecond=5))
    assert a.pdf_path != b.pdf_path
    assert a.pdf_path.exists() and b.pdf_path.exists()


def test_named_output_refuses_silent_overwrite(tmp_path: Path) -> None:
    repo, sid = _repo_with_session(tmp_path)
    chat.build_dpr(repo, sid, "myreport", out_dir=tmp_path, now=_NOW)
    with pytest.raises(DprOutputExistsError):
        chat.build_dpr(repo, sid, "myreport", out_dir=tmp_path, now=_NOW)


def test_dpr_paths_honours_an_explicit_pdf_path(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "report.pdf"
    pdf, js = chat._dpr_paths("sess", str(target), out_dir=tmp_path, now=_NOW)
    assert pdf == target
    assert js == target.with_suffix(".json")


def test_bare_session_still_produces_an_evidence_gap_report(tmp_path: Path) -> None:
    # a session that was started but never given any input
    from vyaparsarathi.app.dto import StartSessionRequest
    from vyaparsarathi.app.runtime import AdvisoryRuntime
    from vyaparsarathi.app.service import AdvisoryService

    runtime = chat._build_runtime(offline=True, use_llm=False, dev=True)
    assert isinstance(runtime, AdvisoryRuntime)
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    from vyaparsarathi.app.dto import ChannelId

    handle = service.start_session(StartSessionRequest(channel=ChannelId.CLI, started_at=_NOW))
    runtime.close()

    arts = chat.build_dpr(sessions, handle.session_id, out_dir=tmp_path, now=_NOW)
    assert arts.pdf_path.read_bytes().startswith(b"%PDF-")
    doc = arts.document
    assert doc.market.status is SectionStatus.EVIDENCE_GAP
    assert doc.evidence_gaps  # nothing hidden


# --- the REPL command end to end ------------------------------------


def _feed(monkeypatch: pytest.MonkeyPatch, lines: list[str]) -> None:
    it: Iterator[str] = iter(lines)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(it))


def test_repl_dpr_command_generates_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(chat, "DPR_OUTPUT_DIR", tmp_path / "build" / "dpr")
    _feed(
        monkeypatch,
        [
            "business: pulses grocery store",
            "location: Bhagwanpur, Bihar",
            "cash: 6.5 lakh",
            "revenue: 90000",
            "cogs: 82%",
            "cost: 5 lakh",
            "opex: 9000",
            "/dpr",
            "/quit",
        ],
    )

    rc = chat.run(offline=True, use_llm=False, dev=True)
    assert rc == 0

    out = capsys.readouterr().out
    assert "DPR generated:" in out
    assert "PDF  :" in out and "JSON :" in out

    pdfs = list((tmp_path / "build" / "dpr").glob("*.pdf"))
    jsons = list((tmp_path / "build" / "dpr").glob("*.json"))
    assert len(pdfs) == 1 and len(jsons) == 1
    assert pdfs[0].read_bytes().startswith(b"%PDF-")
    doc = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert doc["report_id"].startswith("DPR-")
    assert doc["metadata"]["llm_used_in_report"] is False


def test_repl_auto_generates_report_after_an_affirmative_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The explicit-request flow (CLAUDE.md §25 Phase 6): once a delivered
    advisory has asked "would you like a PDF report?", a plain "yes,
    generate the report" turn triggers the same `build_dpr` path `/dpr`
    uses — with no PDF written before that explicit turn."""
    monkeypatch.setattr(chat, "DPR_OUTPUT_DIR", tmp_path / "build" / "dpr")
    _feed(
        monkeypatch,
        [
            "business: pulses grocery store",
            "location: Bhagwanpur, Bihar",
            "cash: 6.5 lakh",
            "revenue: 90000",
            "cogs: 82%",
            "cost: 5 lakh",
            "opex: 9000",
            "yes, generate the report",
            "/quit",
        ],
    )

    rc = chat.run(offline=True, use_llm=False, dev=True)
    assert rc == 0

    out = capsys.readouterr().out
    assert "Would you like me to generate a PDF project report?" in out
    assert "DPR generated:" in out
    assert "PDF  :" in out and "JSON :" in out

    pdfs = list((tmp_path / "build" / "dpr").glob("*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes().startswith(b"%PDF-")


def test_repl_declines_do_not_auto_generate_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(chat, "DPR_OUTPUT_DIR", tmp_path / "build" / "dpr")
    _feed(
        monkeypatch,
        [
            "business: pulses grocery store",
            "location: Bhagwanpur, Bihar",
            "cash: 6.5 lakh",
            "revenue: 90000",
            "cogs: 82%",
            "cost: 5 lakh",
            "opex: 9000",
            "not now",
            "/quit",
        ],
    )

    rc = chat.run(offline=True, use_llm=False, dev=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert "DPR generated:" not in out
    assert not (tmp_path / "build" / "dpr").exists() or not list(
        (tmp_path / "build" / "dpr").glob("*.pdf")
    )


def test_repl_dpr_named_collision_is_reported_not_crashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(chat, "DPR_OUTPUT_DIR", tmp_path)
    _feed(
        monkeypatch,
        [
            "business: grocery",
            "location: Bhagwanpur, Bihar",
            "cash: 6.5 lakh",
            "/dpr myrun",
            "/dpr myrun",
            "/quit",
        ],
    )

    rc = chat.run(offline=True, use_llm=False, dev=True)
    assert rc == 0
    out = capsys.readouterr().out
    assert out.count("DPR generated:") == 1
    assert "[error]" in out and "already exists" in out
    assert (tmp_path / "myrun.pdf").exists()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
