"""End-to-end Phase 1 pipeline orchestration (CLAUDE.md §26.1, §26.2)."""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.config import Settings
from vyaparsarathi.database import InMemoryBusinessRepository
from vyaparsarathi.discovery import DiscoveryService
from vyaparsarathi.errors import GeocodingError, SourceUnavailableError
from vyaparsarathi.geocoding import NominatimGeocoder
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.results import DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.sources.osm import OverpassSource
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.sources.osm.models import RawOsmElement

from .conftest import load_fixture

PRIMARY = "https://overpass.test/api/interpreter"
SEARCH = "https://nominatim.test/search"


# --- fakes ---------------------------------------------------------------


class FakeGeocoder:
    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return list(self._candidates)


class RaisingGeocoder:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        raise self._exc


class FakeSource:
    name = SourceName.OSM

    def __init__(self, fetch: OverpassFetch | None = None, exc: Exception | None = None) -> None:
        self._fetch = fetch
        self._exc = exc
        self.called = False

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        self.called = True
        if self._exc is not None:
            raise self._exc
        assert self._fetch is not None
        return self._fetch


# --- helpers ----------------------------------------------------------


def _cand(
    lat: float, lon: float, name: str = "Bhagwanpur", importance: float = 0.4
) -> PlaceCandidate:
    return PlaceCandidate(
        display_name=name,
        latitude=lat,
        longitude=lon,
        importance=importance,
        district="Vaishali",
        state="Bihar",
        country="India",
    )


def _el(eid: int, lat: float | None, lon: float | None, **tags: str) -> RawOsmElement:
    return RawOsmElement(
        element_type="node",
        element_id=eid,
        latitude=lat,
        longitude=lon,
        tags=tags,
        raw={"type": "node", "id": eid, "tags": tags},
    )


def _fetch(elements: list[RawOsmElement], raw_count: int | None = None) -> OverpassFetch:
    return OverpassFetch(
        elements=elements,
        raw_count=raw_count if raw_count is not None else len(elements),
        dropped_no_coordinates=0,
        endpoint_used=PRIMARY,
        mirror_fallback_used=False,
        query_ql="[out:json];",
    )


def _service(
    geocoder: object, source: object, repo: InMemoryBusinessRepository | None = None
) -> DiscoveryService:
    return DiscoveryService(
        geocoder=geocoder,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        repository=repo or InMemoryBusinessRepository(),
        settings=Settings(cache_enabled=False),
    )


# --- tests ----------------------------------------------------------


def test_happy_path_normalizes_dedupes_persists() -> None:
    geocoder = FakeGeocoder([_cand(25.7500, 84.5500)])
    elements = [
        _el(1, 25.75000, 84.55000, shop="convenience", name="Sharma Kirana Store"),
        _el(2, 25.75030, 84.55020, shop="general", name="Sharma Kirana"),  # ~35 m dup
        _el(3, 25.76000, 84.56000, shop="supermarket", name="Bhagwanpur Bazaar"),
    ]
    repo = InMemoryBusinessRepository()
    result = _service(geocoder, FakeSource(_fetch(elements)), repo).discover(
        "Bhagwanpur, Bihar", BusinessCategory.GROCERY, 8000
    )

    assert result.status is DiscoveryStatus.OK
    assert result.resolved_place is not None
    assert result.coverage.total_before_dedup == 3
    assert result.coverage.total_after_dedup == 2
    assert result.coverage.duplicates_merged == 1
    assert [h.business.name for h in result.businesses] == [
        "Sharma Kirana Store",
        "Bhagwanpur Bazaar",
    ]
    assert result.businesses[0].distance_m < result.businesses[1].distance_m
    assert 0.0 < result.confidence <= 0.75
    assert repo.count() == 2
    assert any("OpenStreetMap coverage" in w for w in result.warnings)


def test_location_ambiguous_short_circuits_before_fetch() -> None:
    geocoder = FakeGeocoder([_cand(25.75, 84.55, "Vaishali"), _cand(25.21, 84.98, "Buxar")])
    source = FakeSource(_fetch([]))
    result = _service(geocoder, source).discover(
        "Bhagwanpur, Bihar", BusinessCategory.GROCERY, 8000
    )

    assert result.status is DiscoveryStatus.LOCATION_AMBIGUOUS
    assert len(result.candidates) == 2
    assert result.businesses == []
    assert source.called is False


def test_location_not_found_when_no_candidates() -> None:
    result = _service(FakeGeocoder([]), FakeSource(_fetch([]))).discover(
        "Nowhere", BusinessCategory.GROCERY, 8000
    )
    assert result.status is DiscoveryStatus.LOCATION_NOT_FOUND


def test_geocoder_error_is_degraded_not_raised() -> None:
    result = _service(RaisingGeocoder(GeocodingError("down")), FakeSource(_fetch([]))).discover(
        "Bhagwanpur", BusinessCategory.GROCERY, 8000
    )
    assert result.status is DiscoveryStatus.LOCATION_NOT_FOUND
    assert any("Could not resolve" in w for w in result.warnings)


