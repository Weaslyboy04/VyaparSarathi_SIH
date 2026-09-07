"""Negative invariants for Phase 5 (CLAUDE.md §3.5, §18, §22, §23, §30).
Mirrors the guardrail style in `tests/test_finance_guardrails.py`, and closes
the one gap that file leaves open (see below). Pure & offline."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from tests.test_knowledge_to_finance import _provided, _query, _unbound_plan
from vyaparsarathi.discovery.knowledge_acquisition import acquire_finance_knowledge
from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.knowledge.plan_binding import bind_sourced_inputs, build_loan_terms
from vyaparsarathi.models.finance import InputKind, MoratoriumTreatment, Unit
from vyaparsarathi.models.parameters import ParameterName, ParameterQuery
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"

# The same banned-substring lists tests/test_finance_guardrails.py applies to
# Phase 4's own text fields.
_BANNED_KEY_SUBSTRINGS = ("score", "probability", "success", "guarantee")
_BANNED_TEXT_SUBSTRINGS = (
    "eligible",
    "qualifies",
    "sanctioned",
    "approved",
    "guaranteed",
    "will earn",
    "impossible",
    "unaffordable",
)

_SOURCE_REF_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*#[A-Za-z0-9/._-]+$")


def _all_keys(obj: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


def _all_string_values(obj: object) -> list[str]:
    """Every string *value* anywhere in a nested JSON-ish structure — the
    check `tests/test_finance_guardrails.py::_text_blob` does NOT do: that
    function scans only four hand-picked fields
    (`caveats`/`warnings`/`breaking_point`/`findings[].message`/
    `stress_results[].description`), so a banned word sitting in, say, a
    `source_ref` *value* would slip through today. This walks everything."""
    found: list[str] = []
    if isinstance(obj, str):
        found.append(obj)
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_all_string_values(v))
    elif isinstance(obj, list):
        for item in obj:
            found.extend(_all_string_values(item))
    return found


def _evidence(*, corpus_dir=FIXTURES):
    store = FileCorpusStore(corpus_dir)
    return acquire_finance_knowledge(
        _query(), corpus=store, clock=lambda: datetime(2026, 1, 20, tzinfo=UTC)
    )


# ======================================================================
# Phase 5 never emits ASSUMED, never fabricates on NO_EVIDENCE
# ======================================================================


def test_every_bound_financial_input_is_sourced() -> None:
    evidence = _evidence()
    bound = bind_sourced_inputs(_unbound_plan(), evidence)
    assert bound.bound  # sanity: the fixture corpus does bind something
    for b in bound.bound:
        assert b.financial_input.kind is InputKind.SOURCED


def test_loan_terms_financial_inputs_are_all_sourced() -> None:
    evidence = _evidence()
    loan, _ = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    assert loan is not None
    assert loan.interest_rate_pct.kind is InputKind.SOURCED
    assert loan.tenure_months.kind is InputKind.SOURCED
    assert loan.moratorium_months.kind is InputKind.SOURCED


def test_no_evidence_never_produces_a_financial_input() -> None:
    query = ParameterQuery(names=(ParameterName.COGS_PCT,), as_of=date(2026, 1, 1))
    store = FileCorpusStore(FIXTURES)
    evidence = acquire_finance_knowledge(
        query, corpus=store, clock=lambda: datetime(2026, 1, 20, tzinfo=UTC)
    )
    resolution = evidence.resolutions[0]
    assert resolution.status.value == "no_evidence"
    bound = bind_sourced_inputs(_unbound_plan(), evidence)
    assert bound.bound == ()


def test_source_ref_is_always_a_structured_locator_never_prose() -> None:
    evidence = _evidence()
    for resolution in evidence.resolutions:
        if resolution.status.value == "resolved":
            assert _SOURCE_REF_RE.match(resolution.source_ref), resolution.source_ref


# ======================================================================
# no Phase 5 model field carries a banned-substring name
# ======================================================================


def test_no_knowledge_model_field_is_named_like_a_verdict() -> None:
    from vyaparsarathi.models import knowledge as knowledge_models
    from vyaparsarathi.models import parameters as parameter_models

    banned = ("score", "probability", "success", "guarantee")
    offenders: list[str] = []
    for module in (knowledge_models, parameter_models):
        for name in dir(module):
            obj = getattr(module, name)
            model_fields = getattr(obj, "model_fields", None)
            if not isinstance(model_fields, dict):
                continue
            for field_name in model_fields:
                if any(b in field_name.lower() for b in banned):
                    offenders.append(f"{module.__name__}.{name}.{field_name}")
    assert not offenders, offenders


# ======================================================================
# the value-scanning gap-closer — the full serialized Phase 4 result
# ======================================================================


def test_bound_assessment_result_never_carries_a_banned_word_in_any_string_value() -> None:
    evidence = _evidence()
    bound = bind_sourced_inputs(_unbound_plan(), evidence)
    loan, _ = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    final_plan = bound.plan.model_copy(
        update={"financing": bound.plan.financing.model_copy(update={"loan": loan})}
    )
    result = assess_financials(final_plan)
    dumped = result.model_dump(mode="json")

    blob = " ".join(_all_string_values(dumped)).lower()
    for banned in _BANNED_TEXT_SUBSTRINGS:
        assert banned not in blob, banned

    keys = _all_keys(dumped)
    for banned in _BANNED_KEY_SUBSTRINGS:
        assert not any(banned in k.lower() for k in keys), banned


# Deliberately NOT applied to FinanceKnowledgeEvidence as a whole: its
# `passages`/`resolutions[].chosen.evidence_quote`/`.citation` fields
# legitimately carry VERBATIM quoted text from real scheme documents, which
# routinely and correctly uses words like "sanctioned" or "eligible" to
# describe the scheme's own rules (e.g. "the loan sanctioned under this
# scheme shall carry interest at X%"). Banning them there would forbid
# showing evidence verbatim — the opposite of CLAUDE.md §18/§19's intent.
# The guardrail that actually matters is that this prose never reaches
# FinancialPlanInput / FinancialAssessmentResult, which the two tests above
# and test_citation_never_reaches_the_bound_plan below both verify.


# ======================================================================
# citations never enter FinancialPlanInput; only structured locators do
# ======================================================================


def test_citation_never_reaches_the_bound_plan() -> None:
    evidence = _evidence()
    bound = bind_sourced_inputs(_unbound_plan(), evidence)
    dumped = bound.plan.model_dump(mode="json")
    blob = " ".join(_all_string_values(dumped))
    # the human-readable citation string contains an em dash joining title and
    # publisher — that shape should never appear inside the plan itself.
    for resolution in evidence.resolutions:
        if resolution.citation:
            assert resolution.citation not in blob


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
