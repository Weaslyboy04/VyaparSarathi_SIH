"""`knowledge/confidence.py::parameter_confidence` (CLAUDE.md §22). Pure &
offline."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.knowledge.confidence import parameter_confidence
from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.resolver import resolve_parameters
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
    ParameterName,
    ParameterQuery,
    SourcedParameter,
    ValueNormalization,
)


def _doc(**kw: object) -> DocumentRecord:
    base: dict[str, object] = {
        "document_id": "doc-1",
        "title": "Synthetic test document",
        "publisher": "Fictional test publisher",
        "tier": SourceTier.GOVT_PRIMARY,
        "jurisdiction": Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        "published_on": date(2025, 1, 1),
        "retrieved_at": datetime(2026, 1, 15, tzinfo=UTC),
        "content_sha256": "a" * 64,
    }
    base.update(kw)
    return DocumentRecord(**base)  # type: ignore[arg-type]


def _param(**kw: object) -> SourcedParameter:
    base: dict[str, object] = {
        "parameter_id": "doc-1:interest_rate_pct:1",
        "name": ParameterName.INTEREST_RATE_PCT,
        "value": Decimal("10.5"),
        "unit": Unit.PERCENT_PER_ANNUM,
        "value_token": "10.5%",
        "normalization": ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        "evidence_quote": "The rate shall be 10.5% per annum.",
        "document_id": "doc-1",
        "chunk_id": "doc-1#s1",
        "locator": ChunkLocator(section="1"),
        "tier": SourceTier.GOVT_PRIMARY,
        "applicability": Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar")
        ),
        "reviewed_by": "test",
        "reviewed_on": date(2026, 1, 15),
    }
    base.update(kw)
    return SourcedParameter(**base)  # type: ignore[arg-type]


def test_confidence_is_within_zero_one() -> None:
    conf, _ = parameter_confidence(
        _param(),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert 0.0 <= conf <= 1.0


def test_single_document_ceiling_holds_even_at_perfect_freshness_and_tier() -> None:
    conf, basis = parameter_confidence(
        _param(),
        documents={"doc-1": _doc(published_on=date(2026, 1, 1))},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert conf <= DEFAULT_KNOWLEDGE_CONFIG.single_document_ceiling
    assert basis["single_document_ceiling_applied"] is True


def test_two_agreeing_documents_can_exceed_the_single_document_ceiling() -> None:
    conf, basis = parameter_confidence(
        _param(),
        documents={"doc-1": _doc(published_on=date(2026, 1, 1))},
        agreeing_document_ids=("doc-1", "doc-2"),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert basis["single_document_ceiling_applied"] is False
    assert conf >= DEFAULT_KNOWLEDGE_CONFIG.single_document_ceiling


def test_freshness_decays_with_reference_year_and_floors() -> None:
    fresh, _ = parameter_confidence(
        _param(reference_date=date(2026, 1, 1)),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    stale, _ = parameter_confidence(
        _param(reference_date=date(2000, 1, 1)),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert stale < fresh
    very_old = KnowledgeConfig(
        reference_year=2026, freshness_floor=0.4, freshness_decay_per_year=0.5
    )
    conf, basis = parameter_confidence(
        _param(reference_date=date(1990, 1, 1)),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=very_old,
    )
    assert basis["freshness"] == 0.4  # floored, never negative


def test_national_fallback_has_lower_confidence_than_an_exact_match() -> None:
    # Two agreeing documents so the single-document ceiling does not mask the
    # applicability_match difference this test is isolating.
    national_match, _ = parameter_confidence(
        _param(),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1", "doc-2"),
        applicability_match=DEFAULT_KNOWLEDGE_CONFIG.national_fallback_penalty,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    exact_match, _ = parameter_confidence(
        _param(),
        documents={"doc-1": _doc()},
        agreeing_document_ids=("doc-1", "doc-2"),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert national_match < exact_match


def test_missing_reference_date_falls_back_to_the_freshness_floor() -> None:
    # No reference_date, no applicability.effective_from, and no document ->
    # nothing dated anywhere; the conservative floor applies, never an
    # assumed "current".
    conf, basis = parameter_confidence(
        _param(),
        documents={},
        agreeing_document_ids=("doc-1",),
        applicability_match=1.0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
    )
    assert basis["effective_year"] is None
    assert basis["freshness"] == DEFAULT_KNOWLEDGE_CONFIG.freshness_floor


def test_confidence_never_changes_which_candidate_is_chosen() -> None:
    """The load-bearing invariant: confidence is reporting, not selection."""
    docs = {
        "doc-national": _doc(
            document_id="doc-national",
            jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL),
            published_on=date(2024, 6, 1),
        ),
        "doc-state": _doc(document_id="doc-state", published_on=date(2025, 1, 1)),
    }
    params = [
        _param(
            parameter_id="doc-national:interest_rate_pct:1",
            document_id="doc-national",
            chunk_id="doc-national#s1",
            value=Decimal("12.0"),
            value_token="12.0%",
            evidence_quote="The rate shall be 12.0% per annum.",
            applicability=Applicability(
                jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL)
            ),
        ),
        _param(
            parameter_id="doc-state:interest_rate_pct:1",
            document_id="doc-state",
            chunk_id="doc-state#s1",
        ),
    ]
    query = ParameterQuery(
        names=(ParameterName.INTEREST_RATE_PCT,), state="Bihar", as_of=date(2026, 1, 1)
    )

    flattened = KnowledgeConfig(
        tier_weight=dict.fromkeys(SourceTier, 1.0),
        freshness_decay_per_year=0.0,
        freshness_floor=1.0,
        national_fallback_penalty=1.0,
        category_generic_penalty=1.0,
        single_document_ceiling=1.0,  # so the flattened weights actually show through
    )

    default_result = resolve_parameters(query, params, docs, cfg=DEFAULT_KNOWLEDGE_CONFIG)[0]
    flattened_result = resolve_parameters(query, params, docs, cfg=flattened)[0]

    assert default_result.chosen is not None
    assert flattened_result.chosen is not None
    assert default_result.chosen.parameter_id == flattened_result.chosen.parameter_id
    # only the reported number is allowed to move
    assert default_result.confidence != flattened_result.confidence


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
