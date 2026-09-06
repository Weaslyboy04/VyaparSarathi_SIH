"""Overpass client + adapter (CLAUDE.md §6.1). All HTTP is mocked."""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import SourceUnavailableError
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.sources.osm.adapter import OverpassSource
from vyaparsarathi.sources.osm.client import OverpassClient, build_overpass_ql

from .conftest import load_fixture

PRIMARY = "https://overpass.test/api/interpreter"
MIRROR = "https://overpass-mirror.test/api/interpreter"
SELECTORS = [("shop", "convenience"), ("shop", "supermarket"), ("shop", "grocer")]


def _query() -> DiscoveryQuery:
    return DiscoveryQuery(
        location_text="Bhagwanpur",
        latitude=25.75,
        longitude=84.55,
        radius_m=8000,
        category=BusinessCategory.GROCERY,
    )


def _source(settings: Settings) -> OverpassSource:
    client = OverpassClient(settings, sleep=lambda _: None)
    return OverpassSource(settings, client=client)


# -- build_overpass_ql -------------------------------------------------------


def test_build_ql_structure() -> None:
    ql = build_overpass_ql(SELECTORS, 25.75, 84.55, 8000, 60)
    assert ql.startswith("[out:json][timeout:60];")
    assert ql.rstrip().endswith("out center tags;")
    assert 'nwr["shop"="convenience"](around:8000,25.7500000,84.5500000);' in ql
    assert ql.count("nwr[") == len(SELECTORS)


def test_build_ql_rejects_empty_selectors() -> None:
    with pytest.raises(ValueError):
        build_overpass_ql([], 25.75, 84.55, 8000, 60)


# -- fetch: parsing --------------------------------------------------------


@respx.mock
def test_fetch_parses_nodes_ways_relations(settings: Settings) -> None:
    respx.post(PRIMARY).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "grocery_bhagwanpur.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(_query(), SELECTORS)

    assert fetch.raw_count == 8
    assert fetch.dropped_no_coordinates == 1  # the ghost node with no lat/lon
    assert len(fetch.elements) == 7
    by_id = {e.source_id: e for e in fetch.elements}

    assert by_id["node/1001"].tags["name"] == "Sharma Kirana Store"
    # way / relation coordinates come from `center`
    assert by_id["way/2001"].latitude == pytest.approx(25.7450)
    assert by_id["relation/3001"].longitude == pytest.approx(84.5000)
    assert fetch.mirror_fallback_used is False
    assert fetch.endpoint_used == PRIMARY


@respx.mock
def test_fetch_empty_result(settings: Settings) -> None:
    respx.post(PRIMARY).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "empty.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(_query(), SELECTORS)
    assert fetch.raw_count == 0
    assert fetch.elements == []


# -- fetch: transient failure + fallback -----------------------------


@respx.mock
def test_retry_then_success_on_primary(settings: Settings) -> None:
    route = respx.post(PRIMARY).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=load_fixture("osm", "empty.json")),
        ]
    )
    with _source(settings) as source:
        fetch = source.fetch(_query(), SELECTORS)
    assert route.call_count == 2
    assert fetch.mirror_fallback_used is False


@respx.mock
def test_mirror_fallback_on_persistent_5xx(settings: Settings) -> None:
    respx.post(PRIMARY).mock(return_value=httpx.Response(504))
    respx.post(MIRROR).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "grocery_bhagwanpur.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(_query(), SELECTORS)
    assert fetch.mirror_fallback_used is True
    assert fetch.endpoint_used == MIRROR
    assert len(fetch.elements) == 7


@respx.mock
def test_mirror_fallback_on_timeout(settings: Settings) -> None:
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.post(MIRROR).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "empty.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(_query(), SELECTORS)
    assert fetch.mirror_fallback_used is True


@respx.mock
def test_all_endpoints_down_raises(settings: Settings) -> None:
    respx.post(PRIMARY).mock(return_value=httpx.Response(503))
    respx.post(MIRROR).mock(return_value=httpx.Response(503))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(_query(), SELECTORS)


@respx.mock
def test_malformed_json_is_treated_as_unavailable(settings: Settings) -> None:
    respx.post(PRIMARY).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    respx.post(MIRROR).mock(return_value=httpx.Response(200, text="still not json"))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(_query(), SELECTORS)


@respx.mock
def test_4xx_client_error_is_not_retried(settings: Settings) -> None:
    route = respx.post(PRIMARY).mock(return_value=httpx.Response(400))
    respx.post(MIRROR).mock(return_value=httpx.Response(400))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(_query(), SELECTORS)
    assert route.call_count == 1  # 400 -> straight to next endpoint, no retry
