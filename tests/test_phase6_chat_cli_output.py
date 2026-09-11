"""`scripts/phase6_chat.py`'s terminal rendering — `_print_reply`,
`_start_session`'s greeting (CLAUDE.md §25 Phase 6). Pure w.r.t. the objects
handed in; only stdout is exercised (via `capsys`). No network, no session,
no LLM.
"""

from __future__ import annotations

import pytest
import scripts.phase6_chat as chat

from vyaparsarathi.app.dto import (
    AdvisoryPhase,
    AdvisoryReply,
    Choice,
    OutboundMessage,
)
from vyaparsarathi.app.greeting import GREETING_MESSAGE
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.database.session_memory import InMemorySessionRepository


def _disambiguation_reply() -> AdvisoryReply:
    options = ("Bhagwanpur, Vaishali, Bihar", "Bhagwanpur, Saran, Bihar", "Bhagwanpur, UP")
    choices = tuple(Choice(index=i, label=o) for i, o in enumerate(options, start=1))
    messages = (
        OutboundMessage(text="Several matches were found for location; please choose one."),
        OutboundMessage(text="1. Bhagwanpur, Vaishali, Bihar"),
        OutboundMessage(text="2. Bhagwanpur, Saran, Bihar"),
        OutboundMessage(text="3. Bhagwanpur, UP", choices=choices),
    )
    return AdvisoryReply(
        session_id="s1",
        turn_index=1,
        messages=messages,
        choices=choices,
        state=AdvisoryPhase.BLOCKED,
    )


def test_print_reply_shows_each_disambiguation_option_exactly_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    chat._print_reply(_disambiguation_reply())
    out = capsys.readouterr().out
    for option in (
        "Bhagwanpur, Vaishali, Bihar",
        "Bhagwanpur, Saran, Bihar",
        "Bhagwanpur, UP",
    ):
        assert out.count(option) == 1, f"{option!r} printed {out.count(option)} times:\n{out}"


def test_start_session_prints_greeting_before_session_line(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # `AdvisoryService.start_session` never touches `self._runtime` — a real
    # `AdvisoryRuntime` is not needed to exercise `_start_session`'s printing.
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=None)  # type: ignore[arg-type]
    chat._start_session(service)
    out = capsys.readouterr().out
    lines = out.strip().splitlines()
    assert lines[0] == GREETING_MESSAGE
    assert "started" in lines[1]
