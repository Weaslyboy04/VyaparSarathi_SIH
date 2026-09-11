"""Pure market-price signal result models (CLAUDE.md §5, §22) — the
AGMARKNET analogue of `market/demand_models.py::DemandSignalsResult`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.market_price import MarketPriceStatus


class CommodityBenchmark(BaseModel):
    """One commodity's wholesale/mandi benchmark, aggregated from one or more
    already-retrieved quotes. Never a retail-price synthesis (CLAUDE.md §30)."""

    model_config = ConfigDict(extra="forbid")

    commodity: str
    sample_size: int = Field(ge=1)
    min_modal_price_inr_per_quintal: Decimal
    max_modal_price_inr_per_quintal: Decimal
    median_modal_price_inr_per_quintal: Decimal
    most_recent_arrival_date: date
    markets_sampled: tuple[str, ...] = ()
    is_stale: bool = False


class MarketPriceSignalResult(BaseModel):
    """Deterministic, JSON-serializable. Pure aggregation of already-
    retrieved `CommodityPriceQuote`s — never a new fabricated figure, never
    a retail-price synthesis from a wholesale one (CLAUDE.md §3.1, §30)."""

    model_config = ConfigDict(extra="forbid")

    status: MarketPriceStatus
    district_used: str | None = None
    is_state_widened: bool = False
    commodities_queried: tuple[str, ...] = ()
    commodities_skipped: tuple[str, ...] = ()
    benchmarks: tuple[CommodityBenchmark, ...] = ()
    warnings: tuple[str, ...] = ()


__all__ = ["CommodityBenchmark", "MarketPriceSignalResult"]
