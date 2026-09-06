"""Phase 2C acquisition — the only demand layer that touches network / disk.

Turns a Phase 1 :class:`DiscoveryResult` into a :class:`DemandEvidence`: one
Overpass union query for settlements + activity anchors, plus a slice of the
local Census 2011 extract. Never raises past its boundary — every failure is
caught and recorded as a warning / error string so the pure engine can decide
the status (CLAUDE.md §33, mirrors ``DiscoveryService.discover``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from vyaparsarathi.categories.osm_place_tags import DEMAND_QUERY_SELECTORS
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, SourcePayloadError, SourceUnavailableError
from vyaparsarathi.models.demand import (
    ActivityHit,
    DemandAcquisitionReport,
    DemandEvidence,
    SettlementHit,
)
from vyaparsarathi.models.results import DiscoveryResult
from vyaparsarathi.normalization.settlement import (
    normalize_activity,
    normalize_settlement,
    settlement_from_census_row,
)
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.sources.osm.places import fetch_demand_elements
from vyaparsarathi.utils.geo import haversine_m
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)


def _catchment(discovery: DiscoveryResult) -> tuple[float, float, int] | None:
    if discovery.query is not None:
        q = discovery.query
        return q.latitude, q.longitude, q.radius_m
    if discovery.resolved_place is not None:
        rp = discovery.resolved_place
        return rp.latitude, rp.longitude, discovery.requested_radius_m
    return None


def acquire_demand_evidence(
    discovery: DiscoveryResult,
    *,
    client: OverpassClient,
    census: CensusVillageSource | None = None,
    settings: Settings | None = None,
    clock: Clock = utcnow,
    selectors: Sequence[tuple[str, str]] = tuple(DEMAND_QUERY_SELECTORS),
) -> DemandEvidence:
    """Gather demand evidence around the location Phase 1 resolved."""
    s = settings or get_settings()
    census = census or CensusVillageSource(settings=s)
    acquired_at = clock()

    rp = discovery.resolved_place
    state = rp.state if rp is not None else None
    district = rp.district if rp is not None else None
    base = {
        "location_text": discovery.query_text,
        "state": state,
        "district": district,
        "acquired_at": acquired_at,
    }

    catchment = _catchment(discovery)
    if catchment is None:
        return DemandEvidence(
            warnings=[
                "Phase 1 did not resolve a location; demand analysis needs a catchment centre."
            ],
            **base,
        )
    latitude, longitude, radius_m = catchment

    report = DemandAcquisitionReport()
    warnings: list[str] = []
    settlement_hits: list[SettlementHit] = []
    activity_hits: list[ActivityHit] = []
    unmapped: Counter[str] = Counter()

    # -- OSM settlements + activity anchors (one union query) ----------------
    try:
        fetch = fetch_demand_elements(
            client,
            latitude=latitude,
            longitude=longitude,
            radius_m=radius_m,
            selectors=selectors,
            timeout_s=s.overpass_query_timeout_s,
        )
    except (SourceUnavailableError, SourcePayloadError, HttpError) as exc:
        report.errors.append(f"OpenStreetMap / Overpass unavailable: {exc}")
        warnings.append(
            "OpenStreetMap settlement layer unavailable; settlement coverage cannot be "
            "measured against an independent count."
        )
        logger.warning("demand OSM fetch failed: %s", exc)
    else:
        report.osm_endpoint_used = fetch.endpoint_used
        report.osm_mirror_fallback_used = fetch.mirror_fallback_used
        report.osm_elements_raw = fetch.raw_count
        report.osm_dropped_no_coordinates = fetch.dropped_no_coordinates
        for element in fetch.elements:
            normalized = normalize_settlement(element, now=acquired_at)
            if normalized is not None:
                st = normalized.settlement
                if normalized.unmapped_tag:
                    unmapped[normalized.unmapped_tag] += 1
                dist = haversine_m(latitude, longitude, st.latitude, st.longitude)
                if dist <= radius_m:
                    settlement_hits.append(SettlementHit(settlement=st, distance_m=dist))
                continue
            activity = normalize_activity(element)
            if activity is not None:
                dist = haversine_m(latitude, longitude, activity.latitude, activity.longitude)
                if dist <= radius_m:
                    activity_hits.append(ActivityHit(activity_point=activity, distance_m=dist))
        if fetch.mirror_fallback_used:
            warnings.append("An Overpass mirror was used after the primary endpoint failed.")
        report.osm_settlements_parsed = len(settlement_hits)
        report.osm_activity_parsed = len(activity_hits)
        report.unmapped_place_tags = dict(unmapped)
        if unmapped:
            warnings.append(
                f"{sum(unmapped.values())} OSM place element(s) across {len(unmapped)} tag(s) "
                "were not recognised as habitation."
            )

    # -- Census 2011 village extract ------------------------------------
    near = census.near(latitude, longitude, radius_m, state=state, district=district)
    report.census_file_present = near.file_present
    report.census_rows_in_extract = near.rows_in_extract
    report.census_rows_in_radius = near.rows_in_radius
    report.census_population_ungeolocated_in_area = near.population_rows_ungeolocated_in_area
    report.census_states_in_extract = near.states_in_extract
    report.census_extract_covers_query_area = near.covers_query_area
    if near.parse_errors:
        warnings.append(
            f"{near.parse_errors} census extract row(s) could not be parsed and were skipped."
        )
    if not near.file_present:
        warnings.append(
            "The Census 2011 village extract file is not present; no population data is available."
        )
    elif not near.covers_query_area:
        covered = ", ".join(near.states_in_extract) or "no states"
        warnings.append(
            f"This query is outside the Census 2011 extract's coverage ({covered}); "
            "catchment population is unknown, not zero."
        )
    elif not near.rows and near.population_rows_ungeolocated_in_area > 0:
        warnings.append(
            f"Census 2011 population exists for {near.population_rows_ungeolocated_in_area} "
            f"village(s) in this area, but none could be placed on the map (no village-boundary "
            "coordinates for this state); catchment population cannot be computed here."
        )
    for row in near.rows:
        assert row.latitude is not None and row.longitude is not None  # geolocated rows only
        census_settlement = settlement_from_census_row(row, retrieved_at=acquired_at)
        dist = haversine_m(latitude, longitude, row.latitude, row.longitude)
        settlement_hits.append(SettlementHit(settlement=census_settlement, distance_m=dist))

    settlement_hits.sort(key=lambda h: h.distance_m)
    activity_hits.sort(key=lambda h: h.distance_m)

    warnings.append(
        "Catchment population sums only settlements with a Census 2011 record; where "
        "coverage is below 1.0 it is a lower bound, not a measured catchment population. "
        "Census 2011 is the most recent village-level enumeration available."
    )

    logger.info(
        "demand evidence: %d settlement(s), %d activity anchor(s), census rows in radius=%d",
        len(settlement_hits),
        len(activity_hits),
        near.rows_in_radius,
    )
    return DemandEvidence(
        latitude=latitude,
        longitude=longitude,
        radius_m=radius_m,
        settlements=settlement_hits,
        activity_points=activity_hits,
        acquisition=report,
        warnings=warnings,
        **base,
    )
