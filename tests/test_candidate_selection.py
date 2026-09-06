"""Explicit geocoding-candidate selection: `--candidate N` (Phase 1 usability)."""

from __future__ import annotations

import pytest
import scripts.discover_businesses as cli

from vyaparsarathi.config import Settings
from vyaparsarathi.database import InMemoryBusinessRepository
from vyaparsarathi.discovery import DiscoveryService
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.models import RawOsmElement

# Two genuinely different "Sangareddy" places, ~28 km apart -> ambiguous.
CAND_1 = {
    "display_name": "Sangareddy, Telangana, India",
    "latitude": 17.868434,
    "longitude": 77.822719,
}
CAND_2 = {
    "display_name": "Sangareddy mandal, Sangareddy, Telangana, India",
    "latitude": 17.615515,
    "longitude": 78.081722,
}


class CountingGeocoder:
    """Returns a fixed candidate list and counts how often it is queried."""

    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates
        self.calls = 0

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        self.calls += 1
        return list(self._candidates)


class RecordingSource:
    """Captures the DiscoveryQuery it is handed and returns one OSM element."""

    def __init__(self) -> None:
        self.seen_query: DiscoveryQuery | None = None

    def fetch(self, query: DiscoveryQuery, selectors: list[tuple[str, str]]) -> OverpassFetch:
        self.seen_query = query
        element = RawOsmElement(
            element_type="node",
            element_id=1,
            latitude=query.latitude + 0.001,
            longitude=query.longitude + 0.001,
            tags={"shop": "convenience", "name": "Test Kirana"},
            raw={"type": "node", "id": 1},
        )
        return OverpassFetch(
            elements=[element],
            raw_count=1,
            dropped_no_coordinates=0,
            endpoint_used="https://overpass.test/api/interpreter",
            mirror_fallback_used=False,
            query_ql="[out:json];",
        )


def _candidates(ranked_high_first: bool = True) -> list[PlaceCandidate]:
    # CAND_1 has higher importance so it ranks first (position [1] in the listing).
    a = PlaceCandidate(**CAND_1, importance=0.60, place_rank=16)
    b = PlaceCandidate(**CAND_2, importance=0.40, place_rank=14)
    return [a, b] if ranked_high_first else [b, a]


def _service(geocoder: object, source: object) -> DiscoveryService:
    return DiscoveryService(
        geocoder=geocoder,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        repository=InMemoryBusinessRepository(),
        settings=Settings(cache_enabled=False),
    )


# -- behaviour ----------------------------------------------------------------


def test_ambiguous_location_without_candidate_still_lists_candidates() -> None:
    geo = CountingGeocoder(_candidates())
    src = RecordingSource()
    result = _service(geo, src).discover("Sangareddy, Telangana", BusinessCategory.GROCERY, 8000)

    assert result.status is DiscoveryStatus.LOCATION_AMBIGUOUS
    assert [c.display_name for c in result.candidates] == [
        CAND_1["display_name"],
        CAND_2["display_name"],
    ]
    assert geo.calls == 1
    assert src.seen_query is None  # discovery never started


def test_candidate_1_uses_first_ranked_candidate_coordinates() -> None:
    geo = CountingGeocoder(_candidates())
    src = RecordingSource()
    result = _service(geo, src).discover(
        "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=1
    )

    assert result.status is DiscoveryStatus.OK
    assert result.resolved_place is not None
    assert result.resolved_place.display_name == CAND_1["display_name"]
    assert result.query is not None
    assert (result.query.latitude, result.query.longitude) == (
        CAND_1["latitude"],
        CAND_1["longitude"],
    )
    assert any("selected explicitly" in w for w in result.warnings)


def test_candidate_2_uses_last_valid_candidate() -> None:
    geo = CountingGeocoder(_candidates())
    result = _service(geo, RecordingSource()).discover(
        "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=2
    )
    assert result.status is DiscoveryStatus.OK
    assert result.resolved_place is not None
    assert result.resolved_place.display_name == CAND_2["display_name"]
    assert (result.query.latitude, result.query.longitude) == (
        CAND_2["latitude"],
        CAND_2["longitude"],
    )


