"""Deterministic competitor classifier (CLAUDE.md §11, STEP 5).

Given a Phase 1 :class:`DiscoveryResult` and a :class:`ProposedBusiness`, sort
every discovered business into ``direct`` / ``adjacent`` / ``irrelevant`` with a
deterministic, human-readable reason. No LLM, no network, no market metrics.
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.market.models import (
    ClassifiedCompetitor,
    CompetitorAnalysisResult,
    CompetitorAnalysisStatus,
    ProposedBusiness,
    Relationship,
)
from vyaparsarathi.market.proposed import proposed_from_category
from vyaparsarathi.market.relationships import (
    SUBTYPE_CATEGORY_OVERLAPS,
    relationship_for,
    strongest,
)
from vyaparsarathi.models.business import NormalizedBusiness
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.normalization.text import normalize_name
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


def _name_tokens(business: NormalizedBusiness) -> set[str]:
    tokens = set(normalize_name(business.name).split())
    tokens |= set((business.normalized_name or "").split())
    return tokens


def _base_reason(proposed: BusinessCategory, existing: BusinessCategory, rel: Relationship) -> str:
    if proposed == existing:
        return f"Same primary category as the proposed business ({existing.value})."
    if rel is Relationship.DIRECT:
        return (
            f"'{existing.value}' is configured as a direct substitute for "
            f"'{proposed.value}' retail."
        )
    if rel is Relationship.ADJACENT:
        return (
            f"'{existing.value}' partially serves the same customers as a "
            f"'{proposed.value}' business."
        )
    return f"'{existing.value}' serves a different primary customer need than '{proposed.value}'."


def _classify_one(
    proposed: ProposedBusiness, business: NormalizedBusiness
) -> tuple[Relationship, str, list[str]]:
    base = relationship_for(proposed.category, business.category)
    rel = base
    reasons = [_base_reason(proposed.category, business.category, base)]
    matched: list[str] = []
    name_tokens = _name_tokens(business)

    for subtype in proposed.subtypes:
        # (a) category-level overlap declared for this subtype
        implied = SUBTYPE_CATEGORY_OVERLAPS.get(subtype, {}).get(business.category)
        if implied is not None and strongest(implied, rel) is implied and implied != rel:
            rel = implied
            matched.append(subtype)
            reasons.append(
                f"Proposed subtype '{subtype}' overlaps with "
                f"'{business.category.value}' businesses."
            )
        # (b) the subtype word appears in the existing business's name
        if subtype in name_tokens:
            upgraded = strongest(rel, Relationship.ADJACENT)
            if base is Relationship.DIRECT or business.category == proposed.category:
                upgraded = Relationship.DIRECT
            if subtype not in matched:
                matched.append(subtype)
            if upgraded != rel:
                rel = upgraded
            reasons.append(f"The existing business name references '{subtype}'.")

    return rel, " ".join(reasons), matched


def analyze_competitors(
    discovery: DiscoveryResult, proposed: ProposedBusiness
) -> CompetitorAnalysisResult:
    """Classify every business in ``discovery`` against ``proposed``."""
    counts_base = {
        "discovered": len(discovery.businesses),
        "direct": 0,
        "adjacent": 0,
        "irrelevant": 0,
    }

    if not proposed.resolved or proposed.category is BusinessCategory.UNKNOWN:
        note = proposed.note or "Proposed business could not be mapped to a known category."
        logger.info("competitor analysis skipped: %s", note)
        return CompetitorAnalysisResult(
            status=CompetitorAnalysisStatus.UNKNOWN_CATEGORY,
            proposed=proposed,
            location_text=discovery.query_text,
            discovery_status=discovery.status,
            counts=counts_base,
            warnings=[f"{note} Ask the entrepreneur to name the kind of shop more specifically."],
        )

    warnings: list[str] = []
    if discovery.status is not DiscoveryStatus.OK:
        warnings.append(
            f"Phase 1 discovery status was '{discovery.status.value}'; classification covers "
            f"{len(discovery.businesses)} business(es)."
        )
    if not discovery.businesses:
        warnings.append("No businesses were discovered nearby, so there are no competitors.")

    direct: list[ClassifiedCompetitor] = []
    adjacent: list[ClassifiedCompetitor] = []
    irrelevant: list[ClassifiedCompetitor] = []
    bucket = {
        Relationship.DIRECT: direct,
        Relationship.ADJACENT: adjacent,
        Relationship.IRRELEVANT: irrelevant,
    }

    for hit in discovery.businesses:
        rel, reason, matched = _classify_one(proposed, hit.business)
        bucket[rel].append(
            ClassifiedCompetitor(
                business=hit.business,
                distance_m=hit.distance_m,
                relationship=rel,
                reason=reason,
                matched_subtypes=matched,
            )
        )

    for items in (direct, adjacent, irrelevant):
        items.sort(key=lambda c: (c.distance_m is None, c.distance_m or 0.0))

    logger.info(
        "competitor analysis (%s%s): %d direct, %d adjacent, %d irrelevant of %d",
        proposed.category.value,
        f"+{proposed.subtypes}" if proposed.subtypes else "",
        len(direct),
        len(adjacent),
        len(irrelevant),
        len(discovery.businesses),
    )
    return CompetitorAnalysisResult(
        status=CompetitorAnalysisStatus.OK,
        proposed=proposed,
        location_text=discovery.query_text,
        discovery_status=discovery.status,
        direct_competitors=direct,
        adjacent_competitors=adjacent,
        irrelevant=irrelevant,
        counts={
            "discovered": len(discovery.businesses),
            "direct": len(direct),
            "adjacent": len(adjacent),
            "irrelevant": len(irrelevant),
        },
        warnings=warnings,
    )


def analyze_from_discovery(
    discovery: DiscoveryResult, subtypes: Iterable[str] = ()
) -> CompetitorAnalysisResult:
    """Convenience: use the category Phase 1 already resolved as the proposal."""
    category = discovery.query.category if discovery.query is not None else discovery.category
    proposed = proposed_from_category(category, subtypes=subtypes, raw_text=discovery.query_text)
    return analyze_competitors(discovery, proposed)
