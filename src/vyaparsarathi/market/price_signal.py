"""Pure aggregation of AGMARKNET wholesale price quotes into per-commodity
benchmarks (CLAUDE.md §5, §22, §30). No I/O, no wall-clock read, no new
arithmetic beyond min/max/median of already-retrieved figures — and never a
retail-price synthesis from a wholesale one.
"""

from __future__ import annotations

import statistics

from vyaparsarathi.market.price_config import DEFAULT_MARKET_PRICE_CONFIG, MarketPriceConfig
from vyaparsarathi.market.price_models import CommodityBenchmark, MarketPriceSignalResult
from vyaparsarathi.models.market_price import (
    CommodityPriceQuote,
    MarketPriceEvidence,
    MarketPriceStatus,
)


def compute_market_price_signal(
    evidence: MarketPriceEvidence,
    *,
    config: MarketPriceConfig = DEFAULT_MARKET_PRICE_CONFIG,
) -> MarketPriceSignalResult:
    """Group `evidence.quotes` by commodity into a min/max/median benchmark.
    `evidence.status`/metadata pass straight through — this function never
    decides applicability or reachability, only aggregates what was found.
    "Staleness" is judged against `evidence.acquired_at` (captured once at
    the acquisition boundary), never a fresh clock read (CLAUDE.md §28)."""
    by_commodity: dict[str, list[CommodityPriceQuote]] = {}
    for quote in evidence.quotes:
        by_commodity.setdefault(quote.commodity, []).append(quote)

    as_of = evidence.acquired_at.date()
    benchmarks: list[CommodityBenchmark] = []
    for commodity, quotes in by_commodity.items():
        modal_prices = [q.modal_price_inr_per_quintal for q in quotes]
        most_recent = max(q.arrival_date for q in quotes)
        benchmarks.append(
            CommodityBenchmark(
                commodity=commodity,
                sample_size=len(quotes),
                min_modal_price_inr_per_quintal=min(modal_prices),
                max_modal_price_inr_per_quintal=max(modal_prices),
                median_modal_price_inr_per_quintal=statistics.median(modal_prices),
                most_recent_arrival_date=most_recent,
                markets_sampled=tuple(sorted({q.market for q in quotes})),
                is_stale=(as_of - most_recent).days > config.staleness_days,
            )
        )
    benchmarks.sort(key=lambda b: b.commodity)

    return MarketPriceSignalResult(
        status=evidence.status,
        district_used=evidence.district,
        is_state_widened=evidence.status is MarketPriceStatus.OK_STATE_WIDENED,
        commodities_queried=evidence.commodities_queried,
        commodities_skipped=evidence.commodities_skipped,
        benchmarks=tuple(benchmarks),
        warnings=evidence.warnings,
    )


__all__ = ["compute_market_price_signal"]
