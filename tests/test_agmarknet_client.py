"""AGMARKNET (data.gov.in wholesale mandi price) client + adapter (CLAUDE.md
§6, §6.1's conventions applied to a single-endpoint government API — no
mirror list, unlike Overpass). All HTTP is mocked; see
`tests/fixtures/agmarknet/README.md` for provenance of the fixture values.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.config import Settings
from vyaparsarathi.errors import SourcePayloadError, SourceUnavailableError
from vyaparsarathi.sources.agmarknet.adapter import AgmarknetSource
from vyaparsarathi.sources.agmarknet.client import AgmarknetClient

from .conftest import load_fixture

BASE_URL = "https://agmarknet.test/resource/test-resource-id"


def _client(settings: Settings) -> AgmarknetClient:
    return AgmarknetClient(settings, sleep=lambda _: None)


def _source(settings: Settings) -> AgmarknetSource:
    return AgmarknetSource(settings, client=_client(settings))


# -- fetch: parsing --------------------------------------------------------


@respx.mock
def test_fetch_parses_real_shaped_records(settings: Settings) -> None:
    # One GET per queried commodity — the API's filters match a single exact
    # value, not a list — so a 2-commodity query issues 2 requests; this mock
    # (matching any params) returns the same 3-row fixture to each, hence 6.
    route = respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("agmarknet", "pulses_bihar.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(
            commodities=("Arhar (Tur/Red Gram)(Whole)", "Gram Raw(Chholia)"),
            state="Bihar",
            district="Vaishali",
        )
    assert route.call_count == 2
    assert fetch.raw_count == 6
    assert len(fetch.records) == 6
    assert fetch.commodities_queried == ("Arhar (Tur/Red Gram)(Whole)", "Gram Raw(Chholia)")
    by_market = {(r.commodity, r.market): r for r in fetch.records}
    hajipur_arhar = by_market[("Arhar (Tur/Red Gram)(Whole)", "Hajipur")]
    assert hajipur_arhar.state == "Bihar"
    assert hajipur_arhar.district == "Vaishali"
    assert hajipur_arhar.modal_price == "9400"
    assert hajipur_arhar.arrival_date == "08/09/2026"


@respx.mock
def test_fetch_empty_result(settings: Settings) -> None:
    respx.get(BASE_URL).mock(
        return_value=httpx.Response(200, json=load_fixture("agmarknet", "empty.json"))
    )
    with _source(settings) as source:
        fetch = source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")
    assert fetch.raw_count == 0
    assert fetch.records == []


# -- fetch: failure modes --------------------------------------------------


@respx.mock
def test_retry_then_success(settings: Settings) -> None:
    route = respx.get(BASE_URL).mock(
        side_effect=[
            httpx.Response(429),
            httpx.Response(200, json=load_fixture("agmarknet", "empty.json")),
        ]
    )
    with _source(settings) as source:
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")
    assert route.call_count == 2


@respx.mock
def test_persistent_5xx_raises_source_unavailable(settings: Settings) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(503))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")


@respx.mock
def test_timeout_raises_source_unavailable(settings: Settings) -> None:
    respx.get(BASE_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")


@respx.mock
def test_4xx_client_error_is_not_retried(settings: Settings) -> None:
    route = respx.get(BASE_URL).mock(return_value=httpx.Response(400))
    with _source(settings) as source, pytest.raises(SourceUnavailableError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")
    assert route.call_count == 1


@respx.mock
def test_malformed_json_raises_source_payload_error(settings: Settings) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, text="<html>not json</html>"))
    with _source(settings) as source, pytest.raises(SourcePayloadError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")


@respx.mock
def test_missing_records_key_raises_source_payload_error(settings: Settings) -> None:
    respx.get(BASE_URL).mock(return_value=httpx.Response(200, json={"total": 0}))
    with _source(settings) as source, pytest.raises(SourcePayloadError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")


def test_no_api_key_raises_source_unavailable_without_a_network_call(
    settings: Settings,
) -> None:
    unconfigured = settings.model_copy(update={"agmarknet_api_key": None})
    with _source(unconfigured) as source, pytest.raises(SourceUnavailableError):
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")


@respx.mock
def test_api_key_never_leaks_into_an_error_message(settings: Settings) -> None:
    """CLAUDE.md §24: never print a secret in logs/errors — the key travels
    only in `params=`, never string-concatenated into a URL a raised
    exception's message could echo back."""
    respx.get(BASE_URL).mock(return_value=httpx.Response(503))
    with _source(settings) as source, pytest.raises(SourceUnavailableError) as excinfo:
        source.fetch(commodities=("Rice",), state="Bihar", district="Vaishali")
    assert "test-agmarknet-key" not in str(excinfo.value)
