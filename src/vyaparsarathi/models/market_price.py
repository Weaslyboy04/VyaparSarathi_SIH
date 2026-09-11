"""Wholesale mandi price evidence (CLAUDE.md §5, §7, §22, §30) — the
AGMARKNET analogue of `models/demand.py::DemandEvidence`. The acquisition ->
engine seam: `discovery/market_price_acquisition.py` builds
:class:`MarketPriceEvidence`; the pure `market/price_signal.py` consumes it.
Must stay JSON round-trippable.

CLAUDE.md §3.1/§30's non-negotiable boundary: AGMARKNET is a WHOLESALE
(mandi) benchmark. Nothing here ever computes or carries a "suggested retail
selling price" — that would fabricate a markup assumption presented as fact.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.taxonomy import BusinessCategory


class MarketPriceStatus(StrEnum):
    """Every honest outcome a price lookup can have — CLAUDE.md §22/§30:
    "not relevant to check" and "checked, found nothing" are different facts
    and must never look the same to a reader."""

    OK = "ok"
    OK_STATE_WIDENED = "ok_state_widened"  # no district match; widened to state-level
    NOT_APPLICABLE_CATEGORY = "not_applicable_category"  # no commodity mapping at all
    LOCATION_UNRESOLVED = "location_unresolved"  # no state known
    NOT_CONFIGURED = "not_configured"  # no VYAPAR_AGMARKNET_API_KEY set
    SOURCE_UNAVAILABLE = "source_unavailable"  # unreachable after retries
    NO_RECORDS_FOUND = "no_records_found"  # queried, reachable, genuinely empty


class CommodityPriceQuote(BaseModel):
    """One AGMARKNET record, fully provenanced (CLAUDE.md §7, §23). Prices
    are wholesale, Rs per quintal, as reported at the named mandi."""

    model_config = ConfigDict(extra="forbid")

    commodity: str
    variety: str = ""
    grade: str = ""
    market: str
    district: str
    state: str
    min_price_inr_per_quintal: Decimal = Field(ge=0)
    max_price_inr_per_quintal: Decimal = Field(ge=0)
    modal_price_inr_per_quintal: Decimal = Field(ge=0)
    arrival_date: date
    # A plain string, deliberately NOT `models.taxonomy.SourceName` — that
    # enum is specifically the business-discovery source list; AGMARKNET
    # never produces a `NormalizedBusiness` and shouldn't overload it.
    source: str = "agmarknet"
    source_id: str
    retrieved_at: datetime
    raw: dict = Field(default_factory=dict, repr=False)


class MarketPriceEvidence(BaseModel):
    """The acquisition -> engine seam. Pure input to
    `market/price_signal.py::compute_market_price_signal`."""

    model_config = ConfigDict(extra="forbid")

    category: BusinessCategory | None = None
    subtypes: tuple[str, ...] = ()
    commodities_queried: tuple[str, ...] = ()
    # Commodities a subtype/category implied but that were never queried
    # because they exceeded the per-query cap — recorded, never silently
    # dropped (CLAUDE.md §30).
    commodities_skipped: tuple[str, ...] = ()
    state: str | None = None
    district: str | None = None
    status: MarketPriceStatus
    district_match_used: bool = False
    quotes: tuple[CommodityPriceQuote, ...] = ()
    endpoint_used: str | None = None
    # The only wall-clock read in this phase, captured once at the boundary
    # so the engine stays deterministic (CLAUDE.md §28).
    acquired_at: datetime
    warnings: tuple[str, ...] = ()


__all__ = [
    "CommodityPriceQuote",
    "MarketPriceEvidence",
    "MarketPriceStatus",
]
