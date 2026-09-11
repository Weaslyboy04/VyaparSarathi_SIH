"""AGMARKNET acquisition wiring + degradation (CLAUDE.md §22, §30). No HTTP:
a fake `AgmarknetSource` standing in exactly like `test_demand_acquisition.py`'s
`FakeClient` does for Overpass.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from vyaparsarathi.config import Settings
from vyaparsarathi.discovery.market_price_acquisition import acquire_market_price_evidence
from vyaparsarathi.errors import SourceUnavailableError
from vyaparsarathi.market.models import ProposedBusiness
from vyaparsarathi.models.market_price import MarketPriceStatus
from vyaparsarathi.models.place import ResolvedPlace
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.agmarknet.adapter import AgmarknetFetch
from vyaparsarathi.sources.agmarknet.models import RawAgmarknetRecord

_CLOCK = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def _record(
    commodity: str = "Rice", market: str = "Hajipur", district: str = "Vaishali"
) -> RawAgmarknetRecord:
    return RawAgmarknetRecord(
        state="Bihar",
        district=district,
        market=market,
        commodity=commodity,
        variety="Other",
        grade="FAQ",
        arrival_date="10/09/2026",
        min_price="1900",
        max_price="2100",
        modal_price="2000",
    )


class FakeAgmarknetSource:
    """Stands in for `AgmarknetSource` — only `.fetch()` is used."""

    def __init__(self, *, by_district: dict[str, list[RawAgmarknetRecord]] | None = None) -> None:
        self._by_district = by_district or {}
        self.calls: list[tuple[tuple[str, ...], str, str | None]] = []

    def fetch(self, commodities, state, district):  # noqa: ANN001 - test double
        self.calls.append((tuple(commodities), state, district))
        records = self._by_district.get(district or "__state__", [])
        return AgmarknetFetch(
            records=records, raw_count=len(records), commodities_queried=tuple(commodities)
        )


class RaisingAgmarknetSource:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, commodities, state, district):  # noqa: ANN001 - test double
        self.calls += 1
        raise SourceUnavailableError("AGMARKNET unreachable")


def _place(district: str | None = "Vaishali", state: str | None = "Bihar") -> ResolvedPlace:
    return ResolvedPlace(
        query="Bhagwanpur",
        display_name="Bhagwanpur, Vaishali, Bihar",
        latitude=25.71,
        longitude=85.21,
        state=state,
        district=district,
    )


def _proposed(category: C = C.GROCERY, subtypes: tuple[str, ...] = (), resolved: bool = True):
    return ProposedBusiness(category=category, subtypes=list(subtypes), resolved=resolved)


def _settings(**kw: object) -> Settings:
    return Settings(agmarknet_api_key="test-key", **kw)


def test_district_match_succeeds() -> None:
    client = FakeAgmarknetSource(by_district={"Vaishali": [_record()]})
    ev = acquire_market_price_evidence(
        _proposed(), _place(), client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    assert ev.status is MarketPriceStatus.OK
    assert ev.district_match_used is True
    assert len(ev.quotes) == 1
    assert len(client.calls) == 1


def test_district_empty_widens_to_state() -> None:
    client = FakeAgmarknetSource(by_district={"__state__": [_record()]})
    ev = acquire_market_price_evidence(
        _proposed(), _place(), client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    assert ev.status is MarketPriceStatus.OK_STATE_WIDENED
    assert ev.district_match_used is False
    assert len(ev.quotes) == 1
    assert len(client.calls) == 2  # district attempt, then state-only retry
    assert any("widened" in w.lower() for w in ev.warnings)


def test_both_district_and_state_empty_is_no_records_found() -> None:
    client = FakeAgmarknetSource(by_district={})
    ev = acquire_market_price_evidence(
        _proposed(), _place(), client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    assert ev.status is MarketPriceStatus.NO_RECORDS_FOUND
    assert ev.quotes == ()


def test_unmapped_category_makes_zero_calls() -> None:
    client = FakeAgmarknetSource(by_district={"Vaishali": [_record()]})
    ev = acquire_market_price_evidence(
        _proposed(category=C.PHARMACY),
        _place(),
        client=client,
        settings=_settings(),
        clock=lambda: _CLOCK,
    )
    assert ev.status is MarketPriceStatus.NOT_APPLICABLE_CATEGORY
    assert client.calls == []


def test_unresolved_proposed_business_makes_zero_calls() -> None:
    client = FakeAgmarknetSource()
    ev = acquire_market_price_evidence(
        _proposed(resolved=False),
        _place(),
        client=client,
        settings=_settings(),
        clock=lambda: _CLOCK,
    )
    assert ev.status is MarketPriceStatus.NOT_APPLICABLE_CATEGORY
    assert client.calls == []


def test_no_resolved_place_makes_zero_calls() -> None:
    client = FakeAgmarknetSource()
    ev = acquire_market_price_evidence(
        _proposed(), None, client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    assert ev.status is MarketPriceStatus.LOCATION_UNRESOLVED
    assert client.calls == []


def test_no_api_key_makes_zero_calls() -> None:
    client = FakeAgmarknetSource(by_district={"Vaishali": [_record()]})
    ev = acquire_market_price_evidence(
        _proposed(),
        _place(),
        client=client,
        settings=Settings(agmarknet_api_key=None),
        clock=lambda: _CLOCK,
    )
    assert ev.status is MarketPriceStatus.NOT_CONFIGURED
    assert client.calls == []


def test_source_unavailable_is_recorded_not_raised() -> None:
    client = RaisingAgmarknetSource()
    ev = acquire_market_price_evidence(
        _proposed(), _place(), client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    assert ev.status is MarketPriceStatus.SOURCE_UNAVAILABLE
    assert ev.warnings


def test_no_district_available_still_queries_state_level() -> None:
    client = FakeAgmarknetSource(by_district={"__state__": [_record(district="")]})
    ev = acquire_market_price_evidence(
        _proposed(),
        _place(district=None),
        client=client,
        settings=_settings(),
        clock=lambda: _CLOCK,
    )
    assert ev.status is MarketPriceStatus.OK_STATE_WIDENED
    assert len(client.calls) == 1  # no district to try, so only the state-only call happens
    assert client.calls[0][2] is None


def test_commodities_beyond_the_cap_are_recorded_as_skipped() -> None:
    client = FakeAgmarknetSource(
        by_district={"Vaishali": [_record(commodity="Bengal Gram(Gram)(Whole)")]}
    )
    settings = _settings(agmarknet_max_commodities_per_query=1)
    ev = acquire_market_price_evidence(
        _proposed(subtypes=("pulses",)),
        _place(),
        client=client,
        settings=settings,
        clock=lambda: _CLOCK,
    )
    assert len(ev.commodities_queried) == 1
    assert len(ev.commodities_skipped) >= 1
    assert set(ev.commodities_queried).isdisjoint(ev.commodities_skipped)


def test_arrival_date_and_prices_are_parsed_into_real_types() -> None:
    client = FakeAgmarknetSource(by_district={"Vaishali": [_record()]})
    ev = acquire_market_price_evidence(
        _proposed(), _place(), client=client, settings=_settings(), clock=lambda: _CLOCK
    )
    q = ev.quotes[0]
    assert q.arrival_date == date(2026, 9, 10)
    assert q.modal_price_inr_per_quintal == 2000
