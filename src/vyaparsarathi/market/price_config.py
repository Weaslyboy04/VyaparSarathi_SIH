"""The single configuration layer for the AGMARKNET market-price signal
(CLAUDE.md §22; mirrors `demand_config.py`/`opportunity_config.py`). Every
value is an MVP heuristic, not validated.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MarketPriceConfig(BaseModel):
    """Tunable parameters for
    `market/price_signal.py::compute_market_price_signal`. Frozen so the
    shared default cannot be mutated; echoed into the result via
    `MarketPriceSignalResult` for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # A quote whose arrival_date is older than this many days (relative to
    # when the evidence was acquired, never a fresh clock read) is flagged
    # stale, not dropped — the reader decides how much to trust it.
    # [tunable]
    staleness_days: int = Field(default=14, ge=0)


DEFAULT_MARKET_PRICE_CONFIG = MarketPriceConfig()

__all__ = ["DEFAULT_MARKET_PRICE_CONFIG", "MarketPriceConfig"]
