"""`conversation/report_intent.py` (CLAUDE.md §25 Phase 6/8). Pure; no I/O."""

from __future__ import annotations

import pytest

from vyaparsarathi.conversation.report_intent import ReportIntent, classify_report_intent


@pytest.mark.parametrize(
    "text",
    [
        "yes",
        "Yes!",
        "yeah",
        "sure",
        "ok",
        "okay",
        "please",
        "go ahead",
        "generate the report",
        "generate the PDF please",
        "make a pdf",
        "send the dpr",
        "yes, please generate the report",
        "I need the PDF report",
    ],
)
def test_affirmative_replies(text: str) -> None:
    assert classify_report_intent(text) is ReportIntent.AFFIRM


@pytest.mark.parametrize(
    "text",
    [
        "no",
        "no thanks",
        "not now",
        "not yet",
        "later",
        "skip that",
        "nah",
        "maybe later",
    ],
)
def test_decline_replies(text: str) -> None:
    assert classify_report_intent(text) is ReportIntent.DECLINE


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "actually I have 5 lakh, not 6.5",
        "5 years",
        "I don't know",
        "Bhagwanpur, Bihar",
    ],
)
def test_unrelated_replies_are_unclear(text: str) -> None:
    assert classify_report_intent(text) is ReportIntent.UNCLEAR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
