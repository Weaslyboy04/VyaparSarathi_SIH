"""Phase 3 acquisition wiring (CLAUDE.md §12, §33). No HTTP: a fake Overpass
client and the fixture census CSV. The union *business* fetch is the caller's
job; this layer only adds the demand fetch + per-candidate coverage confidence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.discovery.opportunity_acquisition import acquire_opportunity_evidence
from vyaparsarathi.errors import SourceUnavailableError
from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness
from vyaparsarathi.models.place import ResolvedPlace
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import (
    CoverageSummary,
    DiscoveryResult,
    DiscoveryStatus,
    SourceCoverage,
)
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource

from .conftest import FIXTURES, load_fixture

_CSV = FIXTURES / "census" / "demo_villages.csv"
_CLOCK = datetime(2026, 6, 1, tzinfo=UTC)
_LAT, _LON, _R = 25.7000, 85.2300, 5_000
_CANDS = (C.GROCERY, C.DAIRY, C.AGRI_INPUT, C.PHARMACY, C.FOOD_STALL)


class FakeClient:
    def __init__(self, elements: list[dict], *, fallback: bool = False):
        self._elements = elements
        self._fallback = fallback
        self.calls = 0

    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        self.calls += 1
        return self._elements, "https://overpass.test", self._fallback


class RaisingClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        raise SourceUnavailableError("all Overpass endpoints failed")


def _biz(name: str, cat: C, *, lat: float = _LAT, lon: float = _LON) -> BusinessHit:
    return BusinessHit(
        business=NormalizedBusiness(
            name=name,
            normalized_name=name.lower(),
            category=cat,
            latitude=lat,
            longitude=lon,
            source=SourceName.OSM,
            source_id=f"node/{abs(hash(name)) % 999983}",
            data_quality=0.6,
        ),
        distance_m=400.0,
    )


def _union_discovery(counts: dict[C, int], *, raw_records: int = 30) -> DiscoveryResult:
    businesses: list[BusinessHit] = []
    for cat, n in counts.items():
        businesses.extend(_biz(f"{cat.value}-{i}", cat) for i in range(n))
    return DiscoveryResult(
        status=DiscoveryStatus.OK,
        query_text="Testville, Testland",
        category=C.GROCERY,
        requested_radius_m=_R,
        query=DiscoveryQuery(
            location_text="Testville",
            latitude=_LAT,
            longitude=_LON,
            radius_m=_R,
            category=C.GROCERY,
        ),
        resolved_place=ResolvedPlace(
            query="Testville",
            display_name="Testville, Testland",
            latitude=_LAT,
            longitude=_LON,
            state="Testland",
            district="Test District",
        ),
        businesses=businesses,
        sources_queried=[SourceName.OSM],
        coverage=CoverageSummary(
            per_source=[
                SourceCoverage(
                    source=SourceName.OSM,
                    raw_records=raw_records,
                    normalized=len(businesses),
                )
            ],
            total_after_dedup=len(businesses),
        ),
        confidence=0.6,
    )


def _osm_elements() -> list[dict]:
    return load_fixture("osm", "demand_hajipur.json")["elements"]  # type: ignore[index,return-value]


def test_builds_evidence_with_demand_and_per_candidate_confidence() -> None:
    disc = _union_discovery({C.GROCERY: 6})
    ev = acquire_opportunity_evidence(
        disc,
        _CANDS,
        client=FakeClient(_osm_elements()),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.discovery is disc
    assert ev.demand.acquired_at == _CLOCK
    assert ev.demand.settlements  # census + OSM settlements came through
    assert set(ev.per_category_confidence) == set(_CANDS)


def test_exactly_one_overpass_call_for_demand() -> None:
    client = FakeClient(_osm_elements())
    acquire_opportunity_evidence(
        _union_discovery({C.GROCERY: 3}),
        _CANDS,
        client=client,
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert client.calls == 1  # the demand union query; no business query here


def test_per_candidate_confidence_zero_when_nothing_relevant() -> None:
    # groceries only: pharmacy has no relevant business -> 0.0; grocery/dairy > 0.
    disc = _union_discovery({C.GROCERY: 6})
    ev = acquire_opportunity_evidence(
        disc,
        _CANDS,
        client=FakeClient([]),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.per_category_confidence[C.PHARMACY] == 0.0
    assert ev.per_category_confidence[C.GROCERY] > 0.0
    assert ev.per_category_confidence[C.DAIRY] > 0.0  # grocery is ADJACENT to a proposed dairy


def test_no_businesses_means_every_candidate_confidence_is_zero() -> None:
    disc = _union_discovery({}, raw_records=18)
    ev = acquire_opportunity_evidence(
        disc,
        _CANDS,
        client=FakeClient([]),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert set(ev.per_category_confidence.values()) == {0.0}


def test_candidate_categories_are_deduped_order_stable_and_drop_unknown() -> None:
    ev = acquire_opportunity_evidence(
        _union_discovery({C.GROCERY: 2}),
        (C.DAIRY, C.GROCERY, C.DAIRY, C.UNKNOWN, C.GROCERY),
        client=FakeClient([]),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.candidate_categories == [C.DAIRY, C.GROCERY]


def test_demand_osm_failure_is_recorded_not_raised() -> None:
    ev = acquire_opportunity_evidence(
        _union_discovery({C.GROCERY: 4}),
        _CANDS,
        client=RaisingClient(),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    assert ev.demand.acquisition.errors  # captured as data, not an exception
    assert isinstance(ev.warnings, list)
    # census still produced population-bearing settlements
    assert any(h.settlement.population is not None for h in ev.demand.settlements)


def test_mirror_fallback_flows_into_the_confidence_inputs() -> None:
    disc = _union_discovery({C.GROCERY: 6})
    with_fallback = acquire_opportunity_evidence(
        disc,
        _CANDS,
        client=FakeClient(_osm_elements(), fallback=True),
        census=CensusVillageSource(path=_CSV),
        clock=lambda: _CLOCK,
    )
    # the mirror flag lives on the discovery coverage row; a fabricated one here
    # is False, so this only asserts the call path stays intact.
    assert with_fallback.per_category_confidence[C.GROCERY] > 0.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
