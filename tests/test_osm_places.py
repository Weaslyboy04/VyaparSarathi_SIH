"""OSM demand fetch + settlement/activity normalization (Phase 2C). Offline."""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import SourceUnavailableError
from vyaparsarathi.models.demand import ActivityKind, SettlementType
from vyaparsarathi.normalization.settlement import normalize_activity, normalize_settlement
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.sources.osm.parse import parse_element
from vyaparsarathi.sources.osm.places import fetch_demand_elements

from .conftest import FROZEN_NOW, load_fixture

PRIMARY = "https://overpass.test/api/interpreter"
MIRROR = "https://overpass-mirror.test/api/interpreter"
SELECTORS = [("place", "village"), ("place", "town"), ("amenity", "school")]


def _client(settings: Settings) -> OverpassClient:
    return OverpassClient(settings, sleep=lambda _: None)


@respx.mock
def test_fetch_parses_and_drops_coordinateless(settings: Settings) -> None:
    respx.post(PRIMARY).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "demand_hajipur.json"))
    )
    with _client(settings) as client:
        fetch = fetch_demand_elements(
            client,
            latitude=25.685,
            longitude=85.21,
            radius_m=8_000,
            selectors=SELECTORS,
            timeout_s=30,
        )
    assert fetch.raw_count == 10
    assert fetch.dropped_no_coordinates == 1  # the ghost node
    assert fetch.endpoint_used == PRIMARY
    assert len(fetch.elements) == 9


@respx.mock
def test_mirror_fallback_and_retry(settings: Settings) -> None:
    respx.post(PRIMARY).mock(side_effect=httpx.ConnectTimeout("down"))
    respx.post(MIRROR).mock(
        return_value=httpx.Response(200, json=load_fixture("osm", "demand_hajipur.json"))
    )
    with _client(settings) as client:
        fetch = fetch_demand_elements(
            client,
            latitude=25.685,
            longitude=85.21,
            radius_m=8_000,
            selectors=SELECTORS,
            timeout_s=30,
        )
    assert fetch.mirror_fallback_used is True
    assert fetch.endpoint_used == MIRROR


@respx.mock
def test_all_endpoints_bad_payload_raises(settings: Settings) -> None:
    respx.post(PRIMARY).mock(return_value=httpx.Response(200, text="not json"))
    respx.post(MIRROR).mock(return_value=httpx.Response(200, text="not json"))
    with _client(settings) as client, pytest.raises(SourceUnavailableError):
        fetch_demand_elements(
            client,
            latitude=25.685,
            longitude=85.21,
            radius_m=8_000,
            selectors=SELECTORS,
            timeout_s=30,
        )


def _element(raw: dict):
    el = parse_element(raw)
    assert el is not None
    return el


def test_normalize_settlement_maps_place_and_population_tag() -> None:
    fixture = load_fixture("osm", "demand_hajipur.json")
    by_id = {e["id"]: e for e in fixture["elements"]}  # type: ignore[index]

    town = normalize_settlement(_element(by_id[500001]), now=FROZEN_NOW)
    assert town is not None
    assert town.settlement.place_type is SettlementType.TOWN
    assert town.settlement.name == "Hajipur"
    assert town.settlement.osm_tagged_population == 147688  # read but quarantined
    assert town.settlement.census_code is None
    assert town.settlement.provenance[0].retrieved_at == FROZEN_NOW

    hamlet = normalize_settlement(_element(by_id[500003]), now=FROZEN_NOW)
    assert hamlet is not None
    assert hamlet.settlement.place_type is SettlementType.HAMLET


def test_non_habitation_place_is_not_a_settlement() -> None:
    fixture = load_fixture("osm", "demand_hajipur.json")
    by_id = {e["id"]: e for e in fixture["elements"]}  # type: ignore[index]
    assert normalize_settlement(_element(by_id[500009]), now=FROZEN_NOW) is None  # place=locality


def test_normalize_activity_kinds() -> None:
    fixture = load_fixture("osm", "demand_hajipur.json")
    by_id = {e["id"]: e for e in fixture["elements"]}  # type: ignore[index]

    assert normalize_activity(_element(by_id[500005])).kind is ActivityKind.SCHOOL  # type: ignore[union-attr]
    assert normalize_activity(_element(by_id[500006])).kind is ActivityKind.BANK  # type: ignore[union-attr]
    assert normalize_activity(_element(by_id[500007])).kind is ActivityKind.MARKETPLACE  # type: ignore[union-attr]
    assert (
        normalize_activity(_element(by_id[500008])).kind is ActivityKind.TRANSPORT_STOP  # type: ignore[union-attr]
    )
    assert normalize_activity(_element(by_id[500001])) is None  # a town is not an activity
