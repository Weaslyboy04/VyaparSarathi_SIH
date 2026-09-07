"""Phase 3 acquisition — the only opportunity layer that touches network / disk.

Turns a **union** Phase 1 :class:`DiscoveryResult` (one Overpass fetch widened to
every candidate category via ``DiscoveryService.discover(also_fetch=...)``) plus
one :func:`acquire_demand_evidence` call into an :class:`OpportunityEvidence`.
Never raises past its boundary — it delegates to layers that already degrade
gracefully and only adds pure arithmetic on top.

**Per-candidate coverage confidence.** ``discovery/confidence.py`` computes a
single data-coverage confidence from the size of the *whole* result. Under a
one-category Phase 1 fetch that was implicitly per-category. Under a union fetch
it is not: a candidate with zero relevant businesses would inherit the confidence
earned by ~8 other categories' POIs, which would stop Phase 2D's
"absence of evidence is not evidence of absence" rung from firing. This function
restores the original semantics by recomputing :func:`coverage_confidence` per
candidate over only the businesses that are **not irrelevant** to that candidate
(``relationship_for`` — the same table Phase 2A uses). The pure engine
(``market/opportunity.py``) then swaps the per-candidate value onto a view of the
discovery result before Phase 2B. No Phase 1 / Phase 2 code changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from vyaparsarathi.config import Settings
from vyaparsarathi.discovery.confidence import coverage_confidence
from vyaparsarathi.discovery.demand_acquisition import acquire_demand_evidence
from vyaparsarathi.market.models import Relationship
from vyaparsarathi.market.relationships import relationship_for
from vyaparsarathi.models.demand import DemandEvidence
from vyaparsarathi.models.opportunity import OpportunityEvidence
from vyaparsarathi.models.results import DiscoveryResult
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)


def _dedupe_order_stable(
    categories: Sequence[BusinessCategory],
) -> list[BusinessCategory]:
    seen: set[BusinessCategory] = set()
    out: list[BusinessCategory] = []
    for c in categories:
        if c not in seen and c is not BusinessCategory.UNKNOWN:
            seen.add(c)
            out.append(c)
    return out


def _osm_coverage_stats(discovery: DiscoveryResult) -> tuple[int, int, bool]:
    """``(raw_records, unmapped_tag_count, mirror_fallback_used)`` from the OSM
    source-coverage row, or conservative zeros when it is absent."""
    for per in discovery.coverage.per_source:
        if per.source is SourceName.OSM:
            return (
                per.raw_records,
                sum(per.unmapped_tags.values()),
                per.mirror_fallback_used,
            )
    return 0, 0, False


def acquire_opportunity_evidence(
    discovery: DiscoveryResult,
    candidate_categories: Sequence[BusinessCategory],
    *,
    client: OverpassClient,
    census: CensusVillageSource | None = None,
    settings: Settings | None = None,
    clock: Clock = utcnow,
    demand: DemandEvidence | None = None,
) -> OpportunityEvidence:
    """Bundle a union discovery result + demand evidence + per-candidate coverage
    confidence for :func:`vyaparsarathi.market.opportunity.score_opportunities`.

    ``discovery`` must already be a union fetch (its ``businesses`` covering every
    category in ``candidate_categories``); this function does **not** issue a
    business query. It issues exactly one demand query (via
    :func:`acquire_demand_evidence`) unless ``demand`` is supplied, in which
    case that already-acquired evidence is reused verbatim and no second
    Overpass union query is made. `acquire_demand_evidence` is
    category-independent (fixed `DEMAND_QUERY_SELECTORS`), so a caller that
    already ran it for the same catchment (e.g. Phase 6's `DEMAND_EVIDENCE`
    step) should always pass it here rather than let this function repeat an
    identical query — the default (`None`) reproduces the original
    single-query-inside-this-function behaviour unchanged, for every existing
    caller.
    """
    candidates = _dedupe_order_stable(candidate_categories)
    demand = (
        demand
        if demand is not None
        else acquire_demand_evidence(
            discovery, client=client, census=census, settings=settings, clock=clock
        )
    )

    raw_records, unmapped, mirror = _osm_coverage_stats(discovery)
    per_category_confidence: dict[BusinessCategory, float] = {}
    for cat in candidates:
        relevant = [
            h
            for h in discovery.businesses
            if relationship_for(cat, h.business.category) is not Relationship.IRRELEVANT
        ]
        per_category_confidence[cat] = coverage_confidence(
            raw_count=raw_records,
            normalized=len(relevant),
            after_dedup=len(relevant),
            unmapped_tag_count=unmapped,
            mirror_fallback_used=mirror,
        )

    warnings = [
        "Data-coverage confidence is computed PER candidate category (over the "
        "businesses not irrelevant to it), so a candidate with no relevant "
        "businesses is not credited with coverage earned by other categories in "
        "the same union fetch.",
    ]
    if discovery.warnings:
        warnings.append(
            f"Union discovery run carried {len(discovery.warnings)} warning(s); see "
            "the discovery result."
        )

    logger.info(
        "opportunity evidence: %d candidate categor(y/ies), %d business(es) in the "
        "union fetch, per-candidate confidence range %.2f-%.2f",
        len(candidates),
        len(discovery.businesses),
        min(per_category_confidence.values(), default=0.0),
        max(per_category_confidence.values(), default=0.0),
    )
    return OpportunityEvidence(
        discovery=discovery,
        demand=demand,
        candidate_categories=candidates,
        per_category_confidence=per_category_confidence,
        warnings=warnings,
    )
