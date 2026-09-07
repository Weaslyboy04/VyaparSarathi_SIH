"""Check LLM-generated narrative text against the evidence bundle (CLAUDE.md
§3.1, §5.4, §25 Phase 6). PURE.

Every number, status, and verdict is computed by pure code *before* an LLM
is ever invoked (`conversation/bundle.py`, built from already-run engine
artifacts). This module checks a *rendering* of that data, never derives a
value from one — so even a fully successful prompt-injection cannot change
a fact, a calculation, or a recommendation; its maximum effect is a
narrative that fails this check and is replaced by the deterministic
renderer (`conversation/render.py`).

**Stated limits** (CLAUDE.md §3.5's honesty about uncertainty, applied to
this module itself): a presence check cannot catch *mis-attribution* — a
real number from the bundle, but under the wrong label — or a fabricated
qualitative claim that carries no digits. It narrows, it does not
eliminate. Devanagari digits and Hindi word-numbers are out of scope (this
module's numeral matching is ASCII-digit only, mirroring
`models/parameters.py::_NUMERAL_RE`); the degradation is a template
fallback, which is the honest outcome, not a silent gap.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.bundle import EvidenceBundle

_DIGIT_RUN = re.compile(r"\d[\d,]*(?:\.\d+)?")

# The union of every banned-language list already in this repo
# (scripts/phase3_demo.py:382, scripts/phase4_demo.py:250,
# tests/test_finance_guardrails.py:33-34) — this module applies the same
# discipline to LLM-authored prose that those apply to engine-authored text.
BANNED_PHRASES: frozenset[str] = frozenset(
    {
        "impossible",
        "ineligible",
        "unaffordable",
        "cannot",
        "not allowed",
        "guaranteed",
        "guarantee",
        "eligible",
        "sanctioned",
        "approved",
        "qualifies",
        "will earn",
        "success",
        "probability",
        "safe investment",
        "risk-free",
    }
)


class GroundingResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    reason: str = ""


def _normalized_numerals(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _DIGIT_RUN.finditer(text)}


def check_section(
    text: str, bundle: EvidenceBundle, cited_keys: Sequence[str], *, min_digit_run: int = 1
) -> GroundingResult:
    """`text` is grounded iff (a) it contains no banned phrase, and (b) every
    numeral of `min_digit_run` digits or more in `text` also appears
    (comma-insensitively) in one of the `render` strings of the bundle
    `Fact`s named by `cited_keys` — grounding is scoped PER SECTION by that
    section's own citations, not the whole bundle, which is the single
    biggest lever against a number leaking in from a fact about a different
    section.

    `min_digit_run` defaults to 1 (check every numeral, including a single
    digit like a DSCR of "1.6" or an "11%" rate) — a coarser threshold would
    wave through exactly the short, high-stakes figures this check exists
    to catch. Raise it only for a caller that has a specific, narrower
    reason (e.g. deliberately ignoring bare list markers)."""
    lowered = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in lowered:
            return GroundingResult(accepted=False, reason=f"banned phrase: {phrase!r}")

    allowed_renders = " ".join(fact.render for fact in bundle.facts if fact.key in set(cited_keys))
    allowed_numerals = {n.replace(",", "") for n in _DIGIT_RUN.findall(allowed_renders)}

    for numeral in _normalized_numerals(text):
        if len(numeral) < min_digit_run:
            continue
        if numeral not in allowed_numerals:
            return GroundingResult(
                accepted=False, reason=f"ungrounded numeral {numeral!r} not in cited facts"
            )
    return GroundingResult(accepted=True)


__all__ = ["BANNED_PHRASES", "GroundingResult", "check_section"]
