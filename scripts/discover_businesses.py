"""Phase 1 + Phase 2A manual-test CLI (CLAUDE.md §26 STEP 14, §11).

    python scripts/discover_businesses.py --location "Bhagwanpur, Bihar" \\
        --category grocery --radius 8

Resolves the location (reporting ambiguity instead of guessing), queries
OpenStreetMap / Overpass, normalizes, deduplicates, computes distances,
persists, and prints a summary. ``--json`` emits the full DiscoveryResult.

Add ``--competitors`` to also classify the discovered businesses against the
proposed business (Phase 2A) into direct / adjacent / irrelevant, with a reason
for each. ``--subtype pulses`` (repeatable) and ``--proposed "pulses grocery
store"`` refine the proposal.

Add ``--metrics`` to also compute Phase 2B competition metrics (counts,
nearest / average / median distances, distance-band counts, a raw spatial
density, and a transparent competition signal) from that classification.

Add ``--demand`` to also gather Phase 2C local-demand signals (settlements +
activity anchors from OSM, plus catchment population from the Census 2011
extract). With ``--metrics --demand`` the competitors-per-1,000-residents ratio
is computed too.

Add ``--assess`` (implies ``--metrics --demand``) to also run Phase 2D: fuse the
2B competition and 2C demand into an overall market label (underserved / served /
crowded / thin market / mixed / insufficient evidence) with positive signals,
concerns and data caveats.

Add ``--opportunity`` (implies a widened / union Overpass fetch) to run Phase 3:
score and rank a curated shortlist of candidate businesses (plus the proposed
one) for this location and the entrepreneur's resources, and say whether the
proposed business is the best of them or an alternative is materially better.
``--cash INR``, ``--asset KIND`` (repeatable) and ``--experience CATEGORY``
(repeatable) supply the entrepreneur profile.
"""

from __future__ import annotations

import argparse
import sys

