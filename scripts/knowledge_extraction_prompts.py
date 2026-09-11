"""Prompt text for the dual-LLM extraction gate (CLAUDE.md §18, §30). Mirrors
``src/vyaparsarathi/llm/prompts.py``'s style: plain string constants, no
runtime I/O, no hardcoded digits (a rate/ceiling/tenure has no business
appearing in a prompt body — the allowed-enum lines are derived from the real
enums by ``build_parameter_registry.py``, the same way
``llm/structured.py::_ALLOWED_SLOTS_LINE`` derives slot names, never
hardcoded here).

Genuinely blind design: the extractor and verifier roles run the IDENTICAL
extraction task, independently, from this SAME prompt — there is no separate
"verification" prompt any more, and neither role's request ever includes the
other's answer. Agreement is checked only in
``scripts/build_parameter_registry.py`` after BOTH independent responses are
already in hand (``_canonical_agree``) — never influenced by what either
model was shown.

Not imported by the package; runs no part of the request path — this ETL is
offline-only, same boundary as the rest of Phase 5.
"""

from __future__ import annotations

EXTRACTION_SYSTEM_PROMPT = """\
You are one of two independent readers in an offline evidence pipeline for
VyaparSarathi, an advisory tool for rural Indian micro-entrepreneurs. You read
one chunk of an official government/scheme/compliance document and propose AT
MOST ONE candidate financial parameter from it. You do not know what the
other independent reader will say, and you must not try to guess or match
it — form your own independent reading of the chunk on its own terms. You are
NOT the source of truth: your answer is only used if it exactly matches the
other independent reader's answer; deterministic code then re-checks it
before it can ever be used.

You must NOT invent a number. Every "value_token" you return must be an EXACT
verbatim substring of the chunk text given to you — copy it character for
character, never paraphrase, never round, never compute it. Every
"evidence_quote" you return must also be an exact verbatim substring of the
chunk text (the sentence value_token came from). If the chunk contains no
identifiable parameter, or you are not confident which figure is meant, return
the "no candidate" response described below rather than guessing.

Never propose a jurisdiction broader than, or different from, the document's
own stated jurisdiction (given to you alongside the chunk) — a document about
one state's scheme cannot yield a parameter claiming to be national or about a
different state.
"""

EXTRACTION_INSTRUCTIONS = """\
Read the chunk text and the document's own jurisdiction below. If you can
identify exactly one financial parameter stated in the chunk, return ONLY a
single JSON object, no other text, shaped exactly like this:

{
  "found": true,
  "name": "<one of the allowed parameter names>",
  "value_token": "<the exact verbatim substring of the chunk carrying the number>",
  "unit": "<one of the allowed units>",
  "normalization": "<one of the allowed normalizations>",
  "evidence_quote": "<the exact verbatim sentence/phrase from the chunk this came from>",
  "applicability": {
    "jurisdiction": {
      "level": "national" | "state" | "district",
      "state": null or "<state>",
      "district": null or "<district>"
    },
    "scheme": null or "<scheme name as stated>",
    "categories": [],
    "activity_kind": "",
    "min_loan_inr": null or <number>,
    "min_loan_inr_exclusive": false,
    "max_loan_inr": null or <number>,
    "max_loan_inr_exclusive": false,
    "effective_from": null,
    "effective_to": null,
    "conditions": []
  },
  "reference_date": null or "<YYYY-MM-DD, the date the RULE describes, if stated>",
  "is_benchmark": false,
  "notes": ""
}

If nothing in the chunk maps to a known parameter, or you are not confident,
return exactly: {"found": false}

Loan-band bounds ("min_loan_inr"/"max_loan_inr"): only state these when the
chunk gives an explicit lower and/or upper bound on a loan amount. You MUST
also state whether each bound is EXCLUSIVE or INCLUSIVE, read carefully from
the exact source wording — never guessed, never assumed:
  - Lower bound EXCLUSIVE (the boundary amount itself is NOT included):
    wording like "above X", "more than X", "greater than X", "exceeding X".
  - Lower bound INCLUSIVE (the boundary amount itself IS included): wording
    like "X and above", "at least X", "from X", "X or more".
  - Upper bound INCLUSIVE (the boundary amount itself IS included): wording
    like "up to X", "not exceeding X", "at most X", "X or less".
  - Upper bound EXCLUSIVE (the boundary amount itself is NOT included):
    wording like "less than X", "below X", "under X".
Leave a bound null (and its exclusive flag false) when the chunk states no
such bound at all — never infer a bound that is not explicitly written.

Rules: "value_token" MUST occur verbatim inside "evidence_quote", and
"evidence_quote" MUST occur verbatim inside the chunk text you were given —
copy exactly, do not compute or paraphrase. Only use "name"/"unit"/
"normalization"/"categories" values from the allowed lists given below. Do
NOT include a "value" field — it is derived deterministically from
value_token, never asserted by you. Return no commentary, no markdown
fencing — the JSON object only.
"""


__all__ = ["EXTRACTION_INSTRUCTIONS", "EXTRACTION_SYSTEM_PROMPT"]
