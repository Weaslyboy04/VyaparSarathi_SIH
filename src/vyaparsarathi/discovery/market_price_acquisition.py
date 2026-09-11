"""AGMARKNET acquisition — the only market-price layer that touches network
(CLAUDE.md §33, mirrors `demand_acquisition.py`). Turns a resolved business
category + location into a :class:`MarketPriceEvidence`. Never raises past
its boundary — every failure is caught and turned into an honest
:class:`MarketPriceStatus`, never a crash and never a fabricated result
(CLAUDE.md §30).

Degradation order (CLAUDE.md §22, §30 — "not relevant to check" and
"checked, found nothing" must never look the same to a reader):

1. No API key configured -> ``NOT_CONFIGURED``, zero HTTP calls.
2. No resolved business category, or the category/subtypes imply no
   AGMARKNET commodity at all -> ``NOT_APPLICABLE_CATEGORY``, zero calls.
3. No resolved state -> ``LOCATION_UNRESOLVED``, zero calls. Deliberately no
   nationwide fallback — a mislabeled national figure is worse than an
   honest gap.
4. Query with the district filter (when a district is known). Zero records
   -> retry state-only. Records found only at state level ->
   ``OK_STATE_WIDENED``, with an explicit caveat that the figure is not
   district-specific.
5. Still zero after both attempts -> ``NO_RECORDS_FOUND``.
6. A transport failure at any point -> ``SOURCE_UNAVAILABLE``.
"""

from __future__ import annotations

from datetime import date as date_
from datetime import datetime

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, SourcePayloadError, SourceUnavailableError
from vyaparsarathi.finance.money import rupees
from vyaparsarathi.market.commodity_map import commodities_for
from vyaparsarathi.market.models import ProposedBusiness
from vyaparsarathi.models.market_price import (
    CommodityPriceQuote,
    MarketPriceEvidence,
    MarketPriceStatus,
)
from vyaparsarathi.models.place import ResolvedPlace
from vyaparsarathi.sources.agmarknet.adapter import AgmarknetFetch, AgmarknetSource
from vyaparsarathi.sources.agmarknet.models import RawAgmarknetRecord
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)


def _parse_arrival_date(raw: str) -> date_:
    return datetime.strptime(raw, "%d/%m/%Y").date()


def _to_quote(record: RawAgmarknetRecord, *, retrieved_at: datetime) -> CommodityPriceQuote | None:
    try:
        return CommodityPriceQuote(
            commodity=record.commodity,
            variety=record.variety,
            grade=record.grade,
            market=record.market,
            district=record.district,
            state=record.state,
            min_price_inr_per_quintal=rupees(record.min_price),
            max_price_inr_per_quintal=rupees(record.max_price),
            modal_price_inr_per_quintal=rupees(record.modal_price),
            arrival_date=_parse_arrival_date(record.arrival_date),
            source_id=(
                f"agmarknet:{record.state}:{record.district}:{record.market}:"
                f"{record.commodity}:{record.variety}:{record.arrival_date}"
            ),
            retrieved_at=retrieved_at,
            raw=record.raw,
        )
    except Exception:  # noqa: BLE001 - a malformed row is dropped, never fatal
        logger.warning("dropped an unparseable AGMARKNET quote for %r", record.commodity)
        return None


def acquire_market_price_evidence(
    proposed: ProposedBusiness | None,
    resolved_place: ResolvedPlace | None,
    *,
    client: AgmarknetSource,
    settings: Settings | None = None,
    clock: Clock = utcnow,
) -> MarketPriceEvidence:
    s = settings or get_settings()
    acquired_at = clock()

    category = proposed.category if proposed is not None and proposed.resolved else None
    subtypes = tuple(proposed.subtypes) if proposed is not None else ()
    base: dict[str, object] = {
        "category": category,
        "subtypes": subtypes,
        "acquired_at": acquired_at,
    }

    if s.agmarknet_api_key is None:
        return MarketPriceEvidence(status=MarketPriceStatus.NOT_CONFIGURED, **base)

    if category is None:
        return MarketPriceEvidence(status=MarketPriceStatus.NOT_APPLICABLE_CATEGORY, **base)

    commodities = commodities_for(category, subtypes)
    if commodities is None:
        return MarketPriceEvidence(status=MarketPriceStatus.NOT_APPLICABLE_CATEGORY, **base)

    cap = s.agmarknet_max_commodities_per_query
    queried, skipped = commodities[:cap], commodities[cap:]
    base = {**base, "commodities_queried": queried, "commodities_skipped": skipped}

    state = resolved_place.state if resolved_place is not None else None
    if not state:
        return MarketPriceEvidence(status=MarketPriceStatus.LOCATION_UNRESOLVED, **base)
    district = resolved_place.district if resolved_place is not None else None
    base = {**base, "state": state}

    def _fetch(with_district: str | None) -> AgmarknetFetch:
        return client.fetch(commodities=queried, state=state, district=with_district)

    warnings: list[str] = []
    try:
        fetch = _fetch(district)
        district_match_used = bool(district) and len(fetch.records) > 0
        if not fetch.records and district:
            fetch = _fetch(None)
            if fetch.records:
                warnings.append(
                    f"No AGMARKNET records for {district} district; widened to "
                    f"state-level ({state}) prices — not specific to your exact location."
                )
    except (SourceUnavailableError, SourcePayloadError, HttpError) as exc:
        logger.warning("AGMARKNET acquisition failed: %s", exc)
        return MarketPriceEvidence(
            status=MarketPriceStatus.SOURCE_UNAVAILABLE,
            warnings=[f"AGMARKNET unavailable: {exc}"],
            **base,
        )

    quotes = tuple(
        q for r in fetch.records if (q := _to_quote(r, retrieved_at=acquired_at)) is not None
    )
    if not quotes:
        return MarketPriceEvidence(
            status=MarketPriceStatus.NO_RECORDS_FOUND, district=district, **base
        )

    status = (
        MarketPriceStatus.OK if district_match_used else MarketPriceStatus.OK_STATE_WIDENED
    )
    return MarketPriceEvidence(
        status=status,
        district=district,
        district_match_used=district_match_used,
        quotes=quotes,
        endpoint_used=s.agmarknet_base_url,
        warnings=warnings,
        **base,
    )


__all__ = ["acquire_market_price_evidence"]