from vyaparsarathi.config import get_settings
from vyaparsarathi.database import InMemoryBusinessRepository, create_repository
from vyaparsarathi.discovery import (
    DiscoveryService,
    acquire_demand_evidence,
    acquire_opportunity_evidence,
)
from vyaparsarathi.geocoding import NominatimGeocoder
from vyaparsarathi.market import (
    analyze_competitors,
    assess_market,
    compute_competition_metrics,
    compute_demand_signals,
    proposed_from_category,
    resolve_proposed_business,
    score_opportunities,
)
from vyaparsarathi.market.assessment_models import MarketAssessmentResult, MarketAssessmentStatus
from vyaparsarathi.market.demand_models import DemandSignalsResult, DemandStatus
from vyaparsarathi.market.metrics_models import (
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
)
from vyaparsarathi.market.models import CompetitorAnalysisResult, CompetitorAnalysisStatus
from vyaparsarathi.market.opportunity_config import DEFAULT_OPPORTUNITY_CONFIG
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult, OpportunityStatus
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.sources.osm import OverpassSource
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.utils.logging import configure_logging


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="discover_businesses",
        description="VyaparSarathi Phase 1 — hyper-local business discovery (OSM).",
    )
    parser.add_argument("--location", required=True, help='e.g. "Bhagwanpur, Bihar"')
    parser.add_argument(
        "--category",
        required=True,
        help="internal category, e.g. grocery (see --list-categories)",
    )
    parser.add_argument(
        "--radius", type=float, default=8.0, help="radius in kilometres (default 8)"
    )
    parser.add_argument(
        "--candidate",
        type=int,
        default=None,
        metavar="N",
        help="on an ambiguous location, use the Nth listed geocoding candidate (1-based) "
        "instead of stopping; its coordinates are used directly (no second geocode)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit JSON: the DiscoveryResult, or the CompetitorAnalysisResult with --competitors",
    )
    parser.add_argument(
        "--db",
        action="store_true",
        help="persist to the configured SQL database (VYAPAR_DB_URL) instead of memory",
    )
    parser.add_argument(
        "--competitors",
        action="store_true",
        help="run Phase 2A: classify discovered businesses vs the proposed business",
    )
    parser.add_argument(
        "--metrics",
        action="store_true",
        help="run Phase 2B: competition metrics (counts, distances, bands, density, signal); "
        "implies --competitors",
    )
    parser.add_argument(
        "--demand",
        action="store_true",
        help="run Phase 2C: local-demand signals (settlements, activity anchors, catchment "
        "population); with --metrics also the competitors-per-1,000-residents ratio",
    )
    parser.add_argument(
        "--assess",
        action="store_true",
        help="run Phase 2D: overall market assessment — combine 2B competition and 2C demand "
        "into a market label (implies --metrics --demand)",
    )
    parser.add_argument(
        "--opportunity",
        action="store_true",
        help="run Phase 3: score & rank a curated shortlist of candidate businesses (plus the "
        "proposed one) for this location and profile; widens the Overpass fetch to one union "
        "query covering every candidate",
    )
    parser.add_argument(
        "--cash",
        type=int,
        default=None,
        metavar="INR",
        help="entrepreneur's stated liquid cash in rupees (Phase 3 profile)",
    )
    parser.add_argument(
        "--asset",
        action="append",
        metavar="KIND",
        help="an asset already owned, e.g. --asset storefront (repeatable; Phase 3 profile). "
        f"Valid: {', '.join(sorted(a.value for a in AssetKind))}",
    )
    parser.add_argument(
        "--experience",
        action="append",
        metavar="CATEGORY",
        help="an internal category the entrepreneur has trade experience in, e.g. "
        "--experience dairy (repeatable; Phase 3 profile)",
    )
    parser.add_argument(
        "--subtype",
        action="append",
        metavar="KEYWORD",
        help="proposed-business subtype, e.g. --subtype pulses (repeatable)",
    )
    parser.add_argument(
        "--proposed",
        default=None,
        metavar="TEXT",
        help='free-text proposed business, e.g. "pulses grocery store" (overrides --category '
        "for the Phase 2A proposal only)",
    )
    parser.add_argument("--max-display", type=int, default=50, help="max businesses to list")
    parser.add_argument("--log-level", default=None, help="override VYAPAR_LOG_LEVEL")
    parser.add_argument(
        "--list-categories", action="store_true", help="print valid categories and exit"
    )
    return parser.parse_args(argv)


