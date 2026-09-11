"""Nominatim geocoder (CLAUDE.md §4.1). All HTTP is mocked."""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import GeocodingError, SourcePayloadError
from vyaparsarathi.geocoding.nominatim import NominatimGeocoder

from .conftest import load_fixture

SEARCH = "https://nominatim.test/search"


def _geocoder(settings: Settings) -> NominatimGeocoder:
    return NominatimGeocoder(settings, sleep=lambda _: None)


@respx.mock
def test_single_result_with_admin_hierarchy(settings: Settings) -> None:
    respx.get(SEARCH).mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "lat": "25.7500",
                    "lon": "84.5500",
                    "display_name": "Bhagwanpur, Vaishali, Bihar, India",
                    "importance": 0.42,
                    "place_rank": 19,
                    "osm_type": "node",
                    "osm_id": 1,
                    "address": {
                        "village": "Bhagwanpur",
                        "county": "Mahua",
                        "state_district": "Vaishali",
                        "state": "Bihar",
                        "country": "India",
                    },
                }
            ],
        )
    )
    with _geocoder(settings) as geo:
        candidates = geo.geocode("Bhagwanpur, Bihar")

    assert len(candidates) == 1
    c = candidates[0]
    assert (c.latitude, c.longitude) == (25.75, 84.55)
    assert c.district == "Vaishali"  # from state_district
    assert c.block == "Mahua"  # from county
    assert c.village == "Bhagwanpur"
    assert c.state == "Bihar"
    assert c.country == "India"
    assert c.raw  # original payload retained


@respx.mock
def test_multiple_candidates_returned(settings: Settings) -> None:
    respx.get(SEARCH).mock(
        return_value=httpx.Response(200, json=load_fixture("nominatim", "bhagwanpur_bihar.json"))
    )
    with _geocoder(settings) as geo:
        candidates = geo.geocode("Bhagwanpur, Bihar")
    assert len(candidates) == 2
    assert {c.district for c in candidates} == {"Vaishali", "Buxar"}


@respx.mock
def test_no_candidates(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=[]))
    with _geocoder(settings) as geo:
        assert geo.geocode("Nowhere At All") == []


@respx.mock
def test_entries_without_coordinates_are_skipped(settings: Settings) -> None:
    respx.get(SEARCH).mock(
        return_value=httpx.Response(200, json=[{"display_name": "No coords here", "address": {}}])
    )
    with _geocoder(settings) as geo:
        assert geo.geocode("weird") == []


@respx.mock
def test_malformed_body_raises_payload_error(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(200, text="<html>nope</html>"))
    with _geocoder(settings) as geo, pytest.raises(SourcePayloadError):
        geo.geocode("Bhagwanpur")


@respx.mock
def test_non_list_json_raises_payload_error(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(200, json={"error": "boom"}))
    with _geocoder(settings) as geo, pytest.raises(SourcePayloadError):
        geo.geocode("Bhagwanpur")


@respx.mock
def test_http_error_raises_geocoding_error(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(500))
    with _geocoder(settings) as geo, pytest.raises(GeocodingError):
        geo.geocode("Bhagwanpur")


@respx.mock
def test_http_404_raises_geocoding_error(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(404))
    with _geocoder(settings) as geo, pytest.raises(GeocodingError):
        geo.geocode("Bhagwanpur")


@respx.mock
def test_http_403_raises_geocoding_error_with_actionable_message(settings: Settings) -> None:
    respx.get(SEARCH).mock(return_value=httpx.Response(403))
    with _geocoder(settings) as geo:
        with pytest.raises(GeocodingError) as exc_info:
            geo.geocode("Bhagwanpur")
    message = str(exc_info.value)
    assert "403" in message
    assert "VYAPAR_USER_AGENT" in message


@respx.mock
def test_timeout_raises_geocoding_error(settings: Settings) -> None:
    respx.get(SEARCH).mock(side_effect=httpx.ConnectTimeout("slow"))
    with _geocoder(settings) as geo, pytest.raises(GeocodingError):
        geo.geocode("Bhagwanpur")


@respx.mock
def test_empty_query_rejected(settings: Settings) -> None:
    with _geocoder(settings) as geo, pytest.raises(GeocodingError):
        geo.geocode("   ")
