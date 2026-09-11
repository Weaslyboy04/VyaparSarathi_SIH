"""MANUAL-ONLY live smoke test for the Phase 6 Gemini conversational bridge
(CLAUDE.md §25 Phase 6 Priority 6's test-strategy: "a diagnostic script ...
run manually with the configured Gemini key but never part of normal
automated tests"). Never asserted, never run in CI/pytest — offline
automated coverage for every capability below lives in
`tests/test_llm_structured_output.py`, `tests/test_market_proposed.py`,
`tests/test_llm_language_guard.py`, `tests/test_app_service.py`, and
`tests/test_llm_reply_authoring.py` via `ScriptedLlmProvider` fakes; this
script instead answers "does a REAL model actually behave the way those
fakes assume it will".

Requires the same live-Gemini configuration as `scripts/phase6_chat.py
--llm` (`VYAPAR_LLM_ENABLED=true` plus `VYAPAR_LLM_BASE_URL` or
`VYAPAR_LLM_API_KEY`). Runs NORMAL mode (the real product UX) and feeds one
fixed, realistic transcript through `AdvisoryService` covering: the CLI
greeting, a typo in the business name, location disambiguation, a natural
(non-numeric) choice utterance, several assets in one message, an explicit
"I don't know" decline, a correction including the "90 thousand"/"9
thousand" magnitude fix from this session, a Hinglish message, a user
question ("What loan should I take?"), the one consolidated final advisory,
and an explicit "yes, generate the report" PDF request. Prints each reply
for a human to read and judge — it does not assert anything, since a live
model's exact wording is not something this repo's tests should depend on.
Never prints the API key, any other secret, or a full raw HTTP payload —
only response text/summaries.

For a lower-level, single-call diagnostic of Gemini's raw response shape
(finish reason, thinking-token usage, safety blocks), use
`scripts/phase6_gemini_diag.py` instead — this script is the multi-turn,
end-to-end complement to that single-shot tool, not a replacement for it.
Rate-limit/latency behaviour, if it occurs, is observed opportunistically
here (never deliberately forced against a real billed service); nothing
below retries beyond what `GeminiLlmProvider` already does on its own.

Run (only after this run is approved):

    ./.venv/Scripts/python.exe scripts/phase6_live_smoke.py
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime

import scripts.phase6_chat as chat
from vyaparsarathi.app.dto import ChannelId, MessageRequest, StartSessionRequest
from vyaparsarathi.app.greeting import GREETING_MESSAGE
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import get_settings
from vyaparsarathi.conversation.session_models import SlotState
from vyaparsarathi.database.session_memory import InMemorySessionRepository

# Covers, in order: a typo (Priority 3), location disambiguation, a natural
# (non-numeric) choice utterance (Priority 2), several assets in one message
# with structured detail (Priority 4), an explicit "I don't know" decline (of
# trade experience — the last Tier-A item), a multi-value correction
# including the "N thousand" magnitude fix (Priority 1) that also supplies
# all four viability drivers in one message, Hinglish (Priority 3) added
# after the advisory has already been delivered, a grounded user question
# (Priority 5's honesty-about-missing-evidence path), and finally an explicit
# PDF report request (Phase 6 polish: CLAUDE.md's "ask, then generate on
# request" ordering).
_TRANSCRIPT: list[str] = [
    "Hi, I hav 6.5 lakh and want to start a grocerry shop in Bhagwanpur Bihar.",
    "Vaishali one",
    "I have 2 cows, one small storefront, and a bike.",
    "I don't know",
    "Actually I have 5 lakh, not 6.5. Sales may be 90 thousand a month; "
    "stock costs 82 percent; setup is 5 lakh and monthly expenses are 9 thousand.",
    "mere paas ek dukaan bhi hai",
    "What loan should I take?",
    "yes, generate the report",
]


def main() -> int:
    settings = get_settings()
    if not settings.llm_enabled:
        print("VYAPAR_LLM_ENABLED is not true — nothing to run.", file=sys.stderr)
        return 2
    if not (settings.llm_base_url or settings.llm_api_key is not None):
        print("Neither VYAPAR_LLM_BASE_URL nor VYAPAR_LLM_API_KEY is set.", file=sys.stderr)
        return 2

    print("=" * 72)
    print("MANUAL LIVE SMOKE TEST — real Gemini calls. Never part of CI.")
    print("Read the transcript below; nothing here is asserted automatically.")
    print("No secrets are printed.")
    print("=" * 72)

    runtime = chat._build_runtime(offline=False, use_llm=True, dev=False)  # dev=False -> NORMAL
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    print(GREETING_MESSAGE)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.CLI, started_at=datetime.now(UTC))
    )

    for i, text in enumerate(_TRANSCRIPT, start=1):
        print(f"\n--- turn {i} ---")
        print(f"> {text}")
        started = datetime.now(UTC)
        reply = service.send_message(
            MessageRequest(
                session_id=handle.session_id,
                text=text,
                channel=ChannelId.CLI,
                received_at=started,
            )
        )
        latency_s = (datetime.now(UTC) - started).total_seconds()
        # Every numbered option is already baked into its own message line
        # (see `phase6_chat.py::_print_reply`) — `m.choices` is not reprinted.
        for m in reply.messages:
            print(m.text)
        for w in reply.warnings:
            print(f"[warning] {w}")
        if reply.report is not None:
            print(f"[report: {reply.report.status.value}] {reply.report.message}")
        print(
            f"[{reply.state.value} / {reply.severity.value} / "
            f"expects={reply.expects.value} / {latency_s:.2f}s]"
        )

    print("\n" + "=" * 72)
    print("Final slot state (non-missing only; sanity-check the values below):")
    session = sessions.get(handle.session_id)
    if session is not None:
        for name in sorted(session.slots, key=lambda n: n.value):
            slot = session.slots[name]
            if slot.state is SlotState.MISSING:
                continue
            print(f"  {name.value}: {slot.state.value} = {slot.value}")
        print(
            "  assets: "
            f"{sorted(a.value for a in session.assets.current.items)} "
            f"notes={list(session.assets.current.notes)}"
        )
        print(
            f"  experience: {sorted(c.value for c in session.experience_categories.current.items)}"
        )
        llm_turns = sum(t.llm_used for t in session.turns)
        print(f"  turns where llm_used=True: {llm_turns}/{len(session.turns)}")
    print("=" * 72)
    print(
        "Check by eye: did the greeting print before anything else? Did "
        "'Vaishali one' resolve without repeating the location list, and was "
        "each option shown exactly once? Did the typo 'grocerry' resolve to "
        "grocery? Did 'I have 2 cows, one small storefront, and a bike.' set "
        "all three asset kinds (with notes) from one message? Did plain "
        "'I don't know' decline trade experience rather than confuse the "
        "extractor? Are liquid_cash_inr/monthly_revenue_inr/fixed_opex_inr "
        "the RIGHT magnitude (500000 / 90000 / 9000, not 5/90/9 or "
        "50/900/900)? Did the Hinglish message ('mere paas ek dukaan bhi "
        "hai') extract anything sensible, added AFTER the advisory was "
        "already delivered? Did the loan question answer only from "
        "available evidence rather than inventing a rate? Did the advisory "
        "turn's own text ask 'Would you like me to generate a PDF project "
        "report?', and did 'yes, generate the report' turn 'report:' into "
        "'requested' above without another LLM call?"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