def _render_human(result: DiscoveryResult, max_display: int) -> str:
    lines: list[str] = []
    q = result

    lines.append(f"Location:   {q.query_text}")
    lines.append(f"Status:     {q.status.value}")

    if q.resolved_place is not None:
        rp = q.resolved_place
        admin = ", ".join(
            f"{label}={value}"
            for label, value in (
                ("village", rp.village),
                ("block", rp.block),
                ("district", rp.district),
                ("state", rp.state),
                ("country", rp.country),
            )
            if value
        )
        lines.append(f"Resolved:   {rp.display_name}")
        lines.append(f"            {rp.latitude:.6f}, {rp.longitude:.6f}")
        if admin:
            lines.append(f"            {admin}")

    lines.append(f"Category:   {q.category.value}")
    lines.append(f"Radius:     {q.requested_radius_m / 1000:.1f} km")
    lines.append("")

    if q.status is DiscoveryStatus.LOCATION_AMBIGUOUS:
        lines.append(
            "Location is ambiguous — re-run with a more specific --location, "
            "or add --candidate N to pick one of the numbered candidates below."
        )
        lines.append("Candidates:")
        for i, c in enumerate(q.candidates, 1):
            admin = ", ".join(v for v in (c.village, c.district, c.state) if v)
            lines.append(
                f"  [{i}] {c.display_name}\n"
                f"      {c.latitude:.6f}, {c.longitude:.6f}" + (f"  ({admin})" if admin else "")
            )
        return "\n".join(lines)

    cov = q.coverage
    per = cov.per_source[0] if cov.per_source else None
    lines.append(f"Businesses found (normalized): {cov.total_before_dedup}")
    lines.append(f"After deduplication:          {cov.total_after_dedup}")
    lines.append(f"Within radius:                {len(q.businesses)}")
    lines.append(f"Duplicates merged:            {cov.duplicates_merged}")
    lines.append(f"Uncertain pairs (review):     {cov.uncertain_pairs}")
    if per is not None:
        fallback = "yes" if per.mirror_fallback_used else "no"
        lines.append(
            f"Data source:                 OpenStreetMap "
            f"(endpoint={per.endpoint_used}; mirror fallback: {fallback})"
        )
        lines.append(
            f"Raw / dropped(no coord) / dropped(other): "
            f"{per.raw_records} / {per.dropped_no_coordinates} / {per.dropped_other}"
        )
        if per.unmapped_tags:
            lines.append(f"Unmapped tags:                {per.unmapped_tags}")
    lines.append(
        f"Market-data confidence:      {q.confidence:.2f}  "
        f"(data coverage only — NOT business viability)"
    )

    if q.warnings:
        lines.append("")
        lines.append("Warnings:")
        for w in q.warnings:
            lines.append(f"  - {w}")

    if q.businesses:
        lines.append("")
        lines.append(
            f"  {'#':>3}  {'Name':<32} {'Category':<18} {'Dist':>8}  {'Coordinates':<24} Source"
        )
        for i, hit in enumerate(q.businesses[:max_display], 1):
            b = hit.business
            name = (b.name or "(unnamed)")[:32]
            coords = f"{b.latitude:.5f}, {b.longitude:.5f}"
            lines.append(
                f"  {i:>3}  {name:<32} {b.category.value:<18} "
                f"{hit.distance_m:>6.0f} m  {coords:<24} {b.source.value} {b.source_id}"
            )
        if len(q.businesses) > max_display:
            lines.append(f"  ... {len(q.businesses) - max_display} more not shown")

    return "\n".join(lines)


def _render_analysis(analysis: CompetitorAnalysisResult, max_display: int) -> str:
    p = analysis.proposed
    label = p.category.value + (f" + {', '.join(p.subtypes)}" if p.subtypes else "")
    lines = ["", "Business Analysis", "", f"Proposed: {label}"]

    if analysis.status is CompetitorAnalysisStatus.UNKNOWN_CATEGORY:
        lines.append("")
        lines.append(
            f"Could not classify — {analysis.warnings[0] if analysis.warnings else p.note}"
        )
        return "\n".join(lines)

    def block(title: str, items: list, show_reason: bool) -> None:
        lines.append("")
        lines.append(f"{title}: {len(items)}")
        for i, c in enumerate(items[:max_display], 1):
            b = c.business
            dist = f"{c.distance_m:.0f} m" if c.distance_m is not None else "n/a"
            lines.append(f"  {i}. {b.name or '(unnamed)'}")
            lines.append(f"     Category: {b.category.value}   Distance: {dist}")
            if show_reason:
                lines.append(f"     Why: {c.reason}")
        if len(items) > max_display:
            lines.append(f"  ... {len(items) - max_display} more not shown")

    block("Direct competitors", analysis.direct_competitors, True)
    block("Adjacent competitors", analysis.adjacent_competitors, True)
    block("Irrelevant", analysis.irrelevant, True)

    if analysis.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in analysis.warnings)
    return "\n".join(lines)


def _km(metres: float | None) -> str:
    if metres is None:
        return "n/a"
    if metres >= 1000.0:
        return f"{metres / 1000.0:.2f} km"
    return f"{metres:.0f} m"