def test_candidate_ordering_matches_the_ambiguous_listing_not_raw_geocoder_order() -> None:
    # geocoder returns them low-importance-first; --candidate 1 must still be the
    # high-importance one, i.e. position [1] in the ambiguous listing.
    geo = CountingGeocoder(_candidates(ranked_high_first=False))
    result = _service(geo, RecordingSource()).discover(
        "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=1
    )
    assert result.resolved_place is not None
    assert result.resolved_place.display_name == CAND_1["display_name"]


@pytest.mark.parametrize("bad", [0, -1, 3, 99])
def test_invalid_candidate_number_raises_clear_value_error(bad: int) -> None:
    geo = CountingGeocoder(_candidates())
    with pytest.raises(ValueError, match=r"candidate .* out of range"):
        _service(geo, RecordingSource()).discover(
            "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=bad
        )
    # it still geocoded exactly once (to learn how many candidates exist)
    assert geo.calls == 1


def test_candidate_selection_never_silently_falls_back_to_1() -> None:
    geo = CountingGeocoder(_candidates())
    with pytest.raises(ValueError):
        _service(geo, RecordingSource()).discover(
            "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=5
        )


def test_candidate_selection_does_not_geocode_twice() -> None:
    geo = CountingGeocoder(_candidates())
    _service(geo, RecordingSource()).discover(
        "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=1
    )
    assert geo.calls == 1


def test_selected_candidate_reaches_business_discovery() -> None:
    geo = CountingGeocoder(_candidates())
    src = RecordingSource()
    result = _service(geo, src).discover(
        "Sangareddy, Telangana", BusinessCategory.GROCERY, 8000, candidate=1
    )

    # Overpass adapter was called with the chosen candidate's coordinates ...
    assert src.seen_query is not None
    assert (src.seen_query.latitude, src.seen_query.longitude) == (
        CAND_1["latitude"],
        CAND_1["longitude"],
    )
    # ... and a normalized business flowed all the way through to the result.
    assert result.status is DiscoveryStatus.OK
    assert [h.business.name for h in result.businesses] == ["Test Kirana"]
    assert result.businesses[0].business.category is BusinessCategory.GROCERY


def test_candidate_on_an_unambiguous_location_is_harmless() -> None:
    geo = CountingGeocoder([PlaceCandidate(**CAND_1, importance=0.6)])
    result = _service(geo, RecordingSource()).discover(
        "Somewhere Specific", BusinessCategory.GROCERY, 8000, candidate=1
    )
    assert result.status is DiscoveryStatus.OK
    assert result.resolved_place is not None
    assert result.resolved_place.display_name == CAND_1["display_name"]
    assert not any("selected explicitly" in w for w in result.warnings)  # no ambiguity to note


# -- CLI wiring -------------------------------------------------------------


def test_cli_forwards_candidate_to_discover(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    class StubService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def discover(
            self,
            location_text: str,
            category: BusinessCategory,
            radius_m: int,
            *,
            candidate: int | None = None,
        ) -> DiscoveryResult:
            seen["candidate"] = candidate
            seen["radius_m"] = radius_m
            return DiscoveryResult(
                status=DiscoveryStatus.NO_RESULTS,
                query_text=location_text,
                category=category,
                requested_radius_m=radius_m,
            )

    monkeypatch.setattr(cli, "DiscoveryService", StubService)

    rc = cli.main(
        [
            "--location",
            "Sangareddy, Telangana",
            "--candidate",
            "1",
            "--category",
            "grocery",
            "--radius",
            "8",
        ]
    )
    assert seen["candidate"] == 1
    assert seen["radius_m"] == 8000
    assert rc == 1  # NO_RESULTS -> non-zero, but not the '2' of a bad argument


def test_cli_rejects_bad_candidate_with_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    class StubService:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def discover(self, *args: object, candidate: int | None = None, **kwargs: object) -> object:
            raise ValueError(f"candidate {candidate} is out of range: 2 candidate(s) available")

    monkeypatch.setattr(cli, "DiscoveryService", StubService)

    rc = cli.main(
        [
            "--location",
            "Sangareddy, Telangana",
            "--candidate",
            "9",
            "--category",
            "grocery",
            "--radius",
            "8",
        ]
    )
    assert rc == 2
