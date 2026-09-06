"""Phase 2C demand engine (CLAUDE.md §11, §22). Pure — every input is a
hand-built ``DemandEvidence``; no HTTP, no disk, no clock."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from vyaparsarathi.market.demand import compute_demand_signals
from vyaparsarathi.market.demand_config import DemandConfig
from vyaparsarathi.market.demand_models import DemandStatus
from vyaparsarathi.market.metrics_models import (
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
)
from vyaparsarathi.models.business import ProvenanceEntry
from vyaparsarathi.models.demand import (
    ActivityHit,
    ActivityKind,
    ActivityPoint,
    DemandAcquisitionReport,
    DemandEvidence,
    GeographyLevel,
    PopulationRecord,
    Settlement,
    SettlementHit,
    SettlementType,
)
from vyaparsarathi.models.taxonomy import SourceName

_NOW = datetime(2026, 3, 1, tzinfo=UTC)


def _prov(source: SourceName, sid: str) -> ProvenanceEntry:
    return ProvenanceEntry(source=source, source_id=sid, retrieved_at=_NOW)


def _census_hit(
    name: str,
    code: str,
    persons: int | None,
    dist: float,
    *,
    households: int | None = None,
    ref_year: int = 2011,
) -> SettlementHit:
    prov = _prov(SourceName.GOVT, code)
    pop = PopulationRecord(
        persons=persons,
        households=households,
        geography_level=GeographyLevel.VILLAGE,
        dataset="census_2011_pca_village",
        reference_year=ref_year,
        provenance=prov,
        quality=0.85,
    )
    return SettlementHit(
        settlement=Settlement(
            name=name,
            normalized_name=name.lower(),
            place_type=SettlementType.VILLAGE,
            latitude=25.7,
            longitude=85.2,
            census_code=code,
            population=pop,
            provenance=[prov],
            data_quality=0.85,
        ),
        distance_m=dist,
    )


def _osm_hit(name: str, dist: float, *, osm_pop: int | None = None) -> SettlementHit:
    return SettlementHit(
        settlement=Settlement(
            name=name,
            normalized_name=name.lower(),
            place_type=SettlementType.VILLAGE,
            latitude=25.7,
            longitude=85.2,
            census_code=None,
            osm_tagged_population=osm_pop,
            provenance=[_prov(SourceName.OSM, f"node/{name}")],
            data_quality=0.6,
        ),
        distance_m=dist,
    )


def _activity(kind: ActivityKind, dist: float) -> ActivityHit:
    return ActivityHit(
        activity_point=ActivityPoint(
            kind=kind,
            name=f"{kind.value} {dist:.0f}",
            latitude=25.7,
            longitude=85.2,
            source=SourceName.OSM,
            source_id=f"node/{kind.value}{dist:.0f}",
        ),
        distance_m=dist,
    )


def _evidence(
    *settlements: SettlementHit,
    activity: tuple[ActivityHit, ...] = (),
    radius_m: int = 5_000,
    covers_area: bool = True,
    file_present: bool = True,
    errors: tuple[str, ...] = (),
    ungeolocated: int = 0,
) -> DemandEvidence:
    report = DemandAcquisitionReport(
        census_file_present=file_present,
        census_extract_covers_query_area=covers_area,
        census_population_ungeolocated_in_area=ungeolocated,
        errors=list(errors),
    )
    return DemandEvidence(
        latitude=25.7,
        longitude=85.2,
        radius_m=radius_m,
        location_text="Testville",
        state="Testland",
        settlements=list(settlements),
        activity_points=list(activity),
        acquisition=report,
        acquired_at=_NOW,
    )


# -- population present --------------------------------------------------


def test_population_available_sums_and_density() -> None:
    ev = _evidence(
        _census_hit("Jadua", "C1", 5000, 100.0, households=1000),
        _census_hit("Rampur", "C2", 3000, 2000.0, households=600),
        radius_m=5_000,
    )
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.OK
    assert r.catchment.persons == 8000
    assert r.catchment.households == 1600
    assert r.catchment.is_floor is False
    assert r.catchment.population_coverage == 1.0
    area = 3.14159265 * 25  # pi * 5^2
    assert r.catchment.density_persons_per_km2 is not None
    assert abs(r.catchment.density_persons_per_km2 - 8000 / area) < 1.0


def test_partial_coverage_is_a_floor() -> None:
    ev = _evidence(
        _osm_hit("A", 100.0),
        _osm_hit("B", 200.0),
        _osm_hit("C", 300.0),
        _census_hit("A", "C1", 5000, 120.0),
        radius_m=4_000,
    )
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.OK
    assert r.catchment.settlements_found == 3  # OSM enumerates
    assert r.catchment.settlements_with_population == 1
    assert r.catchment.population_coverage == 1 / 3
    assert r.catchment.is_floor is True
    assert 0.0 <= r.catchment.population_coverage <= 1.0


def test_households_independent_of_persons() -> None:
    ev = _evidence(
        _census_hit("A", "C1", 4000, 100.0, households=None),
        _census_hit("B", "C2", None, 200.0, households=300),
    )
    r = compute_demand_signals(ev)
    # persons summed over rows that have persons; households over rows that have households
    assert r.catchment.persons == 4000
    assert r.catchment.households == 300


def test_duplicate_census_code_counted_once() -> None:
    ev = _evidence(
        _census_hit("Jadua", "C1", 5000, 100.0),
        _census_hit("Jadua dup", "C1", 5000, 150.0),
        _census_hit("Rampur", "C2", 3000, 400.0),
    )
    r = compute_demand_signals(ev)
    assert r.catchment.persons == 8000  # C1 counted once, not 13000
    assert r.catchment.duplicates_suppressed == ["C1"]
    assert any("counted once" in w for w in r.warnings)


def test_settlement_outside_radius_excluded_by_acquisition_is_respected() -> None:
    # the engine trusts the acquisition radius filter; a hit beyond radius still
    # would only enter if acquisition passed it — model that it did not.
    ev = _evidence(_census_hit("Near", "C1", 2000, 500.0), radius_m=5_000)
    r = compute_demand_signals(ev)
    assert r.catchment.settlements_found == 1


def test_input_ordering_does_not_change_result() -> None:
    a = _census_hit("A", "C1", 1000, 100.0)
    b = _census_hit("B", "C2", 2000, 900.0)
    c = _census_hit("C", "C3", 3000, 400.0)
    r1 = compute_demand_signals(_evidence(a, b, c)).model_dump()
    r2 = compute_demand_signals(_evidence(c, a, b)).model_dump()
    assert r1 == r2


# -- boundary sensitivity ---------------------------------------------


def test_boundary_settlements_and_share() -> None:
    ev = _evidence(
        _census_hit("Core", "C1", 7000, 500.0),
        _census_hit("Edge", "C2", 3000, 4600.0),  # 0.92 * 5000 -> boundary
        radius_m=5_000,
    )
    r = compute_demand_signals(ev)
    assert r.catchment.boundary_settlements == ["Edge"]
    assert r.catchment.boundary_persons == 3000
    assert abs((r.catchment.boundary_share or 0) - 3000 / 10000) < 1e-9


# -- no population / no settlements ----------------------------------


def test_no_population_data_is_first_class_not_zero() -> None:
    ev = _evidence(_osm_hit("A", 100.0), _osm_hit("B", 300.0), file_present=True, covers_area=False)
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.NO_POPULATION_DATA
    assert r.catchment.persons is None
    assert r.catchment.households is None
    assert r.catchment.population_coverage == 0.0
    assert r.catchment.settlements_found == 2
    assert any("unknown, not zero" in w for w in r.warnings)


def test_no_settlements_found() -> None:
    r = compute_demand_signals(_evidence())
    assert r.status is DemandStatus.NO_SETTLEMENTS_FOUND
    assert r.catchment.persons is None
    assert r.catchment.population_coverage is None


def test_population_available_but_not_geolocated_is_a_distinct_state() -> None:
    # Census population exists for villages in the area, but none is on the map.
    ev = _evidence(_osm_hit("A", 100.0), _osm_hit("B", 300.0), ungeolocated=7)
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.POPULATION_NOT_GEOLOCATED
    assert r.status is not DemandStatus.NO_POPULATION_DATA  # not collapsed
    assert r.catchment.persons is None  # not zero
    assert r.coverage.population_available_not_geolocated is True
    assert r.coverage.population_records_ungeolocated_in_area == 7
    assert "census_population_ungeolocated" in r.coverage.signals_available
    assert any("be placed on the map" in w for w in r.warnings)


def test_geolocated_population_beats_ungeolocated_count() -> None:
    # If even one census row is geolocated, status is OK (not the ungeolocated state).
    ev = _evidence(_census_hit("A", "C1", 4000, 100.0), ungeolocated=5)
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.OK
    assert r.catchment.persons == 4000
    assert r.coverage.population_records_ungeolocated_in_area == 5
    assert r.coverage.population_available_not_geolocated is False


def test_activity_only_survives_missing_population() -> None:
    ev = _evidence(
        _osm_hit("A", 100.0),
        activity=(_activity(ActivityKind.SCHOOL, 900.0), _activity(ActivityKind.BANK, 1200.0)),
        covers_area=False,
    )
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.NO_POPULATION_DATA
    assert r.activity.total_points == 2
    assert r.activity.counts_by_kind[ActivityKind.SCHOOL] == 1
    assert r.activity.nearest_m_by_kind[ActivityKind.SCHOOL] == 900.0


def test_activity_counts_and_nearest() -> None:
    ev = _evidence(
        _census_hit("A", "C1", 4000, 100.0),
        activity=(
            _activity(ActivityKind.SCHOOL, 1500.0),
            _activity(ActivityKind.SCHOOL, 400.0),
            _activity(ActivityKind.TRANSPORT_STOP, 800.0),
        ),
    )
    r = compute_demand_signals(ev)
    assert r.activity.counts_by_kind[ActivityKind.SCHOOL] == 2
    assert r.activity.nearest_m_by_kind[ActivityKind.SCHOOL] == 400.0
    assert r.activity.total_points == 3


# -- degradation ----------------------------------------------------


def test_location_unresolved() -> None:
    ev = DemandEvidence(location_text="?", acquired_at=_NOW)
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.LOCATION_UNRESOLVED


def test_invalid_radius() -> None:
    ev = _evidence(_census_hit("A", "C1", 1000, 10.0), radius_m=0)
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.INVALID_RADIUS
    assert r.catchment.persons is None


def test_source_unavailable_when_osm_failed_and_census_absent() -> None:
    ev = _evidence(file_present=False, errors=("Overpass down",))
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.SOURCE_UNAVAILABLE


def test_osm_down_but_census_present_still_yields_population() -> None:
    ev = _evidence(
        _census_hit("A", "C1", 4200, 100.0),
        file_present=True,
        covers_area=True,
        errors=("Overpass down",),
    )
    r = compute_demand_signals(ev)
    assert r.status is DemandStatus.OK
    assert r.catchment.persons == 4200


# -- osm-tagged population quarantine ------------------------------


def test_osm_tagged_population_is_reported_separately_not_summed() -> None:
    ev = _evidence(
        _osm_hit("Big Town", 100.0, osm_pop=20000),
        _census_hit("Big Town", "C1", 5000, 120.0),
    )
    r = compute_demand_signals(ev)
    assert r.osm_tagged_population_total == 20000
    assert r.catchment.persons == 5000  # census only


# -- competitors per 1,000 ---------------------------------------


def _competition(direct: int, status: CompetitionMetricsStatus = CompetitionMetricsStatus.OK):
    return CompetitionMetricsResult(
        status=status,
        proposed_category="grocery",  # type: ignore[arg-type]
        analysis_radius_m=5_000,
        direct_count=direct,
    )


def test_competitors_per_1000_with_competition_result() -> None:
    ev = _evidence(_census_hit("A", "C1", 4000, 100.0))
    r = compute_demand_signals(ev, competition=_competition(6))
    assert r.competitors_per_1000_people is not None
    assert abs(r.competitors_per_1000_people - 1000 * 6 / 4000) < 1e-9


def test_competitors_per_1000_none_without_competition() -> None:
    ev = _evidence(_census_hit("A", "C1", 4000, 100.0))
    assert compute_demand_signals(ev).competitors_per_1000_people is None


def test_competitors_per_1000_none_when_population_unknown() -> None:
    ev = _evidence(_osm_hit("A", 100.0), covers_area=False)
    r = compute_demand_signals(ev, competition=_competition(6))
    assert r.competitors_per_1000_people is None


# -- confidence & determinism ----------------------------------


def test_confidence_is_deterministic_regardless_of_wall_clock() -> None:
    # reference_year comes from config, never datetime.now()
    ev = _evidence(_census_hit("A", "C1", 4000, 100.0))
    cfg_2026 = DemandConfig(reference_year=2026)
    cfg_2040 = DemandConfig(reference_year=2040)
    c26 = compute_demand_signals(ev, config=cfg_2026).demand_data_confidence
    c40 = compute_demand_signals(ev, config=cfg_2040).demand_data_confidence
    assert c26 > c40  # older data -> lower freshness -> lower confidence
    # and stable across repeated calls
    assert (
        compute_demand_signals(ev, config=cfg_2026).model_dump()
        == compute_demand_signals(ev, config=cfg_2026).model_dump()
    )


def test_geography_outside_extract_zeroes_population_confidence() -> None:
    ev = _evidence(_census_hit("A", "C1", 4000, 100.0), covers_area=False)
    r = compute_demand_signals(ev)
    assert r.demand_data_confidence_basis["geography_in_curated_extract"] == 0.0
    assert r.coverage.population_confidence == 0.0


def test_confidence_is_not_demand_and_no_score_field_exists() -> None:
    ev = _evidence(_census_hit("A", "C1", 40, 100.0))  # tiny population, good data
    r = compute_demand_signals(ev)
    assert "viability" in r.demand_data_confidence_note.lower()
    dumped = r.model_dump()
    for banned in ("demand_score", "score", "viability", "recommendation", "verdict"):
        assert banned not in dumped
    # small population with observable data is a valid, informative result
    assert r.catchment.persons == 40
    assert r.demand_data_confidence > 0.0


def test_result_is_json_serializable_with_config_and_guidance() -> None:
    ev = _evidence(
        _census_hit("A", "C1", 4000, 100.0), activity=(_activity(ActivityKind.BANK, 50.0),)
    )
    payload = json.loads(compute_demand_signals(ev).model_dump_json())
    assert payload["catchment"]["unit"] == "persons"
    assert payload["config"]["reference_year"] == 2026
    assert payload["interpretation_guidance"]
    assert payload["status"] == "ok"
