"""`knowledge/resolver.py::resolve_parameters` — the precedence ladder
(CLAUDE.md §18, §22, §30). Pure & offline; uses the shared synthetic fixture
corpus (`tests/fixtures/knowledge/`, all invented — see its README)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG
from vyaparsarathi.knowledge.resolver import resolve_parameters
from vyaparsarathi.models.parameters import ParameterName, ParameterQuery, ResolutionStatus
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore

FIXTURES = Path(__file__).parent / "fixtures" / "knowledge"
_AS_OF = date(2026, 1, 1)


@pytest.fixture(scope="module")
def corpus() -> tuple[list, dict]:
    store = FileCorpusStore(FIXTURES)
    docs = {d.document_id: d for d in store.documents()}
    return list(store.parameters()), docs


def _resolve_one(corpus, name: ParameterName, **kw) -> object:
    params, docs = corpus
    query = ParameterQuery(names=(name,), as_of=_AS_OF, **kw)
    return resolve_parameters(query, params, docs)[0]


# ======================================================================
# tier beats specificity, specificity beats date — the happy path
# ======================================================================


def test_state_specific_rate_beats_national_baseline(corpus) -> None:
    res = _resolve_one(
        corpus,
        ParameterName.INTEREST_RATE_PCT,
        state="Bihar",
        scheme="test-rural-udyog-yojana",
    )
    assert res.status is ResolutionStatus.RESOLVED
    assert res.chosen.value == Decimal("10.5")
    assert res.chosen.document_id == "test-bihar-state-guideline"


def test_national_fallback_resolves_with_a_note_when_no_state_row_exists(corpus) -> None:
    res = _resolve_one(
        corpus,
        ParameterName.LOAN_TENURE_MONTHS,
        state="Bihar",
        scheme="test-rural-udyog-yojana",
    )
    assert res.status is ResolutionStatus.RESOLVED
    assert res.chosen.document_id == "test-national-credit-guideline"
    assert any("NATIONAL" in n for n in res.notes)
    # the national fallback penalty must show up in the reported confidence
    assert res.confidence_basis["applicability_match"] < 1.0


def test_no_scheme_filter_still_resolves_a_scheme_specific_row(corpus) -> None:
    # Querying without a scheme must not reject a scheme-specific candidate —
    # only an explicit disagreement rejects (module docstring, filter 4).
    # LICENCE_FEE_INR has exactly one unambiguous candidate in the corpus, so
    # this isolates "unset query side never rejects" from the separate
    # question of ranking among several matching candidates (covered by
    # test_conflicting_same_tier_same_specificity_different_values).
    res = _resolve_one(corpus, ParameterName.LICENCE_FEE_INR, category=C.GROCERY)
    assert res.status is ResolutionStatus.RESOLVED
    assert res.chosen.applicability.jurisdiction.state == "Bihar"


def test_multiple_schemes_with_no_scheme_filter_can_legitimately_conflict(corpus) -> None:
    # This corpus deliberately has THREE independent interest-rate schemes in
    # Bihar (test-rural-udyog-yojana, test-conflict-scheme x2). Without a
    # scheme filter, all three compete; the two most recent (the conflict
    # pair) tie at the top rank and disagree — CONFLICTING is the honest
    # answer, not a guess at which scheme the caller meant.
    res = _resolve_one(corpus, ParameterName.INTEREST_RATE_PCT, state="Bihar")
    assert res.status is ResolutionStatus.CONFLICTING


# ======================================================================
# statutory fee / benchmark
# ======================================================================


def test_statutory_licence_fee_resolves(corpus) -> None:
    res = _resolve_one(corpus, ParameterName.LICENCE_FEE_INR, state="Bihar", category=C.GROCERY)
    assert res.status is ResolutionStatus.RESOLVED
    assert res.chosen.value == Decimal("500")


def test_industry_benchmark_resolves_from_industry_body_tier(corpus) -> None:
    res = _resolve_one(corpus, ParameterName.INVENTORY_DAYS, category=C.GROCERY)
    assert res.status is ResolutionStatus.RESOLVED
    assert res.chosen.tier.value == "industry_body"


def test_gross_margin_has_no_evidence_anywhere_in_the_corpus(corpus) -> None:
    # The deliberate absence: no document in this corpus states a grocery
    # gross margin — the system must say so, not invent one.
    res = _resolve_one(corpus, ParameterName.GROSS_MARGIN_PCT, category=C.GROCERY)
    assert res.status is ResolutionStatus.NO_EVIDENCE
    assert res.chosen is None


# ======================================================================
# the four "does not fabricate" statuses
# ======================================================================


def test_conflicting_same_tier_same_specificity_different_values(corpus) -> None:
    res = _resolve_one(
        corpus, ParameterName.INTEREST_RATE_PCT, state="Bihar", scheme="test-conflict-scheme"
    )
    assert res.status is ResolutionStatus.CONFLICTING
    assert res.chosen is None
    assert res.confidence is None
    values = {c.value for c in res.candidates if c.document_id.startswith("test-conflict-doc")}
    assert values == {Decimal("11.0"), Decimal("13.0")}


def test_stale_expired_rule_is_stale_only_not_resolved(corpus) -> None:
    res = _resolve_one(corpus, ParameterName.SUBSIDY_PCT, state="Bihar", scheme="test-stale-scheme")
    assert res.status is ResolutionStatus.STALE_ONLY
    assert res.chosen is None


def test_unresolved_conditions_is_held_back(corpus) -> None:
    res = _resolve_one(
        corpus,
        ParameterName.SECURITY_DEPOSIT_MONTHS,
        state="Bihar",
        scheme="test-conditional-scheme",
    )
    assert res.status is ResolutionStatus.CONDITIONS_UNRESOLVED
    assert res.chosen is None


def test_secondary_tier_never_supplies_a_binding_rate() -> None:
    # sources/knowledge/loader.py already excludes SECONDARY-tier rows for a
    # tier-gated name at load time (defense in depth: parameters_rejected_
    # tier_floor, see test_knowledge_corpus_loader.py), so the fixture corpus
    # loaded through FileCorpusStore never even offers one to the resolver.
    # This test isolates the RESOLVER's *own* tier-floor check by handing it
    # a SECONDARY-tier candidate directly, bypassing the loader.
    from datetime import UTC, datetime

    from vyaparsarathi.models.finance import Unit
    from vyaparsarathi.models.knowledge import (
        ChunkLocator,
        DocumentRecord,
        Jurisdiction,
        JurisdictionLevel,
        SourceTier,
    )
    from vyaparsarathi.models.parameters import (
        Applicability,
        SourcedParameter,
        ValueNormalization,
    )

    secondary_doc = DocumentRecord(
        document_id="test-standalone-secondary",
        title="Synthetic Standalone Secondary Commentary (test fixture)",
        publisher="Fictional test publisher",
        tier=SourceTier.SECONDARY,
        jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL),
        published_on=date(2025, 1, 1),
        retrieved_at=datetime(2026, 1, 15, tzinfo=UTC),
        content_sha256="b" * 64,
    )
    secondary_param = SourcedParameter(
        parameter_id="test-standalone-secondary:interest_rate_pct:1",
        name=ParameterName.INTEREST_RATE_PCT,
        value=Decimal("9.0"),
        unit=Unit.PERCENT_PER_ANNUM,
        value_token="9.0%",
        normalization=ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        evidence_quote="A blog claims the rate is 9.0% per annum.",
        document_id="test-standalone-secondary",
        chunk_id="test-standalone-secondary#s1",
        locator=ChunkLocator(section="1"),
        tier=SourceTier.SECONDARY,
        applicability=Applicability(jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL)),
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )

    query = ParameterQuery(names=(ParameterName.INTEREST_RATE_PCT,), as_of=_AS_OF)
    res = resolve_parameters(
        query, [secondary_param], {"test-standalone-secondary": secondary_doc}
    )[0]
    assert res.status is ResolutionStatus.NO_EVIDENCE
    assert res.chosen is None
    reasons = " ".join(res.rejected_reasons.values())
    assert "tier" in reasons


# ======================================================================
# loan-band inclusive/exclusive boundaries — MUDRA Kishor's real shape:
# "above Rs. 50,000 and up to Rs. 5 lakh" (exclusive lower, inclusive upper)
# ======================================================================


def _kishor_band_param() -> object:
    from vyaparsarathi.models.finance import Unit
    from vyaparsarathi.models.knowledge import (
        ChunkLocator,
        Jurisdiction,
        JurisdictionLevel,
        SourceTier,
    )
    from vyaparsarathi.models.parameters import Applicability, SourcedParameter, ValueNormalization

    return SourcedParameter(
        parameter_id="test-mudra-kishor:loan_ceiling_inr:1",
        name=ParameterName.LOAN_CEILING_INR,
        value=Decimal("500000"),
        unit=Unit.INR,
        value_token="5 lakh",
        normalization=ValueNormalization.LAKH_TO_INR,
        evidence_quote="Kishor: covering loans above Rs. 50,000 and up to Rs. 5 lakh.",
        document_id="test-mudra-kishor",
        chunk_id="test-mudra-kishor#s2",
        locator=ChunkLocator(section="2"),
        tier=SourceTier.GOVT_PRIMARY,
        applicability=Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL),
            scheme="test-mudra-kishor",
            min_loan_inr=Decimal("50000"),
            min_loan_inr_exclusive=True,
            max_loan_inr=Decimal("500000"),
            max_loan_inr_exclusive=False,
        ),
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )


def _resolve_kishor_at(amount: Decimal) -> ResolutionStatus:
    param = _kishor_band_param()
    query = ParameterQuery(
        names=(ParameterName.LOAN_CEILING_INR,),
        scheme="test-mudra-kishor",
        loan_amount_inr=amount,
        as_of=_AS_OF,
    )
    res = resolve_parameters(query, [param], {})[0]
    return res.status


def test_loan_band_exclusive_lower_bound_rejects_the_boundary_amount() -> None:
    # Rs. 50,000 itself is EXCLUDED ("above Rs. 50,000") — it belongs to
    # Shishu, not Kishor.
    assert _resolve_kishor_at(Decimal("50000")) is ResolutionStatus.NO_EVIDENCE


def test_loan_band_exclusive_lower_bound_accepts_one_rupee_above() -> None:
    assert _resolve_kishor_at(Decimal("50001")) is ResolutionStatus.RESOLVED


def test_loan_band_inclusive_upper_bound_accepts_the_boundary_amount() -> None:
    # Rs. 5,00,000 itself IS included ("up to Rs. 5 lakh").
    assert _resolve_kishor_at(Decimal("500000")) is ResolutionStatus.RESOLVED


def test_loan_band_inclusive_upper_bound_rejects_one_rupee_above() -> None:
    assert _resolve_kishor_at(Decimal("500001")) is ResolutionStatus.NO_EVIDENCE


def test_no_row_at_all_for_a_name_is_no_evidence(corpus) -> None:
    params, docs = corpus
    query = ParameterQuery(names=(ParameterName.COGS_PCT,), as_of=_AS_OF)
    res = resolve_parameters(query, params, docs)[0]
    assert res.status is ResolutionStatus.NO_EVIDENCE
    assert res.candidates == []


# ======================================================================
# agreement across independent documents
# ======================================================================


def test_agreeing_documents_are_recorded_and_boost_confidence(corpus) -> None:
    from datetime import UTC, datetime

    from vyaparsarathi.models.finance import Unit
    from vyaparsarathi.models.knowledge import (
        ChunkLocator,
        DocumentRecord,
        Jurisdiction,
        JurisdictionLevel,
        SourceTier,
    )
    from vyaparsarathi.models.parameters import (
        Applicability,
        SourcedParameter,
        ValueNormalization,
    )

    params, docs = corpus
    extra_doc = DocumentRecord(
        document_id="test-agreeing-doc",
        title="Synthetic Agreeing Circular (test fixture)",
        publisher="Fictional Rural Credit Bureau (synthetic test fixture)",
        tier=SourceTier.GOVT_PRIMARY,
        jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        published_on=date(2025, 1, 1),
        retrieved_at=datetime(2026, 1, 15, tzinfo=UTC),
        content_sha256="a" * 64,
    )
    quote = "Under this synthetic agreeing circular, the rate is 10.5% per annum."
    extra_param = SourcedParameter(
        parameter_id="test-agreeing-doc:interest_rate_pct:1",
        name=ParameterName.INTEREST_RATE_PCT,
        value=Decimal("10.5"),
        unit=Unit.PERCENT_PER_ANNUM,
        value_token="10.5%",
        normalization=ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        evidence_quote=quote,
        document_id="test-agreeing-doc",
        chunk_id="test-agreeing-doc#s1",
        locator=ChunkLocator(section="1"),
        tier=SourceTier.GOVT_PRIMARY,
        applicability=Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
            scheme="test-rural-udyog-yojana",
        ),
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )

    query = ParameterQuery(
        names=(ParameterName.INTEREST_RATE_PCT,),
        state="Bihar",
        scheme="test-rural-udyog-yojana",
        as_of=_AS_OF,
    )
    without = resolve_parameters(query, params, docs)[0]
    with_agreement = resolve_parameters(
        query, [*params, extra_param], {**docs, "test-agreeing-doc": extra_doc}
    )[0]

    assert without.agreeing_document_ids == ("test-bihar-state-guideline",)
    assert set(with_agreement.agreeing_document_ids) == {
        "test-bihar-state-guideline",
        "test-agreeing-doc",
    }
    assert with_agreement.confidence >= without.confidence


# ======================================================================
# structural invariants
# ======================================================================


def test_resolved_source_ref_matches_the_locator_regex(corpus) -> None:
    import re

    res = _resolve_one(corpus, ParameterName.LICENCE_FEE_INR, state="Bihar", category=C.GROCERY)
    assert re.fullmatch(r"[a-z0-9][a-z0-9._-]*#[A-Za-z0-9/._-]+", res.source_ref)


def test_resolver_is_deterministic_across_repeated_calls(corpus) -> None:
    params, docs = corpus
    query = ParameterQuery(
        names=(ParameterName.INTEREST_RATE_PCT,),
        state="Bihar",
        scheme="test-rural-udyog-yojana",
        as_of=_AS_OF,
    )
    a = resolve_parameters(query, params, docs, cfg=DEFAULT_KNOWLEDGE_CONFIG)
    b = resolve_parameters(query, params, docs, cfg=DEFAULT_KNOWLEDGE_CONFIG)
    assert [r.model_dump(mode="json") for r in a] == [r.model_dump(mode="json") for r in b]


def test_empty_corpus_yields_no_evidence_for_everything() -> None:
    query = ParameterQuery(names=tuple(ParameterName), as_of=_AS_OF)
    results = resolve_parameters(query, [], {})
    assert len(results) == len(ParameterName)
    assert all(r.status is ResolutionStatus.NO_EVIDENCE for r in results)
    assert all(r.chosen is None for r in results)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
