"""Phase 6 demo scenario as a gate test (CLAUDE.md §25 Phase 6, §31, §32).

Mirrors `tests/test_phase5_demo.py`'s relationship to `scripts/phase5_demo.py`:
imports the same builders the demo script uses so the demo and the gate
assert exactly the same behaviour.
"""

from __future__ import annotations

import pytest
from scripts.phase6_demo import _FIXTURES_KNOWLEDGE, _empty_corpus_dir, check_common, run_transcript

from vyaparsarathi.app.dto import AdvisoryPhase
from vyaparsarathi.conversation.session_models import StepId


def test_phase6_demo_has_no_failing_invariants() -> None:
    assert check_common() == []


def test_phase6_demo_transcript_a_never_states_an_interest_rate() -> None:
    replies, session = run_transcript(_empty_corpus_dir())
    all_text = " ".join(m.text for r in replies for m in r.messages).lower()
    assert "interest rate" not in all_text
    knowledge = session.artifacts.get(StepId.FINANCE_KNOWLEDGE)
    assert knowledge is not None
    resolutions = knowledge.payload.get("resolutions", [])
    assert all(r.get("status") != "resolved" for r in resolutions)


def test_phase6_demo_transcript_b_resolves_at_least_one_parameter() -> None:
    replies, session = run_transcript(_FIXTURES_KNOWLEDGE)
    assert replies
    knowledge = session.artifacts.get(StepId.FINANCE_KNOWLEDGE)
    assert knowledge is not None
    resolutions = knowledge.payload.get("resolutions", [])
    assert any(r.get("status") == "resolved" for r in resolutions)


def test_phase6_demo_reaches_a_terminal_state_deterministically() -> None:
    replies_1, _ = run_transcript(_empty_corpus_dir())
    replies_2, _ = run_transcript(_empty_corpus_dir())
    # session_id is a fresh UUID per run (app/dto.py::new_session_id); every
    # other field must still be byte-identical across runs.
    dump_1 = [r.model_dump(mode="json", exclude={"session_id"}) for r in replies_1]
    dump_2 = [r.model_dump(mode="json", exclude={"session_id"}) for r in replies_2]
    assert dump_1 == dump_2
    assert replies_1[-1].state in set(AdvisoryPhase)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