def _render_metrics(metrics: CompetitionMetricsResult) -> str:
    lines = ["", "Market Competition Analysis (Phase 2B)", ""]
    label = metrics.proposed_category.value + (
        f" + {', '.join(metrics.proposed_subtypes)}" if metrics.proposed_subtypes else ""
    )
    lines.append(f"Location:            {metrics.location_text or 'n/a'}")
    lines.append(f"Proposed business:  {label}")
    lines.append(f"Analysis radius:    {metrics.analysis_radius_m / 1000:.1f} km")
    lines.append(f"Status:             {metrics.status.value}")

    if metrics.status is not CompetitionMetricsStatus.OK:
        lines.append("")
        lines.extend(f"  - {w}" for w in metrics.warnings)
        return "\n".join(lines)

    d = metrics.direct_distance
    lines += [
        "",
        f"Direct competitors:       {metrics.direct_count}",
        f"Adjacent competitors:     {metrics.adjacent_count}",
        f"Total relevant:           {metrics.total_relevant_count}",
        "",
        f"Nearest direct competitor:  {_km(d.nearest_m)}",
        f"Farthest direct competitor: {_km(d.farthest_m)}",
        f"Average direct distance:    {_km(d.mean_m)}",
        f"Median direct distance:     {_km(d.median_m)}",
    ]
    if d.missing_distance:
        lines.append(f"(direct competitors with no distance: {d.missing_distance})")

    lines.append("")
    for band in metrics.distance_bands:
        note = "  (beyond analysis radius)" if band.exceeds_radius else ""
        lines.append(
            f"Direct within {_km(band.max_distance_m):>7}: {band.direct_count}"
            f"   (incl. adjacent: {band.relevant_count}){note}"
        )

    den = metrics.density
    lines += [
        "",
        f"Search area:              {den.catchment_area_km2:.2f} km²"
        if den.catchment_area_km2 is not None
        else "Search area:              n/a",
        f"Direct competitor density: {den.direct_per_km2:.3f} / km²"
        if den.direct_per_km2 is not None
        else "Direct competitor density: n/a",
        f"  ({den.note})",
        "",
        f"Competition signal:       {metrics.signal.value.upper()}",
        f"  {metrics.signal_reason}",
        "",
        f"Data confidence:          {metrics.data_confidence:.2f}",
        f"  ({metrics.data_confidence_note})",
    ]
    if metrics.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in metrics.warnings)
    return "\n".join(lines)


def _render_demand(demand: DemandSignalsResult) -> str:
    lines = ["", "Local Demand Signals (Phase 2C)", ""]
    lines.append(f"Location:            {demand.location_text or 'n/a'}")
    lines.append(f"Analysis radius:    {demand.analysis_radius_m / 1000:.1f} km")
    lines.append(f"Status:             {demand.status.value}")

    if demand.status in {
        DemandStatus.LOCATION_UNRESOLVED,
        DemandStatus.INVALID_RADIUS,
        DemandStatus.SOURCE_UNAVAILABLE,
    }:
        lines.append("")
        lines.extend(f"  - {w}" for w in demand.warnings)
        return "\n".join(lines)

    c = demand.catchment
    persons = "unknown" if c.persons is None else f"{c.persons:,}"
    households = "unknown" if c.households is None else f"{c.households:,}"
    floor = " (floor)" if c.is_floor else ""
    lines += [
        "",
        f"Settlements in catchment:   {c.settlements_found}",
        f"  with a Census 2011 record: {c.settlements_with_population}",
        f"Catchment population:       {persons}{floor}",
        f"Catchment households:       {households}{floor}",
    ]
    if c.population_coverage is not None:
        lines.append(f"Population coverage:        {c.population_coverage:.0%}")
    if c.density_persons_per_km2 is not None:
        lines.append(f"Population density:         {c.density_persons_per_km2:.0f} / km²")
    if c.duplicates_suppressed:
        lines.append(f"Census codes counted once:  {', '.join(c.duplicates_suppressed)}")
    if c.boundary_settlements:
        share = "" if c.boundary_share is None else f" ({c.boundary_share:.0%} of the total)"
        lines.append(f"Boundary-proximate:        {', '.join(c.boundary_settlements)}{share}")
    if c.reference_years:
        yrs = ", ".join(str(y) for y in c.reference_years)
        lines.append(f"Population data year(s):    {yrs}")

    a = demand.activity
    lines.append("")
    lines.append(f"Activity anchors:          {a.total_points}")
    for kind, count in sorted(a.counts_by_kind.items()):
        near = a.nearest_m_by_kind.get(kind)
        near_txt = "" if near is None else f", nearest {_km(near)}"
        lines.append(f"  {kind.value:<16} {count}{near_txt}")

    if demand.osm_tagged_population_total is not None:
        lines.append("")
        lines.append(
            f"OSM-tagged population (secondary, not in the total): "
            f"{demand.osm_tagged_population_total:,}"
        )

    if demand.competitors_per_1000_people is not None:
        lines.append("")
        lines.append(
            f"Direct competitors per 1,000 residents: {demand.competitors_per_1000_people:.2f}"
        )
        lines.append(f"  ({demand.competitors_per_1000_people_note})")

    lines += [
        "",
        f"Demand-data confidence:     {demand.demand_data_confidence:.2f}",
        f"  ({demand.demand_data_confidence_note})",
    ]
    if demand.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in demand.warnings)
    return "\n".join(lines)


