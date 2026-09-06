"""Deterministic local-demand engine (CLAUDE.md §11, §22, Phase 2C design).

Consumes a :class:`DemandEvidence` (settlements + activity anchors + census
slice, gathered by ``discovery/demand_acquisition.py``) and, optionally, a Phase
2B :class:`CompetitionMetricsResult`. Produces a :class:`DemandSignalsResult`.

Pure: no network, no disk, no wall-clock, no RNG. Distances are taken from the
evidence, never recomputed. No settlement is re-classified here. Every population
figure keeps its provenance; a missing figure is ``None``, never ``0``.
"""

from __future__ import annotations

import math
from collections import Counter

from vyaparsarathi.market.demand_config import DEFAULT_DEMAND_CONFIG, DemandConfig
from vyaparsarathi.market.demand_models import (
    ActivitySummary,
    CatchmentPopulation,
    DemandCoverage,
    DemandSignalsResult,
    DemandStatus,
)
from vyaparsarathi.market.metrics_models import CompetitionMetricsResult, CompetitionMetricsStatus
from vyaparsarathi.models.demand import ActivityKind, DemandEvidence, SettlementHit
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


def _freshness(data_year: int, cfg: DemandConfig) -> float:
    raw = 1.0 - cfg.freshness_decay_per_year * max(0, cfg.reference_year - data_year)
    return max(cfg.freshness_floor, raw)


def _persons(hit: SettlementHit) -> int | None:
    pop = hit.settlement.population
    return pop.persons if pop is not None else None


def _households(hit: SettlementHit) -> int | None:
    pop = hit.settlement.population
    return pop.households if pop is not None else None


def _reference_year(hit: SettlementHit) -> int | None:
    pop = hit.settlement.population
    return pop.reference_year if pop is not None else None


def _sum_or_none(values: list[int]) -> int | None:
    return sum(values) if values else None


def _unique_by_census_code(
    census_hits: list[SettlementHit],
) -> tuple[list[SettlementHit], list[str]]:
    """Keep the first (nearest) hit per census code; the rest are suppressed."""
    seen: set[str] = set()
    kept: list[SettlementHit] = []
    suppressed: list[str] = []
    for hit in census_hits:
        code = hit.settlement.census_code
        assert code is not None  # census_hits are pre-filtered on census_code
        if code in seen:
            suppressed.append(code)
            continue
        seen.add(code)
        kept.append(hit)
    return kept, suppressed


def _weighted_mean(pairs: list[tuple[float, float]]) -> float:
    """pairs of (value, weight); renormalised over the given entries."""
    total_w = sum(w for _, w in pairs)
    if total_w <= 0.0:
        return 0.0
    return sum(v * w for v, w in pairs) / total_w


