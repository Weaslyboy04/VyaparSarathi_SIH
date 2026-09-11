"""`market/price_signal.py::compute_market_price_signal` — pure aggregation
of already-retrieved AGMARKNET quotes (CLAUDE.md §5, §22, §30). No I/O, no
new arithmetic beyond min/max/median of retrieved figures, never a
retail-price synthesis from a wholesale one. Offline & pure.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from vyaparsarathi.market.price_config import MarketPriceConfig
from vyaparsarathi.market.price_signal import compute_market_price_signal
from vyaparsarathi.models.market_price import (
    CommodityPriceQuote,
    MarketPriceEvidence,
    MarketPriceStatus,
)

_ACQUIRED = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def _quote(
    commodity: str,
    market: str,
    modal: int,
    *,
    min_p: int | None = None,
    max_p: int | None = None,
    arrival: date = date(2026, 9, 10),
) -> CommodityPriceQuote:
    return CommodityPriceQuote(
        commodity=commodity,
        market=market,
        district="Vaishali",
        state="Bihar",
        min_price_inr_per_quintal=Decimal(min_p if min_p is not None else modal - 100),
        max_price_inr_per_quintal=Decimal(max_p if max_p is not None else modal + 100),
        modal_price_inr_per_quintal=Decimal(modal),
        arrival_date=arrival,
        source_id=f"agmarknet:{market}:{commodity}:{arrival}",
        retrieved_at=_ACQUIRED,
    )


def _evidence(quotes: tuple[CommodityPriceQuote, ...], **kw: object) -> MarketPriceEvidence:
    return MarketPriceEvidence(
        status=MarketPriceStatus.OK, quotes=quotes, acquired_at=_ACQUIRED, **kw
    )


def test_single_commodity_min_max_median() -> None:
    quotes = (
        _quote("Rice", "Hajipur", 2000),
        _quote("Rice", "Mahnar", 2200),
        _quote("Rice", "Lalganj", 1900),
    )
    result = compute_market_price_signal(_evidence(quotes))
    assert len(result.benchmarks) == 1
    b = result.benchmarks[0]
    assert b.commodity == "Rice"
    assert b.sample_size == 3
    assert b.min_modal_price_inr_per_quintal == Decimal(1900)
    assert b.max_modal_price_inr_per_quintal == Decimal(2200)
    assert b.median_modal_price_inr_per_quintal == Decimal(2000)
    assert set(b.markets_sampled) == {"Hajipur", "Mahnar", "Lalganj"}


def test_multi_commodity_grouping_is_independent() -> None:
    quotes = (
        _quote("Rice", "Hajipur", 2000),
        _quote("Wheat", "Hajipur", 2500),
        _quote("Wheat", "Mahnar", 2700),
    )
    result = compute_market_price_signal(_evidence(quotes))
    by_commodity = {b.commodity: b for b in result.benchmarks}
    assert set(by_commodity) == {"Rice", "Wheat"}
    assert by_commodity["Rice"].sample_size == 1
    assert by_commodity["Wheat"].sample_size == 2
    assert by_commodity["Wheat"].median_modal_price_inr_per_quintal == Decimal(2600)


def test_most_recent_arrival_date_is_the_max_across_quotes() -> None:
    quotes = (
        _quote("Rice", "Hajipur", 2000, arrival=date(2026, 9, 1)),
        _quote("Rice", "Mahnar", 2100, arrival=date(2026, 9, 10)),
    )
    result = compute_market_price_signal(_evidence(quotes))
    assert result.benchmarks[0].most_recent_arrival_date == date(2026, 9, 10)


def test_staleness_flag_uses_acquired_at_not_a_clock_read() -> None:
    old_quote = _quote("Rice", "Hajipur", 2000, arrival=date(2026, 8, 1))
    evidence = _evidence((old_quote,))  # acquired 2026-09-11 -> 41 days old
    default_cfg = MarketPriceConfig()
    result = compute_market_price_signal(evidence, config=default_cfg)
    assert result.benchmarks[0].is_stale is True

    lenient_cfg = MarketPriceConfig(staleness_days=365)
    result2 = compute_market_price_signal(evidence, config=lenient_cfg)
    assert result2.benchmarks[0].is_stale is False


def test_status_and_metadata_pass_through_unchanged() -> None:
    evidence = MarketPriceEvidence(
        status=MarketPriceStatus.OK_STATE_WIDENED,
        district="Vaishali",
        commodities_queried=("Rice", "Wheat"),
        commodities_skipped=("Gram Raw(Chholia)",),
        quotes=(),
        acquired_at=_ACQUIRED,
        warnings=("widened to state level",),
    )
    result = compute_market_price_signal(evidence)
    assert result.status is MarketPriceStatus.OK_STATE_WIDENED
    assert result.is_state_widened is True
    assert result.district_used == "Vaishali"
    assert result.commodities_queried == ("Rice", "Wheat")
    assert result.commodities_skipped == ("Gram Raw(Chholia)",)
    assert result.warnings == ("widened to state level",)
    assert result.benchmarks == ()


def test_no_quotes_yields_no_benchmarks_never_a_crash() -> None:
    evidence = MarketPriceEvidence(
        status=MarketPriceStatus.NO_RECORDS_FOUND, quotes=(), acquired_at=_ACQUIRED
    )
    result = compute_market_price_signal(evidence)
    assert result.benchmarks == ()
    assert result.status is MarketPriceStatus.NO_RECORDS_FOUND