def test_source_unavailable_is_degraded_not_raised() -> None:
    geocoder = FakeGeocoder([_cand(25.75, 84.55)])
    result = _service(
        geocoder, FakeSource(exc=SourceUnavailableError("all mirrors down"))
    ).discover("Bhagwanpur", BusinessCategory.GROCERY, 8000)
    assert result.status is DiscoveryStatus.SOURCE_UNAVAILABLE
    assert result.resolved_place is not None
    assert result.confidence == 0.0


def test_no_results_status_when_source_empty() -> None:
    geocoder = FakeGeocoder([_cand(25.75, 84.55)])
    result = _service(geocoder, FakeSource(_fetch([]))).discover(
        "Bhagwanpur", BusinessCategory.GROCERY, 8000
    )
    assert result.status is DiscoveryStatus.NO_RESULTS
    assert result.confidence == 0.0
    assert any("No matching businesses" in w for w in result.warnings)


def test_unmapped_tags_are_counted_and_kept_as_unknown() -> None:
    geocoder = FakeGeocoder([_cand(25.75, 84.55)])
    elements = [
        _el(1, 25.7501, 84.5501, shop="convenience", name="Normal Kirana"),
        _el(2, 25.7502, 84.5502, shop="e-cigarette", name="Vape Point"),
    ]
    result = _service(geocoder, FakeSource(_fetch(elements))).discover(
        "Bhagwanpur", BusinessCategory.GROCERY, 8000
    )
    cov = result.coverage.per_source[0]
    assert cov.unmapped_tags == {"shop=e-cigarette": 1}
    categories = {h.business.category for h in result.businesses}
    assert BusinessCategory.UNKNOWN in categories
    assert any("could not be categorised" in w for w in result.warnings)


def test_distance_filter_drops_business_outside_radius() -> None:
    geocoder = FakeGeocoder([_cand(25.7500, 84.5500)])
    elements = [
        _el(1, 25.7505, 84.5505, shop="convenience", name="Inside"),
        _el(2, 25.9000, 84.5500, shop="convenience", name="WayOutside"),  # ~17 km north
    ]
    result = _service(geocoder, FakeSource(_fetch(elements))).discover(
        "Bhagwanpur", BusinessCategory.GROCERY, 8000
    )
    assert [h.business.name for h in result.businesses] == ["Inside"]


def test_persistence_failure_is_warned_not_fatal() -> None:
    class BrokenRepo(InMemoryBusinessRepository):
        def save_businesses(self, businesses: list) -> None:  # type: ignore[override]
            raise RuntimeError("disk on fire")

    geocoder = FakeGeocoder([_cand(25.75, 84.55)])
    elements = [_el(1, 25.7501, 84.5501, shop="convenience", name="Kirana")]
    result = _service(geocoder, FakeSource(_fetch(elements)), BrokenRepo()).discover(
        "Bhagwanpur", BusinessCategory.GROCERY, 8000
    )
    assert result.status is DiscoveryStatus.OK
    assert any("were not persisted" in w for w in result.warnings)


@pytest.mark.parametrize("radius", [0, -5, 25_001])
def test_bad_radius_raises_value_error(radius: int) -> None:
    svc = _service(FakeGeocoder([_cand(25.75, 84.55)]), FakeSource(_fetch([])))
    with pytest.raises(ValueError):
        svc.discover("Bhagwanpur", BusinessCategory.GROCERY, radius)


def test_category_without_osm_selectors_reports_no_results() -> None:
    geocoder = FakeGeocoder([_cand(25.75, 84.55)])
    source = FakeSource(_fetch([]))
    result = _service(geocoder, source).discover("Bhagwanpur", BusinessCategory.UNKNOWN, 8000)
    assert result.status is DiscoveryStatus.NO_RESULTS
    assert source.called is False
    assert any("no OSM tag mapping" in w for w in result.warnings)


@respx.mock
def test_full_stack_integration_with_mocked_http(settings: Settings) -> None:
    respx.get(SEARCH).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "25.7500",
                    "lon": "84.5500",
                    "display_name": "Bhagwanpur, Vaishali, Bihar, India",
                    "importance": 0.42,
                    "address": {"village": "Bhagwanpur", "state": "Bihar", "country": "India"},
                }
            ],
        )
    )
    respx.post(PRIMARY).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "grocery_bhagwanpur.json"))
    )
    repo = InMemoryBusinessRepository()
    with (
        NominatimGeocoder(settings, sleep=lambda _: None) as geo,
        OverpassSource(settings, client=OverpassClient(settings, sleep=lambda _: None)) as source,
    ):
        service = DiscoveryService(geo, source, repo, settings)
        result = service.discover("Bhagwanpur, Bihar", BusinessCategory.GROCERY, 8000)

    assert result.status is DiscoveryStatus.OK
    assert result.resolved_place is not None
    assert result.resolved_place.state == "Bihar"
    # fixture has 8 raw, 1 no-coord dropped, 2 "Sharma Kirana" nodes merge -> 6 businesses
    assert result.coverage.total_before_dedup == 7
    assert result.coverage.duplicates_merged == 1
    assert len(result.businesses) == 6
    assert repo.count() == 6
    assert result.businesses == sorted(result.businesses, key=lambda h: h.distance_m)
