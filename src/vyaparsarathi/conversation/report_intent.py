"""Deterministic yes/no classification for the "would you like a PDF report?"
offer (CLAUDE.md §25 Phase 6/8). PURE — no LLM, no I/O.

DPR generation must stay composition-only and never depend on the LLM
pipeline (CLAUDE.md §18, §30), so this is a plain keyword/regex classifier,
not a new `Intent` value or LLM JSON-schema field. It is only ever consulted
by `app/service.py::AdvisoryService.send_message` at the one moment a bare
"yes"/"no" is unambiguous: after a `decide()` peek already shows
`DELIVER_FINAL`/`DELIVER_PARTIAL` — nothing else is pending, so there is
nothing else the reply could be answering.
"""

from __future__ import annotations

import re
from enum import StrEnum

_AFFIRM_WORDS = re.compile(
    r"^\s*(yes|yeah|yep|yup|sure|ok(ay)?|please|go ahead|do it)\.?\s*!?\s*$", re.IGNORECASE
)
_AFFIRM_PHRASES = re.compile(
    r"\b(generate|make|create|send|prepare|give me|need)\b.{0,40}\b(pdf|dpr|report)\b"
    r"|\b(pdf|dpr|report)\b.{0,40}\b(generate|make|create|send|prepare)\b"
    r"|\byes\b.{0,20}\b(pdf|dpr|report)\b",
    re.IGNORECASE,
)
_DECLINE_WORDS = re.compile(
    r"^\s*(no|nah|nope|not (now|yet|today)|later|skip( that)?|"
    r"no thanks|don'?t need (it|one)|maybe later)\.?\s*$",
    re.IGNORECASE,
)


class ReportIntent(StrEnum):
    AFFIRM = "affirm"
    DECLINE = "decline"
    UNCLEAR = "unclear"


def classify_report_intent(text: str) -> ReportIntent:
    """Classify a reply to the "would you like a PDF report?" offer.
    Deliberately conservative: anything not clearly a yes/no about a report
    is `UNCLEAR`, so the caller falls back to the normal conversation
    pipeline rather than mis-firing on an unrelated message."""
    stripped = text.strip()
    if not stripped:
        return ReportIntent.UNCLEAR
    if _DECLINE_WORDS.match(stripped):
        return ReportIntent.DECLINE
    if _AFFIRM_WORDS.match(stripped) or _AFFIRM_PHRASES.search(stripped):
        return ReportIntent.AFFIRM
    return ReportIntent.UNCLEAR


__all__ = ["ReportIntent", "classify_report_intent"]
