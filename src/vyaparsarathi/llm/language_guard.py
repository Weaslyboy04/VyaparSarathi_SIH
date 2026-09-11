"""Detect Devanagari-script numerals this system has made no decision to
parse (CLAUDE.md §3.5, §30 — Phase 6 Priority 3). PURE — no I/O.

**Explicit decision (not yet revisited):** Devanagari digits (०-९) and Hindi
number-words are detected and met with a clear, respectful clarification
asking the user to restate the amount in English digits or words
(lakh/crore/thousand) — never silently parsed. `models/parameters.py::
normalize_value`'s numeral regex is ASCII-digit-only for the same reason
(see its own comment); this module is the conversational-layer counterpart
that catches the case *before* an LLM ever gets a chance to (mis)interpret
it, rather than relying solely on a downstream parse failure.

Romanized Hindi/English code-mixing ("mere paas 5 lakh hain") is NOT
detected here and needs no special handling — it already reaches the LLM
extractor like any other message, and the verbatim-substring check in
`conversation/deltas.py` works over any UTF-8 text regardless of script.
"""

from __future__ import annotations

import re

_DEVANAGARI_DIGIT_RE = re.compile(r"[०-९]")

# A small, explicit, non-exhaustive set of common Hindi number-words in
# Devanagari script. Missing one here is not a safety gap — an undetected
# Hindi number-word still can't fabricate a value, since `normalize_value`'s
# ASCII-only regex would simply fail to find a numeral and the update is
# dropped with a warning (CLAUDE.md's existing graceful-degradation path).
_HINDI_NUMBER_WORDS = frozenset(
    {
        "शून्य",
        "एक",
        "दो",
        "तीन",
        "चार",
        "पांच",
        "पाँच",
        "छह",
        "सात",
        "आठ",
        "नौ",
        "दस",
        "सौ",
        "हज़ार",
        "हजार",
        "लाख",
        "करोड़",
        "करोड",
    }
)

CLARIFICATION_MESSAGE = (
    "I can only read numbers written with English digits or words like "
    "'lakh', 'crore', or 'thousand' right now (for example '90000' or "
    "'6.5 lakh') — could you please resend that amount that way?"
)


def contains_unsupported_devanagari_numeral(text: str) -> bool:
    """True iff `text` contains a Devanagari digit or a common Hindi
    number-word — signals the caller should ask for a restatement instead
    of attempting extraction at all."""
    if _DEVANAGARI_DIGIT_RE.search(text):
        return True
    tokens = set(text.split())
    return bool(tokens & _HINDI_NUMBER_WORDS)


__all__ = ["CLARIFICATION_MESSAGE", "contains_unsupported_devanagari_numeral"]