def compute_demand_signals(
    evidence: DemandEvidence,
    *,
    competition: CompetitionMetricsResult | None = None,
    config: DemandConfig | None = None,
) -> DemandSignalsResult:
    """Compute Phase 2C demand signals for one catchment."""
    cfg = config or DEFAULT_DEMAND_CONFIG
    base: dict[str, object] = {
        "location_text": evidence.location_text,
        "analysis_radius_m": evidence.radius_m,
        "query_latitude": evidence.latitude,
        "query_longitude": evidence.longitude,
        "state": evidence.state,
        "district": evidence.district,
        "interpretation_guidance": list(cfg.interpretation_guidance),
        "config": cfg,
    }
    warnings = list(evidence.warnings)
    has_osm_settlements = any(h.settlement.census_code is None for h in evidence.settlements)

    if evidence.latitude is None or evidence.longitude is None:
        return DemandSignalsResult(
            status=DemandStatus.LOCATION_UNRESOLVED,
            warnings=warnings,
            **base,
        )
    if evidence.radius_m <= 0:
        bad_radius = f"Analysis radius is {evidence.radius_m} m; demand metrics are undefined."
        return DemandSignalsResult(
            status=DemandStatus.INVALID_RADIUS,
            warnings=[bad_radius, *warnings],
            **base,
        )

    osm_settlements = [h for h in evidence.settlements if h.settlement.census_code is None]
    census_raw = [h for h in evidence.settlements if h.settlement.census_code is not None]
    census_unique, duplicates_suppressed = _unique_by_census_code(census_raw)

    osm_failed = bool(evidence.acquisition.errors)
    census_absent = not evidence.acquisition.census_file_present
    covers_area = evidence.acquisition.census_extract_covers_query_area
    ungeolocated = evidence.acquisition.census_population_ungeolocated_in_area

    # -- activity summary (independent of population) ----------------------
    act_counts: Counter[ActivityKind] = Counter()
    act_nearest: dict[ActivityKind, float] = {}
    for hit in evidence.activity_points:
        k = hit.activity_point.kind
        act_counts[k] += 1
        if k not in act_nearest or hit.distance_m < act_nearest[k]:
            act_nearest[k] = hit.distance_m
    activity = ActivitySummary(
        total_points=len(evidence.activity_points),
        counts_by_kind=dict(act_counts),
        nearest_m_by_kind=act_nearest,
    )
    osm_tagged_total = sum(
        h.settlement.osm_tagged_population
        for h in osm_settlements
        if h.settlement.osm_tagged_population is not None
    )
    osm_tagged_population_total = osm_tagged_total or None

    if osm_failed and census_absent:
        return DemandSignalsResult(
            status=DemandStatus.SOURCE_UNAVAILABLE,
            activity=activity,
            osm_tagged_population_total=osm_tagged_population_total,
            warnings=[
                "Every demand data source was unavailable (OpenStreetMap failed and no "
                "Census 2011 extract is present).",
                *warnings,
            ],
            **base,
        )

    settlements_found = max(len(osm_settlements), len(census_unique))
    # A "population record" is a census row that carries a PopulationRecord at all
    # (persons and households are summed independently — one may be absent).
    pop_hits = [h for h in census_unique if h.settlement.population is not None]

    echoed_settlements = sorted(evidence.settlements, key=lambda h: h.distance_m)
    echoed_activity = sorted(evidence.activity_points, key=lambda h: h.distance_m)

    catchment_area_km2 = math.pi * (evidence.radius_m / 1000.0) ** 2

    if settlements_found == 0:
        return DemandSignalsResult(
            status=DemandStatus.NO_SETTLEMENTS_FOUND,
            settlements=echoed_settlements,
            activity_points=echoed_activity,
            activity=activity,
            osm_tagged_population_total=osm_tagged_population_total,
            catchment=CatchmentPopulation(
                settlements_found=0,
                population_coverage=None,
                catchment_area_km2=catchment_area_km2,
                duplicates_suppressed=duplicates_suppressed,
            ),
            coverage=DemandCoverage(
                signals_available=_signals(has_osm_settlements, False, evidence, present=True),
                signals_unavailable=_signals(has_osm_settlements, False, evidence, present=False),
                census_extract_covers_query_area=covers_area,
            ),
            warnings=[
                "No settlement was found in this catchment from any source; OpenStreetMap "
                "coverage may be the cause, not absence on the ground.",
                *warnings,
            ],
            **base,
        )

    persons = _sum_or_none([p for p in map(_persons, pop_hits) if p is not None])
    households = _sum_or_none([h for h in map(_households, pop_hits) if h is not None])

    settlements_with_population = len(pop_hits)
    population_coverage = (
        settlements_with_population / settlements_found if settlements_found else None
    )
    is_floor = (
        (population_coverage < 1.0)
        if (persons is not None and population_coverage is not None)
        else None
    )

    density = (
        persons / catchment_area_km2 if (persons is not None and catchment_area_km2 > 0) else None
    )

    boundary_cut = cfg.boundary_fraction * evidence.radius_m
    boundary_hits = [h for h in pop_hits if h.distance_m >= boundary_cut]
    boundary_persons = _sum_or_none([p for p in map(_persons, boundary_hits) if p is not None])
    boundary_share = (
        boundary_persons / persons
        if (boundary_persons is not None and persons is not None and persons > 0)
        else None
    )
    reference_years = sorted({y for y in map(_reference_year, pop_hits) if y is not None})

    catchment = CatchmentPopulation(
        persons=persons,
        households=households,
        is_floor=is_floor,
        settlements_found=settlements_found,
        settlements_with_population=settlements_with_population,
        population_coverage=population_coverage,
        duplicates_suppressed=duplicates_suppressed,
        reference_years=reference_years,
        boundary_settlements=[
            h.settlement.name or h.settlement.census_code or "(unnamed)" for h in boundary_hits
        ],
        boundary_persons=boundary_persons,
        boundary_share=boundary_share,
        catchment_area_km2=catchment_area_km2,
        density_persons_per_km2=density,
    )

    # -- competitors per 1,000 residents (Phase 2B join) ------------------
    competitors_per_1000 = None
    if (
        competition is not None
        and competition.status is CompetitionMetricsStatus.OK
        and persons is not None
        and persons > 0
    ):
        competitors_per_1000 = 1000.0 * competition.direct_count / persons

    # -- confidence (evidence quality, NOT demand magnitude) --------------
    data_year = reference_years[0] if reference_years else cfg.census_reference_year
    freshness = _freshness(data_year, cfg)
    geo_term = 1.0 if covers_area else 0.0

    population_conf: float | None
    if persons is None or population_coverage is None:
        population_conf = None
    else:
        population_conf = cfg.census_tier * freshness * population_coverage * geo_term

    settlement_conf = (
        min(cfg.osm_single_source_ceiling, 0.3 + 0.05 * settlements_found)
        if settlements_found
        else None
    )
    distinct_kinds = len(act_counts)
    activity_conf = (
        min(cfg.osm_single_source_ceiling, 0.2 + 0.1 * distinct_kinds) if distinct_kinds else None
    )

    weighted: list[tuple[float, float]] = []
    if population_conf is not None:
        weighted.append((population_conf, cfg.weight_population))
    if settlement_conf is not None:
        weighted.append((settlement_conf, cfg.weight_settlement))
    if activity_conf is not None:
        weighted.append((activity_conf, cfg.weight_activity))
    demand_data_confidence = round(_weighted_mean(weighted), 3)

    basis: dict[str, float | None] = {
        "freshness": round(freshness, 3),
        "geography_in_curated_extract": geo_term,
        "population_coverage": population_coverage,
        "census_tier": cfg.census_tier,
        "osm_single_source_ceiling": cfg.osm_single_source_ceiling,
        "population_confidence": None if population_conf is None else round(population_conf, 3),
        "settlement_confidence": None if settlement_conf is None else round(settlement_conf, 3),
        "activity_confidence": None if activity_conf is None else round(activity_conf, 3),
    }

    coverage = DemandCoverage(
        signals_available=_signals(has_osm_settlements, bool(pop_hits), evidence, present=True),
        signals_unavailable=_signals(has_osm_settlements, bool(pop_hits), evidence, present=False),
        population_confidence=None if population_conf is None else round(population_conf, 3),
        settlement_confidence=None if settlement_conf is None else round(settlement_conf, 3),
        activity_confidence=None if activity_conf is None else round(activity_conf, 3),
        source_reference_years=({"census_2011_pca_village": data_year} if pop_hits else {}),
        geography_mismatch=(persons is None and not covers_area and settlements_found > 0),
        census_extract_covers_query_area=covers_area,
        population_records_ungeolocated_in_area=ungeolocated,
        population_available_not_geolocated=(not pop_hits and ungeolocated > 0),
    )

    if duplicates_suppressed:
        warnings.append(
            f"{len(duplicates_suppressed)} settlement record(s) shared a census code with "
            f"another and were counted once: {', '.join(duplicates_suppressed)}."
        )

    if pop_hits:
        status = DemandStatus.OK
    elif ungeolocated > 0:
        status = DemandStatus.POPULATION_NOT_GEOLOCATED
        if not any("be placed on the map" in w for w in warnings):
            warnings.insert(
                0,
                f"Census 2011 population exists for {ungeolocated} village(s) in this area but "
                "none could be placed on the map; catchment population is not computable here, "
                "not zero.",
            )
    else:
        status = DemandStatus.NO_POPULATION_DATA
        warnings.insert(
            0,
            "No population record is available for any settlement in this catchment; "
            "catchment population is unknown, not zero.",
        )

    if status is DemandStatus.OK and is_floor:
        warnings.append(
            f"Catchment population is a floor: {settlements_with_population} of "
            f"{settlements_found} settlements carry a Census 2011 record."
        )

    logger.info(
        "demand signals (%s, r=%dm): persons=%s coverage=%s activity=%d conf=%.2f",
        status.value,
        evidence.radius_m,
        persons,
        None if population_coverage is None else round(population_coverage, 2),
        activity.total_points,
        demand_data_confidence,
    )

    return DemandSignalsResult(
        status=status,
        settlements=echoed_settlements,
        activity_points=echoed_activity,
        osm_tagged_population_total=osm_tagged_population_total,
        catchment=catchment,
        activity=activity,
        competitors_per_1000_people=competitors_per_1000,
        demand_data_confidence=demand_data_confidence,
        demand_data_confidence_basis=basis,
        coverage=coverage,
        warnings=warnings,
        **base,
    )


def _signals(
    has_osm_settlements: bool,
    has_population: bool,
    evidence: DemandEvidence,
    *,
    present: bool,
) -> list[str]:
    """The list of signal names that are (``present=True``) or are not
    (``present=False``) backed by data in this run."""
    ungeolocated = evidence.acquisition.census_population_ungeolocated_in_area > 0
    state = {
        "osm_settlements": has_osm_settlements,
        "census_population_geolocated": has_population,
        "census_population_ungeolocated": ungeolocated and not has_population,
        "activity_anchors": bool(evidence.activity_points),
    }
    return [name for name, ok in state.items() if ok is present]