def _render_assessment(a: MarketAssessmentResult) -> str:
    lines = ["", "Overall Market Assessment (Phase 2D)", ""]
    label = a.proposed_category.value + (
        f" + {', '.join(a.proposed_subtypes)}" if a.proposed_subtypes else ""
    )
    lines.append(f"Location:            {a.location_text or 'n/a'}")
    lines.append(f"Proposed business:  {label}")
    lines.append(f"Analysis radius:    {a.analysis_radius_m / 1000:.1f} km")
    lines.append(f"Status:             {a.status.value}")

    if a.status is not MarketAssessmentStatus.OK:
        lines.append("")
        lines.append(f"Assessment:  {a.label.value.upper()}")
        lines.append(f"  {a.label_reason}")
        lines.extend(f"  - {w}" for w in a.warnings)
        return "\n".join(lines)

    tier = a.scale_basis.tier.value
    lines += [
        "",
        f"Catchment scale:    {a.catchment_scale.value.upper()}  (tier: {tier})",
        f"  {a.scale_basis.reason}",
        f"Competition signal: {a.competition_signal.value.upper()}",
        "",
        f"ASSESSMENT:  {a.label.value.upper()}",
        f"  {a.label_reason}",
    ]
    if a.persons_per_direct_competitor is not None:
        lines.append(f"  Residents per direct competitor: {a.persons_per_direct_competitor:,.0f}")

    for title, items in (
        ("Positive signals", a.positive_signals),
        ("Concerns", a.concerns),
        ("Data caveats", a.data_caveats),
    ):
        if items:
            lines.append("")
            lines.append(f"{title}:")
            lines.extend(f"  - [{f.code}] {f.message}" for f in items)

    lines += [
        "",
        f"Assessment-data confidence: {a.assessment_data_confidence:.2f}",
        f"  ({a.assessment_data_confidence_note})",
    ]
    if a.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in a.warnings)
    return "\n".join(lines)


