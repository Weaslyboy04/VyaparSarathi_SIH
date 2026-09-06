"""Phase 2C acquisition wiring + degradation. No HTTP: a fake Overpass client and
the fixture census CSV."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.discovery.demand_acquisition import acquire_demand_evidence
from vyaparsarathi.errors import SourceUnavailableError
from vyaparsarathi.models.demand import ActivityKind
from vyaparsarathi.models.place import ResolvedPlace
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.sources.census.loader import CensusVillageSource

from .conftest import FIXTURES, load_fixture

_CSV = FIXTURES / "census" / "demo_villages.csv"
_CLOCK = datetime(2026, 6, 1, tzinfo=UTC)


class FakeClient:
    """Stands in for OverpassClient — only ``run`` is used by fetch_demand_elements."""

    def __init__(
        self,
        elements: list[dict],
        *,
        endpoint: str = "https://overpass.test",
        fallback: bool = False,
    ):
        self._elements = elements
        self._endpoint = endpoint
        self._fallback = fallback
        self.calls = 0

    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        self.calls += 1
        return self._elements, self._endpoint, self._fallback


class RaisingClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        raise SourceUnavailableError("all Overpass endpoints failed")


def _discovery(*, with_query: bool = True, state: str | None = "Testland") -> DiscoveryResult:
    resolved = (
        ResolvedPlace(
            query="Testville",
            display_name="Testville, Testland",
            latitude=25.7000,
            longitude=85.2300,
            state=state,
            district="Test District",
        )
        if state is not None
        else None
    )
    return DiscoveryResult(
        status=DiscoveryStatus.OK,
        query_text="Testville, Testland",
        category=BusinessCategory.GROCERY,
        requested_radius_m=5_000,
        query=(
            DiscoveryQuery(
                location_text="Testville",
                latitude=25.7000,
                longitude=85.2300,
                radius_m=5_000,
                category=BusinessCategory.GROCERY,
            )
            if with_query
            else None
        ),
        resolved_place=resolved,
    )


def _osm_elements() -> list[dict]:
    return load_fixture("osm", "demand_hajipur.json")["elements"]  # type: ignore[index,return-value]


def test_full_wiring_settlements_activity_and_census() -> None:
    client = FakeClient(_osm_elements())
    census = CensusVillageSource(path=_CSV)
    ev = acquire_demand_evidence(_discovery(), client=client, census=census, clock=lambda: _CLOCK)
    assert ev.acquired_at == _CLOCK
    # OSM settlements within 5 km (Hajipur, Jadua, Rampur Nevada) + census rows
    osm_names = {h.settlement.name for h in ev.settlements if h.settlement.census_code is None}
    assert "Hajipur" in osm_names
    assert "Far Away Gaon" not in osm_names  # ~35 km away, filtered by radius
    census_codes = {h.settlement.census_code for h in ev.settlements if h.settlement.census_code}
    assert "PC11-TEST-0001" in census_codes
    kinds = {h.activity_point.kind for h in ev.activity_points}
    assert kinds == {
        ActivityKind.SCHOOL,
        ActivityKind.BANK,
        ActivityKind.MARKETPLACE,
        ActivityKind.TRANSPORT_STOP,
    }
    assert ev.acquisition.census_extract_covers_query_area is True
    assert ev.acquisition.errors == []
    # settlements come back sorted nearest-first
    dists = [h.distance_m for h in ev.settlements]
    assert dists == sorted(dists)


def test_census_row_retrieved_at_uses_injected_clock() -> None:
    ev = acquire_demand_evidence(
        _discovery(),
        client=FakeClient(_osm_elements()),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    census_hit = next(h for h in ev.settlements if h.settlement.census_code == "PC11-TEST-0001")
    assert census_hit.settlement.population is not None
    assert census_hit.settlement.population.provenance.retrieved_at == _CLOCK


def test_osm_failure_with_census_present_is_recorded_not_raised() -> None:
    ev = acquire_demand_evidence(
        _discovery(),
        client=RaisingClient(),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.acquisition.errors  # OSM failure captured as data
    assert any("unavailable" in w.lower() for w in ev.warnings)
    # census still produced settlements with population
    assert any(h.settlement.population is not None for h in ev.settlements)


def test_population_present_but_ungeolocated_is_recorded_and_warned() -> None:
    # A Testland point far from every geolocated fixture row, but Testland still
    # has ambiguous / unmatched population rows -> available-but-not-geo-matchable.
    discovery = _discovery()
    discovery.query.latitude = 25.2000  # type: ignore[union-attr]
    discovery.query.longitude = 84.5000  # type: ignore[union-attr]
    discovery.resolved_place.latitude = 25.2000  # type: ignore[union-attr]
    discovery.resolved_place.longitude = 84.5000  # type: ignore[union-attr]
    ev = acquire_demand_evidence(
        discovery,
        client=FakeClient([]),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert not any(h.settlement.census_code for h in ev.settlements)
    assert ev.acquisition.census_population_ungeolocated_in_area == 2
    assert ev.acquisition.census_extract_covers_query_area is True
    assert any("be placed on the map" in w for w in ev.warnings)


def test_missing_census_file_is_a_warning_not_a_crash(tmp_path) -> None:
    ev = acquire_demand_evidence(
        _discovery(),
        client=FakeClient(_osm_elements()),
        census=CensusVillageSource(path=tmp_path / "absent.csv.gz"),
        clock=lambda: _CLOCK,
    )
    assert ev.acquisition.census_file_present is False
    assert any("not present" in w for w in ev.warnings)
    assert all(h.settlement.census_code is None for h in ev.settlements)  # OSM only


def test_location_unresolved_returns_empty_evidence() -> None:
    discovery = _discovery(with_query=False, state=None)
    ev = acquire_demand_evidence(
        discovery, client=FakeClient(_osm_elements()), census=CensusVillageSource(path=_CSV)
    )
    assert ev.settlements == []
    assert ev.latitude is None
    assert any("did not resolve" in w for w in ev.warnings)


def test_query_outside_extract_coverage_warns() -> None:
    discovery = DiscoveryResult(
        status=DiscoveryStatus.OK,
        query_text="Elsewhere",
        category=BusinessCategory.GROCERY,
        requested_radius_m=5_000,
        query=DiscoveryQuery(
            location_text="Elsewhere",
            latitude=19.0,
            longitude=73.0,
            radius_m=5_000,
            category=BusinessCategory.GROCERY,
        ),
        resolved_place=ResolvedPlace(
            query="Elsewhere",
            display_name="Elsewhere",
            latitude=19.0,
            longitude=73.0,
            state="Maharashtra",
        ),
    )
    ev = acquire_demand_evidence(
        discovery,
        client=FakeClient([]),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.acquisition.census_extract_covers_query_area is False
    assert any("outside the Census 2011 extract" in w for w in ev.warnings)


def test_all_sources_dead_is_not_raised_here() -> None:
    # acquisition never raises; the engine decides SOURCE_UNAVAILABLE.
    ev = acquire_demand_evidence(
        _discovery(),
        client=RaisingClient(),
        census=CensusVillageSource(path="does/not/exist.csv.gz"),
        clock=lambda: _CLOCK,
    )
    assert ev.acquisition.errors
    assert ev.acquisition.census_file_present is False
    assert ev.settlements == []
    # sanity: it is a plain object, not an exception
    assert isinstance(ev.warnings, list)


def test_acquisition_makes_exactly_one_overpass_call() -> None:
    client = FakeClient(_osm_elements())
    acquire_demand_evidence(
        _discovery(), client=client, census=CensusVillageSource(path=_CSV), clock=lambda: _CLOCK
    )
    assert client.calls == 1  # one union query, not one per tag


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
