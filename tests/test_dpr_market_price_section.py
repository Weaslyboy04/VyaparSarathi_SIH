"""Priority 4 wiring: the AGMARKNET market-price signal must reach the DPR
with genuinely distinct wording per `MarketPriceStatus` — "not relevant to
check" and "checked, found nothing" must never look the same to a reader
(CLAUDE.md §22, §30) — and never show a synthesized retail price from the
wholesale figures it carries (CLAUDE.md §3.1). Offline.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from vyaparsarathi.dpr.artifacts import ArtifactSet
from vyaparsarathi.dpr.report_models import SectionStatus
from vyaparsarathi.dpr.sections import build_market_price_section
from vyaparsarathi.market.price_models import MarketPriceSignalResult
from vyaparsarathi.market.price_signal import compute_market_price_signal
from vyaparsarathi.models.market_price import (
    CommodityPriceQuote,
    MarketPriceEvidence,
    MarketPriceStatus,
)

_ACQUIRED = datetime(2026, 9, 11, 9, 0, tzinfo=UTC)


def _empty_arts(**kw: object) -> ArtifactSet:
    fields = {
        "proposed": None,
        "discovery": None,
        "analysis": None,
        "metrics": None,
        "demand": None,
        "market_price_evidence": None,
        "market_price": None,
        "market": None,
        "opportunity": None,
        "knowledge": None,
        "plan": None,
        "scheme_capacity": None,
        "structure": None,
        "finance": None,
        "recommendation": None,
        "swot": None,
    }
    fields.update(kw)
    return ArtifactSet(**fields)


def _result(status: MarketPriceStatus, **kw: object) -> MarketPriceSignalResult:
    return MarketPriceSignalResult(status=status, **kw)


def test_no_artifact_at_all_is_an_evidence_gap() -> None:
    sec = build_market_price_section(None, _empty_arts())  # type: ignore[arg-type]
    assert sec.status is SectionStatus.EVIDENCE_GAP
    assert sec.gap_note


def test_not_applicable_category_reads_as_not_relevant_not_missing() -> None:
    sec = build_market_price_section(
        None, _empty_arts(market_price=_result(MarketPriceStatus.NOT_APPLICABLE_CATEGORY))
    )  # type: ignore[arg-type]
    assert sec.status is SectionStatus.EVIDENCE_GAP
    assert "doesn't trade" in sec.gap_note.lower() or "not relevant" in sec.gap_note.lower()


def test_no_records_found_reads_as_checked_not_as_not_applicable() -> None:
    sec = build_market_price_section(
        None, _empty_arts(market_price=_result(MarketPriceStatus.NO_RECORDS_FOUND))
    )  # type: ignore[arg-type]
    assert sec.status is SectionStatus.EVIDENCE_GAP
    assert "checked" in sec.gap_note.lower() or "no recent" in sec.gap_note.lower()


def test_the_two_gap_notes_are_never_the_same_text() -> None:
    not_applicable = build_market_price_section(
        None, _empty_arts(market_price=_result(MarketPriceStatus.NOT_APPLICABLE_CATEGORY))
    )  # type: ignore[arg-type]
    not_found = build_market_price_section(
        None, _empty_arts(market_price=_result(MarketPriceStatus.NO_RECORDS_FOUND))
    )  # type: ignore[arg-type]
    assert not_applicable.gap_note != not_found.gap_note


def _quote(
    commodity: str = "Rice", market: str = "Hajipur", modal: int = 2000
) -> CommodityPriceQuote:
    return CommodityPriceQuote(
        commodity=commodity,
        market=market,
        district="Vaishali",
        state="Bihar",
        min_price_inr_per_quintal=Decimal(modal - 100),
        max_price_inr_per_quintal=Decimal(modal + 100),
        modal_price_inr_per_quintal=Decimal(modal),
        arrival_date=date(2026, 9, 10),
        source_id=f"agmarknet:{market}:{commodity}",
        retrieved_at=_ACQUIRED,
    )


def test_ok_renders_benchmarks_with_the_not_a_retail_price_note() -> None:
    evidence = MarketPriceEvidence(
        status=MarketPriceStatus.OK,
        district="Vaishali",
        district_match_used=True,
        quotes=(_quote(),),
        acquired_at=_ACQUIRED,
    )
    result = compute_market_price_signal(evidence)
    sec = build_market_price_section(
        None, _empty_arts(market_price_evidence=evidence, market_price=result)
    )  # type: ignore[arg-type]
    assert sec.status is SectionStatus.RENDERED
    assert sec.widened_to_state is False
    assert len(sec.benchmarks) == 1
    assert sec.benchmarks[0].commodity == "Rice"
    assert sec.not_a_retail_price_note
    assert "not" in sec.not_a_retail_price_note.lower()
    assert "retail" in sec.not_a_retail_price_note.lower()


def test_state_widened_is_flagged_and_carries_a_caveat() -> None:
    evidence = MarketPriceEvidence(
        status=MarketPriceStatus.OK_STATE_WIDENED,
        district="Vaishali",
        district_match_used=False,
        quotes=(_quote(),),
        acquired_at=_ACQUIRED,
        warnings=("No AGMARKNET records for Vaishali district; widened to state-level.",),
    )
    result = compute_market_price_signal(evidence)
    sec = build_market_price_section(
        None, _empty_arts(market_price_evidence=evidence, market_price=result)
    )  # type: ignore[arg-type]
    assert sec.status is SectionStatus.PARTIAL
    assert sec.widened_to_state is True
    assert sec.caveats
    assert any("widened" in c.lower() or "district" in c.lower() for c in sec.caveats)


def test_no_benchmark_string_ever_suggests_a_retail_price() -> None:
    """Regression guard: no rendered text in this section may read as a
    suggested retail/selling price (CLAUDE.md §3.1, §30)."""
    import re

    evidence = MarketPriceEvidence(
        status=MarketPriceStatus.OK,
        district="Vaishali",
        district_match_used=True,
        quotes=(_quote(),),
        acquired_at=_ACQUIRED,
    )
    result = compute_market_price_signal(evidence)
    sec = build_market_price_section(
        None, _empty_arts(market_price_evidence=evidence, market_price=result)
    )  # type: ignore[arg-type]
    blob = sec.model_dump_json()
    assert not re.search(r"suggest(ed)? (retail|selling) price", blob, re.IGNORECASE)


def test_benchmark_never_carries_a_fabricated_commodity_when_not_applicable() -> None:
    sec = build_market_price_section(
        None, _empty_arts(market_price=_result(MarketPriceStatus.NOT_APPLICABLE_CATEGORY))
    )  # type: ignore[arg-type]
    assert sec.benchmarks == ()