def _render_opportunity(r: OpportunityAnalysisResult) -> str:
    lines = ["", "Business Opportunity & Pivot Analysis (Phase 3)", ""]
    lines.append(f"Location:            {r.location_text or 'n/a'}")
    lines.append(f"Analysis radius:    {r.analysis_radius_m / 1000:.1f} km")
    proposed = r.proposed_category.value if r.proposed_category else "(none stated)"
    lines.append(f"Proposed business:  {proposed}")
    lines.append(f"Status:             {r.status.value}")
    lines.append(
        f"Profile completeness: {r.profile_completeness:.0%}   "
        f"Market-data confidence: {r.market_data_confidence:.2f}"
    )
    lines.append("")
    lines.append(f"STANCE:  {r.stance.value.upper()}")
    lines.append(f"  {r.stance_reason}")
    if r.recommended_pivot is not None:
        lines.append(f"  Recommended pivot: {r.recommended_pivot.value}")

    lines.append("")
    lines.append(
        f"  {'#':>2}  {'Business':<20} {'Score':>5}  {'Market label':<22} "
        f"{'Evidence':<10} {'Capital fit':<13} {'Cov':>4}"
    )
    for c in r.candidates:
        score = "n/a" if c.opportunity_score is None else str(c.opportunity_score)
        ev = "sufficient" if c.evidence_sufficient else "thin"
        star = " *" if c.is_proposed else ""
        cap = "" if c.weight_coverage_pct == 100 else f" [{c.weight_coverage_pct}% wt]"
        lines.append(
            f"  {c.rank:>2}  {c.category.value:<20} {score:>5}  {c.market_label.value:<22} "
            f"{ev:<10} {c.capital_fit.value:<13} {c.coverage_confidence:>4.2f}{star}{cap}"
        )
        if c.capability_incomplete:
            lines.append(f"      (capability check incomplete: {', '.join(c.components_missing)})")

    top = r.candidates[0] if r.candidates else None
    if top is not None:
        lines.append("")
        lines.append(f"Top-ranked ({top.category.value}) — component breakdown:")
        for comp in top.components:
            if comp.available and comp.value is not None:
                lines.append(
                    f"  {comp.name:<20} {comp.value:>5.0f}/100  x{comp.effective_weight:.2f} "
                    f"= {comp.contribution:.1f}"
                )
            else:
                lines.append(f"  {comp.name:<20}  (not scored: {comp.unavailable_kind or 'n/a'})")
        for reason in top.reasons:
            lines.append(f"  - {reason}")

    if r.caveats:
        lines.append("")
        lines.append("Caveats:")
        lines.extend(f"  - {c}" for c in r.caveats)
    if r.warnings:
        lines.append("")
        lines.append("Warnings:")
        lines.extend(f"  - {w}" for w in r.warnings)
    return "\n".join(lines)


def _build_profile(
    args: argparse.Namespace, proposed_category: BusinessCategory, resolved: bool
) -> EntrepreneurProfile:
    assets: set[AssetKind] = set()
    for raw in args.asset or []:
        try:
            assets.add(AssetKind(raw.strip().lower()))
        except ValueError:
            valid = ", ".join(sorted(a.value for a in AssetKind))
            raise SystemExit(f"error: unknown --asset {raw!r}. Valid: {valid}") from None
    experience: set[BusinessCategory] = set()
    for raw in args.experience or []:
        try:
            experience.add(BusinessCategory(raw.strip().lower()))
        except ValueError:
            valid = ", ".join(sorted(c.value for c in BusinessCategory))
            raise SystemExit(f"error: unknown --experience {raw!r}. Valid: {valid}") from None
    return EntrepreneurProfile(
        liquid_cash_inr=args.cash,
        assets=assets,
        experience_categories=experience,
        proposed_category=proposed_category if resolved else None,
        proposed_subtypes=list(args.subtype or []),
        proposed_raw_text=args.proposed or args.category,
    )


