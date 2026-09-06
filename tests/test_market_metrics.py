"""Phase 2B competition metrics (CLAUDE.md §11, STEP 14).

Offline and deterministic: every input is a hand-built Phase 1 ``DiscoveryResult``
/ Phase 2A ``CompetitorAnalysisResult``. No HTTP, no database, no clock.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from vyaparsarathi.market import (
    analyze_competitors,
    compute_competition_metrics,
    metrics_from_discovery,
    proposed_from_category,
    resolve_proposed_business,
)
from vyaparsarathi.market.metrics_config import CompetitionMetricsConfig
from vyaparsarathi.market.metrics_models import (
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
    CompetitionSignal,
)
from vyaparsarathi.market.models import (
    ClassifiedCompetitor,
    CompetitorAnalysisResult,
    CompetitorAnalysisStatus,
    Relationship,
)
from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness
from vyaparsarathi.models.results import DiscoveryQuery, DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.models.taxonomy import SourceName

_NOW = datetime(2024, 5, 1, tzinfo=UTC)


def _biz(name: str, category: C, sid: str) -> NormalizedBusiness:
    return NormalizedBusiness(
        name=name,
        normalized_name=name.lower(),
        category=category,
        latitude=25.7,
        longitude=85.2,
        source=SourceName.OSM,
        source_id=sid,
        first_seen=_NOW,
        last_updated=_NOW,
        data_quality=0.8,
    )


def _discovery(
    *businesses: tuple[NormalizedBusiness, float],
    radius_m: int = 5_000,
    confidence: float = 0.7,
    status: DiscoveryStatus = DiscoveryStatus.OK,
    with_query: bool = True,
) -> DiscoveryResult:
    hits = [BusinessHit(business=b, distance_m=d) for b, d in businesses]
    query = (
        DiscoveryQuery(
            location_text="Hajipur",
            latitude=25.7,
            longitude=85.2,
            radius_m=radius_m,
            category=C.GROCERY,
        )
        if with_query
        else None
    )
    return DiscoveryResult(
        status=status,
        query_text="Hajipur, Vaishali, Bihar",
        category=C.GROCERY,
        requested_radius_m=radius_m,
        query=query,
        businesses=hits,
        confidence=confidence,
    )


def _metrics(
    *businesses: tuple[NormalizedBusiness, float],
    radius_m: int = 5_000,
    confidence: float = 0.7,
) -> CompetitionMetricsResult:
    disc = _discovery(*businesses, radius_m=radius_m, confidence=confidence)
    analysis = analyze_competitors(disc, proposed_from_category(C.GROCERY))
    return compute_competition_metrics(analysis, disc)


# -- counts -----------------------------------------------------------------


def test_zero_competitors() -> None:
    m = _metrics()
    assert m.status is CompetitionMetricsStatus.OK
    assert m.direct_count == 0
    assert m.adjacent_count == 0
    assert m.total_relevant_count == 0
    assert m.direct_distance.nearest_m is None
    assert m.direct_distance.mean_m is None
    assert m.direct_distance.median_m is None
    # density is mathematically valid (0 / area) at a valid radius, not None
    assert m.density.direct_per_km2 == 0.0
    assert m.signal is CompetitionSignal.NONE


def test_one_competitor_mean_and_median_equal_its_distance() -> None:
    m = _metrics((_biz("Solo Kirana", C.GROCERY, "n/1"), 250.0))
    assert m.direct_count == 1
    assert m.direct_distance.nearest_m == 250.0
    assert m.direct_distance.farthest_m == 250.0
    assert m.direct_distance.mean_m == 250.0
    assert m.direct_distance.median_m == 250.0


def test_multiple_competitors_distance_stats() -> None:
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 200.0),
        (_biz("C", C.GROCERY, "n/3"), 300.0),
        (_biz("D", C.GROCERY, "n/4"), 400.0),
    )
    assert m.direct_count == 4
    assert m.direct_distance.nearest_m == 100.0
    assert m.direct_distance.farthest_m == 400.0
    assert m.direct_distance.mean_m == 250.0
    assert m.direct_distance.median_m == 250.0  # (200 + 300) / 2


def test_odd_count_median_is_middle_value() -> None:
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 200.0),
        (_biz("C", C.GROCERY, "n/3"), 900.0),
    )
    assert m.direct_distance.median_m == 200.0


def test_direct_and_adjacent_are_separated() -> None:
    m = _metrics(
        (_biz("Kirana", C.GROCERY, "n/1"), 100.0),
        (_biz("Village Store", C.GENERAL_STORE, "n/2"), 150.0),
        (_biz("Milk Booth", C.DAIRY, "n/3"), 400.0),
        (_biz("Snacks", C.FOOD_STALL, "n/4"), 500.0),
    )
    assert m.direct_count == 2  # grocery + general_store
    assert m.adjacent_count == 2  # dairy + food_stall
    assert m.total_relevant_count == 4
    # direct-only stats exclude the adjacent businesses
    assert m.direct_distance.farthest_m == 150.0
    # relevant stats span both
    assert m.relevant_distance.farthest_m == 500.0
    assert m.relevant_distance.count == 4


def test_same_distance_competitors_handled() -> None:
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 200.0),
        (_biz("B", C.GROCERY, "n/2"), 200.0),
        (_biz("C", C.GROCERY, "n/3"), 900.0),
    )
    assert m.direct_distance.nearest_m == 200.0
    assert m.direct_distance.median_m == 200.0


# -- distance bands -------------------------------------------------------


def test_distance_band_counts_are_cumulative() -> None:
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 300.0),
        (_biz("B", C.GROCERY, "n/2"), 800.0),
        (_biz("C", C.GROCERY, "n/3"), 1_500.0),
        (_biz("D", C.GROCERY, "n/4"), 4_000.0),
    )
    by_edge = {b.max_distance_m: b.direct_count for b in m.distance_bands}
    assert by_edge == {500: 1, 1_000: 2, 2_000: 3, 5_000: 4}


def test_band_wider_than_radius_is_flagged() -> None:
    m = _metrics((_biz("A", C.GROCERY, "n/1"), 100.0), radius_m=2_000)
    flags = {b.max_distance_m: b.exceeds_radius for b in m.distance_bands}
    assert flags == {500: False, 1_000: False, 2_000: False, 5_000: True}


# -- density ------------------------------------------------------------


def test_density_normal_radius() -> None:
    # radius 1 km -> area = pi km^2; 2 direct competitors -> 2 / pi per km^2
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 200.0),
        radius_m=1_000,
    )
    assert m.density.catchment_area_km2 is not None
    assert abs(m.density.catchment_area_km2 - 3.14159265) < 1e-4
    assert m.density.direct_per_km2 is not None
    assert abs(m.density.direct_per_km2 - (2.0 / 3.14159265)) < 1e-4


def test_density_zero_competitors_is_zero_not_none() -> None:
    m = _metrics(radius_m=3_000)
    assert m.density.direct_per_km2 == 0.0
    assert m.density.relevant_per_km2 == 0.0


def test_invalid_radius_yields_no_density() -> None:
    disc = _discovery((_biz("A", C.GROCERY, "n/1"), 10.0), radius_m=1, with_query=False)
    # force an invalid radius that the query model would reject
    disc = disc.model_copy(update={"requested_radius_m": 0})
    analysis = analyze_competitors(disc, proposed_from_category(C.GROCERY))
    m = compute_competition_metrics(analysis, disc)
    assert m.status is CompetitionMetricsStatus.INVALID_RADIUS
    assert m.density.direct_per_km2 is None
    assert m.density.catchment_area_km2 is None
    assert any("radius" in w.lower() for w in m.warnings)


# -- radius filtering --------------------------------------------------


def test_competitor_outside_radius_is_excluded() -> None:
    m = _metrics(
        (_biz("Inside", C.GROCERY, "n/1"), 1_000.0),
        (_biz("Outside", C.GROCERY, "n/2"), 4_000.0),
        radius_m=2_000,
    )
    assert m.direct_count == 1
    assert m.direct_distance.farthest_m == 1_000.0
    assert any("beyond the" in w for w in m.warnings)


# -- missing distance ------------------------------------------------


def test_missing_distance_is_counted_but_excluded_from_stats() -> None:
    analysis = CompetitorAnalysisResult(
        status=CompetitorAnalysisStatus.OK,
        proposed=proposed_from_category(C.GROCERY),
        location_text="Hajipur, Vaishali, Bihar",
        direct_competitors=[
            ClassifiedCompetitor(
                business=_biz("Known", C.GROCERY, "n/1"),
                distance_m=300.0,
                relationship=Relationship.DIRECT,
                reason="same category.",
            ),
            ClassifiedCompetitor(
                business=_biz("Unknown dist", C.GROCERY, "n/2"),
                distance_m=None,
                relationship=Relationship.DIRECT,
                reason="same category.",
            ),
        ],
    )
    disc = _discovery(radius_m=5_000)
    m = compute_competition_metrics(analysis, disc)
    assert m.direct_count == 2
    assert m.direct_distance.count == 1
    assert m.direct_distance.missing_distance == 1
    assert m.direct_distance.nearest_m == 300.0


# -- competition signal -------------------------------------------


def test_signal_none_when_no_direct_competitors() -> None:
    m = _metrics((_biz("Dairy", C.DAIRY, "n/1"), 100.0))  # adjacent only
    assert m.direct_count == 0
    assert m.signal is CompetitionSignal.NONE


def test_signal_escalates_with_count() -> None:
    # small radius so density does not dominate; 3 direct -> at least MODERATE
    three = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 120.0),
        (_biz("C", C.GROCERY, "n/3"), 140.0),
        radius_m=20_000,
    )
    assert three.signal in {CompetitionSignal.MODERATE, CompetitionSignal.HIGH}
    six = _metrics(
        *[(_biz(f"S{i}", C.GROCERY, f"n/{i}"), 100.0 + i) for i in range(6)],
        radius_m=20_000,
    )
    assert six.signal is CompetitionSignal.HIGH
    assert "6 direct" in six.signal_reason


def test_signal_thresholds_are_configurable() -> None:
    cfg = CompetitionMetricsConfig(signal_count_moderate=2, signal_count_high=3)
    disc = _discovery(
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 200.0),
        radius_m=20_000,
    )
    analysis = analyze_competitors(disc, proposed_from_category(C.GROCERY))
    m = compute_competition_metrics(analysis, disc, config=cfg)
    assert m.signal is CompetitionSignal.MODERATE
    assert m.config.signal_count_moderate == 2
    assert m.signal_basis["count_moderate_threshold"] == 2.0


# -- data confidence vs viability -------------------------------


def test_data_confidence_is_passed_through_and_not_viability() -> None:
    m = _metrics((_biz("A", C.GROCERY, "n/1"), 100.0), confidence=0.42)
    assert m.data_confidence == 0.42
    assert "viability" in m.data_confidence_note.lower()
    # there is no viability / recommendation field on the result
    assert "viability" not in m.model_dump()
    assert "recommendation" not in m.model_dump()
    assert "score" not in m.model_dump()


# -- unknown category passthrough ---------------------------------


def test_unknown_proposed_category_passes_through() -> None:
    disc = _discovery((_biz("A", C.GROCERY, "n/1"), 100.0))
    analysis = analyze_competitors(disc, resolve_proposed_business("spaceship parts"))
    m = compute_competition_metrics(analysis, disc)
    assert m.status is CompetitionMetricsStatus.UNKNOWN_CATEGORY
    assert m.direct_count == 0
    assert m.signal is CompetitionSignal.NONE
    assert m.warnings


# -- convenience, determinism, serialization --------------------


def test_metrics_from_discovery_end_to_end() -> None:
    disc = _discovery(
        (_biz("Kirana", C.GROCERY, "n/1"), 100.0),
        (_biz("Dairy", C.DAIRY, "n/2"), 200.0),
    )
    m = metrics_from_discovery(disc)
    assert m.direct_count == 1
    assert m.adjacent_count == 1
    assert m.proposed_category is C.GROCERY


def test_metrics_are_deterministic() -> None:
    args = (
        (_biz("A", C.GROCERY, "n/1"), 100.0),
        (_biz("B", C.GROCERY, "n/2"), 800.0),
    )
    disc = _discovery(*args)
    analysis = analyze_competitors(disc, proposed_from_category(C.GROCERY))
    a = compute_competition_metrics(analysis, disc).model_dump()
    b = compute_competition_metrics(analysis, disc).model_dump()
    assert a == b


def test_result_is_json_serializable_with_units_and_config() -> None:
    m = _metrics(
        (_biz("A", C.GROCERY, "n/1"), 300.0),
        (_biz("B", C.GROCERY, "n/2"), 800.0),
    )
    payload = json.loads(m.model_dump_json())
    assert payload["direct_distance"]["unit"] == "m"
    assert payload["density"]["unit"] == "per_km2"
    assert payload["config"]["distance_bands_m"] == [500, 1_000, 2_000, 5_000]
    assert payload["status"] == "ok"
    assert isinstance(payload["warnings"], list) and payload["warnings"]
