"""Deterministic parameter resolution over the reviewed registry (CLAUDE.md
§18, §22, §30).

**The load-bearing structural rule of Phase 5**: this module never calls a
`~vyaparsarathi.knowledge.base.Retriever`. A parameter's value is selected
purely by the precedence ladder below, over the candidates that already exist
in the loaded registry — never by a lexical/relevance score. This is what
makes "RAG is not the source of truth" a type-level property rather than a
promise: `knowledge/retrieval.py` cannot influence which `SourcedParameter` a
resolution chooses, because `resolve_parameters` never imports it.

## Filters (F1-F8)

Every candidate `SourcedParameter` whose `name` matches the query is checked
against every filter below; a candidate failing any one is excluded and
recorded in `ParameterResolution.rejected_reasons`. An unset field on either
side of a comparison **never** causes a rejection — only an explicit
disagreement does (e.g. a candidate scoped to `scheme="x"` is excluded from a
query naming `scheme="y"`, but is *not* excluded from a query that names no
scheme at all).

1. `name` matches (the query itself groups candidates by name).
2. `tier` is in `PARAMETER_SPEC[name].allowed_tiers` — a `SECONDARY`-tier row
   can never supply a binding rate or a benchmark this table gates higher.
3. Jurisdiction compatible — exact state/district match, or the candidate is
   `NATIONAL` (a permitted, confidence-penalised fallback, never a rejection).
4. Scheme compatible — exact match, or the candidate is not scheme-specific.
5. Category compatible — the query category is in the candidate's
   `categories`, or the candidate is not category-specific.
6. Loan band — `min_loan_inr <= query.loan_amount_inr <= max_loan_inr` where
   both sides are known.
7. Not stale — the candidate's `applicability` window contains `query.as_of`
   (when both are known), and its document is not `superseded_by` another
   document present in the corpus.
8. Conditions resolvable — a candidate carrying `applicability.conditions` is
   held back as `CONDITIONS_UNRESOLVED` when
   `cfg.reject_unresolved_conditions` is set. Conditions are **never**
   interpreted, only carried.

## Precedence (survivors of every filter)

1. Higher `SourceTier` wins — the tier enum's own declared order
   (`GOVT_PRIMARY > REGULATOR > PUBLIC_SECTOR_INSTITUTION > INDUSTRY_BODY >
   SECONDARY`), a fixed structural rule, **not** `KnowledgeConfig.tier_weight`
   (that config value only scales the reported confidence *number*, never
   which candidate wins — see `knowledge/confidence.py`'s module docstring).
2. Narrower `Applicability.specificity()`.
3. Later `applicability.effective_from`.
4. Later source `DocumentRecord.published_on`.

A single top-ranked survivor resolves. Multiple survivors tied at the top
rank resolve only if they **all** state the same `(value, unit)` — the lowest
`parameter_id` is chosen for byte-stable output and every agreeing
`document_id` is recorded. Tied survivors with **different** values are
`CONFLICTING`: `chosen` stays `None`, never averaged, never arbitrarily
picked.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

from vyaparsarathi.knowledge.confidence import parameter_confidence
from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.parameter_spec import PARAMETER_SPEC
from vyaparsarathi.models.knowledge import DocumentRecord, JurisdictionLevel, SourceTier
from vyaparsarathi.models.parameters import (
    ParameterName,
    ParameterQuery,
    ParameterResolution,
    ResolutionStatus,
    SourcedParameter,
)

# Fixed structural authority ordering — see the module docstring's precedence
# rule 1. Declaration order in `SourceTier` IS this ordering; kept explicit
# here so a future reordering of the enum is a visible, reviewed change to
# this list, not a silent change to precedence.
_TIER_AUTHORITY_ORDER: tuple[SourceTier, ...] = (
    SourceTier.GOVT_PRIMARY,
    SourceTier.REGULATOR,
    SourceTier.PUBLIC_SECTOR_INSTITUTION,
    SourceTier.INDUSTRY_BODY,
    SourceTier.SECONDARY,
)
_TIER_RANK: dict[SourceTier, int] = {t: i for i, t in enumerate(_TIER_AUTHORITY_ORDER)}


def _norm(value: str | None) -> str:
    return (value or "").strip().casefold()


def _jurisdiction_ok(param: SourcedParameter, query: ParameterQuery) -> bool:
    jur = param.applicability.jurisdiction
    if jur.level is JurisdictionLevel.NATIONAL:
        return True
    if query.state is None:
        return True  # the query does not scope by state; never reject on it
    if _norm(jur.state) != _norm(query.state):
        return False
    if jur.level is JurisdictionLevel.STATE:
        return True
    # DISTRICT
    if query.district is None:
        return True
    return _norm(jur.district) == _norm(query.district)


def _scheme_ok(param: SourcedParameter, query: ParameterQuery) -> bool:
    scheme = param.applicability.scheme
    if scheme is None or query.scheme is None:
        return True
    return scheme == query.scheme


def _category_ok(param: SourcedParameter, query: ParameterQuery) -> bool:
    categories = param.applicability.categories
    if not categories or query.category is None:
        return True
    return query.category in categories


def _loan_band_ok(param: SourcedParameter, query: ParameterQuery) -> bool:
    amount = query.loan_amount_inr
    if amount is None:
        return True
    a = param.applicability
    if a.min_loan_inr is not None and amount < a.min_loan_inr:
        return False
    if a.max_loan_inr is not None and amount > a.max_loan_inr:
        return False
    return True


def _is_stale_or_superseded(
    param: SourcedParameter, query: ParameterQuery, documents: Mapping[str, DocumentRecord]
) -> bool:
    as_of = query.as_of
    a = param.applicability
    if as_of is not None:
        if a.effective_to is not None and as_of > a.effective_to:
            return True
        if a.effective_from is not None and as_of < a.effective_from:
            return True
    doc = documents.get(param.document_id)
    if doc is not None and doc.superseded_by is not None and doc.superseded_by in documents:
        return True
    return False


def _specificity_ok_after_conditions(param: SourcedParameter, cfg: KnowledgeConfig) -> bool:
    return not (cfg.reject_unresolved_conditions and param.applicability.conditions)


def _published_on(param: SourcedParameter, documents: Mapping[str, DocumentRecord]) -> date:
    doc = documents.get(param.document_id)
    if doc is not None and doc.published_on is not None:
        return doc.published_on
    return date.min


def _rank_key(
    param: SourcedParameter, documents: Mapping[str, DocumentRecord]
) -> tuple[int, tuple[int, int, int, int], date, date]:
    """Larger is more authoritative — usable directly with `max()`. See the
    module docstring's precedence rules 1-4."""
    tier_score = -_TIER_RANK.get(param.tier, len(_TIER_AUTHORITY_ORDER))
    effective_from = param.applicability.effective_from or date.min
    return (
        tier_score,
        param.applicability.specificity(),
        effective_from,
        _published_on(param, documents),
    )