def _build_analysis(args: argparse.Namespace, result: DiscoveryResult, category: BusinessCategory):
    subtypes = args.subtype or []
    if args.proposed:
        proposed = resolve_proposed_business(args.proposed, subtypes)
    else:
        proposed = proposed_from_category(category, subtypes, raw_text=args.category)
    return analyze_competitors(result, proposed)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    if args.list_categories:
        print("\n".join(sorted(c.value for c in BusinessCategory)))
        return 0

    settings = get_settings()
    configure_logging(args.log_level or settings.log_level)

    try:
        category = BusinessCategory(args.category.strip().lower())
    except ValueError:
        valid = ", ".join(sorted(c.value for c in BusinessCategory))
        print(f"error: unknown category {args.category!r}. Valid: {valid}", file=sys.stderr)
        return 2

    radius_m = int(round(args.radius * 1000))

    # Phase 3 needs the proposed category up front (for the candidate universe
    # and the profile) and widens the single Overpass fetch to one union query.
    opp_proposed = None
    also_fetch: tuple[BusinessCategory, ...] = ()
    opp_candidates: list[BusinessCategory] = []
    if args.opportunity:
        if args.proposed:
            opp_proposed = resolve_proposed_business(args.proposed, args.subtype or [])
            proposed_cat = opp_proposed.category
            proposed_resolved = opp_proposed.resolved
        else:
            proposed_cat = category
            proposed_resolved = category is not BusinessCategory.UNKNOWN
        opp_candidates = [
            BusinessCategory(v) for v in DEFAULT_OPPORTUNITY_CONFIG.candidate_categories
        ]
        if proposed_resolved and proposed_cat not in opp_candidates:
            opp_candidates.append(proposed_cat)
        also_fetch = tuple(c for c in opp_candidates if c != category)

    repository = create_repository() if args.db else InMemoryBusinessRepository()
    with NominatimGeocoder(settings) as geocoder, OverpassSource(settings) as source:
        service = DiscoveryService(geocoder, source, repository, settings)
        # Keep the call identical to a plain Phase 1 run unless Phase 3 asked to
        # widen the fetch.
        extra = {"also_fetch": also_fetch} if also_fetch else {}
        try:
            result = service.discover(
                args.location, category, radius_m, candidate=args.candidate, **extra
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    want_metrics = args.metrics or args.assess
    want_demand = args.demand or args.assess
    want_analysis = args.competitors or want_metrics
    analysis = _build_analysis(args, result, category) if want_analysis else None
    metrics = (
        compute_competition_metrics(analysis, result)
        if want_metrics and analysis is not None
        else None
    )

    demand = None
    if want_demand:
        with OverpassClient(settings) as demand_client:
            evidence = acquire_demand_evidence(result, client=demand_client, settings=settings)
        demand = compute_demand_signals(evidence, competition=metrics)

    assessment = (
        assess_market(metrics, demand, analysis=analysis)
        if args.assess and metrics is not None and demand is not None
        else None
    )

    opportunity = None
    if args.opportunity:
        proposed_cat = opp_proposed.category if opp_proposed is not None else category
        proposed_resolved = (
            opp_proposed.resolved
            if opp_proposed is not None
            else category is not BusinessCategory.UNKNOWN
        )
        profile = _build_profile(args, proposed_cat, proposed_resolved)
        with OverpassClient(settings) as opp_client:
            opp_evidence = acquire_opportunity_evidence(
                result, opp_candidates, client=opp_client, settings=settings
            )
        opportunity = score_opportunities(opp_evidence, profile)

    if args.json:
        payload = opportunity or assessment or demand or metrics or analysis or result
        print(payload.model_dump_json(indent=2))
    else:
        print(_render_human(result, args.max_display))
        if analysis is not None:
            print(_render_analysis(analysis, args.max_display))
        if metrics is not None:
            print(_render_metrics(metrics))
        if demand is not None:
            print(_render_demand(demand))
        if assessment is not None:
            print(_render_assessment(assessment))
        if opportunity is not None:
            print(_render_opportunity(opportunity))

    # Exit non-zero on a non-OK outcome so scripts can detect it.
    code = 0 if result.status is DiscoveryStatus.OK else 1
    if analysis is not None and analysis.status is not CompetitorAnalysisStatus.OK:
        code = 2
    if metrics is not None and metrics.status is not CompetitionMetricsStatus.OK:
        code = 2
    if demand is not None and demand.status in {
        DemandStatus.LOCATION_UNRESOLVED,
        DemandStatus.INVALID_RADIUS,
        DemandStatus.SOURCE_UNAVAILABLE,
    }:
        code = 2
    if assessment is not None and assessment.status is not MarketAssessmentStatus.OK:
        code = 2
    # Phase 3 outcomes are all valid analyses (OK / no_evidence); only a genuine
    # "nothing to score" is an error. Thin-evidence results still exit 0, matching
    # Phase 2D (an `insufficient_evidence` label there keeps status OK).
    if opportunity is not None and opportunity.status is OpportunityStatus.NO_CANDIDATES:
        code = 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
