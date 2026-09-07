"""Parameter confidence — evidence quality, never viability (CLAUDE.md §22).

Mirrors `market/demand.py`'s freshness/source-tier confidence shape: a
resolved parameter's confidence is `tier_weight x freshness x
applicability_match x agreement_multiplier`, capped at
`KnowledgeConfig.single_document_ceiling` unless at least two independent
documents agree on the same `(value, unit)` — the direct analogue of
`discovery/confidence.py`'s `SINGLE_SOURCE_CEILING`.

**Confidence never selects.** This module is called by
`knowledge/resolver.py` only *after* `chosen` has already been picked by the
fixed precedence ladder (source tier, then applicability specificity, then
recency — never by a `KnowledgeConfig` weight). Re-running the resolver with
every `tier_weight` flattened to `1.0` and freshness decay set to `0.0`
therefore always yields the identical `chosen` for every resolution; only the
reported number changes. See `tests/test_knowledge_confidence.py`.
"""

from __future__ import annotations

from collections.abc import Mapping

from vyaparsarathi.knowledge.knowledge_config import KnowledgeConfig
from vyaparsarathi.models.knowledge import DocumentRecord, SourceTier
from vyaparsarathi.models.parameters import SourcedParameter


def _effective_year(
    chosen: SourcedParameter, documents: Mapping[str, DocumentRecord]
) -> int | None:
    """The year the RESOLVED FACT describes, not when it was retrieved
    (CLAUDE.md §23) — in order of preference: the row's own `reference_date`,
    then its applicability's `effective_from`, then its source document's
    `published_on`. `None` when none of the three is known."""
    if chosen.reference_date is not None:
        return chosen.reference_date.year
    if chosen.applicability.effective_from is not None:
        return chosen.applicability.effective_from.year
    doc = documents.get(chosen.document_id)
    if doc is not None and doc.published_on is not None:
        return doc.published_on.year
    return None


def _freshness(data_year: int, cfg: KnowledgeConfig) -> float:
    raw = 1.0 - cfg.freshness_decay_per_year * max(0, cfg.reference_year - data_year)
    return max(cfg.freshness_floor, raw)


def parameter_confidence(
    chosen: SourcedParameter,
    *,
    documents: Mapping[str, DocumentRecord],
    agreeing_document_ids: tuple[str, ...],
    applicability_match: float,
    cfg: KnowledgeConfig,
) -> tuple[float, dict[str, float | int | str | None]]:
    """The confidence for one already-`chosen` `SourcedParameter`, plus the
    basis dict a `ParameterResolution.confidence_basis` echoes verbatim
    (matching `demand_data_confidence_basis`'s transparency pattern)."""
    effective_year = _effective_year(chosen, documents)
    if effective_year is None:
        # No dated evidence at all for how current this fact is — the
        # conservative choice is the floor, never an assumed "current".
        freshness = cfg.freshness_floor
    else:
        freshness = _freshness(effective_year, cfg)

    tier_weight = cfg.tier_weight.get(chosen.tier, cfg.tier_weight[SourceTier.SECONDARY])

    agreeing_count = len(agreeing_document_ids)
    agreement_multiplier = 1.0
    if agreeing_count >= 2:
        agreement_multiplier = min(1.0 + cfg.agreement_bonus, cfg.agreement_bonus_cap)

    raw = tier_weight * freshness * applicability_match * agreement_multiplier
    single_document_ceiling_applied = agreeing_count < 2
    if single_document_ceiling_applied:
        raw = min(raw, cfg.single_document_ceiling)

    confidence = round(max(0.0, min(1.0, raw)), 2)
    basis: dict[str, float | int | str | None] = {
        "tier": chosen.tier.value,
        "tier_weight": tier_weight,
        "effective_year": effective_year,
        "freshness": round(freshness, 3),
        "applicability_match": round(applicability_match, 3),
        "agreeing_documents": agreeing_count,
        "agreement_multiplier": agreement_multiplier,
        "single_document_ceiling_applied": single_document_ceiling_applied,
    }
    return confidence, basis


__all__ = ["parameter_confidence"]