def _citation(param: SourcedParameter, documents: Mapping[str, DocumentRecord]) -> str:
    doc = documents.get(param.document_id)
    where = param.locator.as_ref()
    if doc is None:
        return f"{param.document_id} ({where})"
    return f"{doc.title} — {doc.publisher} ({where})"


def _applicability_match(
    param: SourcedParameter, query: ParameterQuery, cfg: KnowledgeConfig
) -> tuple[float, list[str]]:
    match = 1.0
    notes: list[str] = []
    if param.applicability.jurisdiction.level is JurisdictionLevel.NATIONAL and (
        query.state is not None
    ):
        match *= cfg.national_fallback_penalty
        notes.append(
            "resolved from a NATIONAL rule; no more specific rule was found for the "
            "queried jurisdiction"
        )
    if query.category is not None and not param.applicability.categories:
        match *= cfg.category_generic_penalty
        notes.append("resolved from a category-generic rule; no category-specific rule was found")
    return match, notes


def _resolve_one(
    name: ParameterName,
    query: ParameterQuery,
    parameters: Sequence[SourcedParameter],
    documents: Mapping[str, DocumentRecord],
    *,
    cfg: KnowledgeConfig,
) -> ParameterResolution:
    candidates = [p for p in parameters if p.name is name]
    if not candidates:
        return ParameterResolution(
            name=name,
            status=ResolutionStatus.NO_EVIDENCE,
            notes=["no SourcedParameter in the registry states this parameter"],
        )

    spec = PARAMETER_SPEC.get(name)
    rejected: dict[str, str] = {}
    survivors: list[SourcedParameter] = []
    any_stale = False
    any_conditions_unresolved = False

    for p in candidates:
        if spec is not None and p.tier not in spec.allowed_tiers:
            rejected[p.parameter_id] = f"tier {p.tier.value} is below the floor for {name.value}"
            continue
        if not _jurisdiction_ok(p, query):
            rejected[p.parameter_id] = (
                f"jurisdiction {p.applicability.jurisdiction.level.value} "
                f"({p.applicability.jurisdiction.state or 'n/a'}) does not match the query"
            )
            continue
        if not _scheme_ok(p, query):
            rejected[p.parameter_id] = f"scheme {p.applicability.scheme!r} does not match the query"
            continue
        if not _category_ok(p, query):
            rejected[p.parameter_id] = "category does not match the query"
            continue
        if not _loan_band_ok(p, query):
            rejected[p.parameter_id] = "loan amount falls outside this row's loan band"
            continue
        if _is_stale_or_superseded(p, query, documents):
            rejected[p.parameter_id] = "not effective at the queried date, or superseded"
            any_stale = True
            continue
        if not _specificity_ok_after_conditions(p, cfg):
            rejected[p.parameter_id] = (
                f"unresolved conditions: {'; '.join(p.applicability.conditions)}"
            )
            any_conditions_unresolved = True
            continue
        survivors.append(p)

    if not survivors:
        if any_stale:
            status = ResolutionStatus.STALE_ONLY
        elif any_conditions_unresolved:
            status = ResolutionStatus.CONDITIONS_UNRESOLVED
        else:
            status = ResolutionStatus.NO_EVIDENCE
        return ParameterResolution(
            name=name,
            status=status,
            candidates=candidates,
            rejected_reasons=rejected,
        )

    best_key = max(_rank_key(p, documents) for p in survivors)
    top = [p for p in survivors if _rank_key(p, documents) == best_key]

    if len(top) > 1:
        distinct_values = {(p.value, p.unit) for p in top}
        if len(distinct_values) > 1:
            return ParameterResolution(
                name=name,
                status=ResolutionStatus.CONFLICTING,
                candidates=candidates,
                rejected_reasons=rejected,
                notes=[
                    f"{len(top)} sources at the same tier and specificity state "
                    f"different values for {name.value}; this parameter is not used"
                ],
            )
        chosen = min(top, key=lambda p: p.parameter_id)
    else:
        chosen = top[0]

    agreeing = [p for p in survivors if (p.value, p.unit) == (chosen.value, chosen.unit)]
    agreeing_document_ids = tuple(sorted({p.document_id for p in agreeing}))

    for p in survivors:
        if p.parameter_id != chosen.parameter_id and p.document_id not in agreeing_document_ids:
            rejected[p.parameter_id] = f"lower precedence than {chosen.parameter_id}"

    applicability_match, match_notes = _applicability_match(chosen, query, cfg)
    confidence, basis = parameter_confidence(
        chosen,
        documents=documents,
        agreeing_document_ids=agreeing_document_ids,
        applicability_match=applicability_match,
        cfg=cfg,
    )
    chosen_doc = documents.get(chosen.document_id)

    return ParameterResolution(
        name=name,
        status=ResolutionStatus.RESOLVED,
        chosen=chosen,
        candidates=candidates,
        rejected_reasons=rejected,
        confidence=confidence,
        confidence_basis=basis,
        agreeing_document_ids=agreeing_document_ids,
        source=f"knowledge:{chosen.document_id}",
        source_ref=f"{chosen.document_id}#{chosen.locator.as_ref()}",
        retrieved_at=chosen_doc.retrieved_at if chosen_doc is not None else None,
        citation=_citation(chosen, documents),
        notes=match_notes,
    )


def resolve_parameters(
    query: ParameterQuery,
    parameters: Sequence[SourcedParameter],
    documents: Mapping[str, DocumentRecord],
    *,
    cfg: KnowledgeConfig = DEFAULT_KNOWLEDGE_CONFIG,
) -> list[ParameterResolution]:
    """Resolve every `ParameterName` in `query.names` against the registry.
    Pure: no I/O, no clock, no RNG — `query.as_of` is the caller's stated
    date, never `date.today()`."""
    return [_resolve_one(name, query, parameters, documents, cfg=cfg) for name in query.names]


__all__ = ["resolve_parameters"]
