"""Shared offline pipeline driver for the Phase 8 DPR tests (CLAUDE.md §28 —
every test here runs with no network). NOT a test module itself.

`run_pipeline` builds an `AdvisoryService` with fixture engines, sends the
given slot updates as one or two structured turns (no LLM), and returns the
finished `ConversationSession` for `dpr.assemble_report` to consume.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from vyaparsarathi.app.dto import ChannelId, MessageRequest, StartSessionRequest
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName
from vyaparsarathi.conversation.understanding import SlotUpdateInput
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.models import RawOsmElement

_CENSUS_FIXTURE = Path(__file__).parent / "fixtures" / "census" / "demo_villages.csv"
# Near the fixture census villages so the population join returns residents.
_LAT, _LON = 25.7120, 85.2100

_DEFAULT_SHOPS: tuple[dict[str, str], ...] = (
    {"shop": "convenience", "name": "Sharma Kirana"},
    {"shop": "supermarket", "name": "Bhagwanpur Bazaar"},
    {"shop": "general", "name": "Gupta Store"},
    {"shop": "convenience", "name": "Raj Grocery"},
    {"shop": "grocery", "name": "Anaj Bhandar"},
    {"amenity": "pharmacy", "name": "Bhagwanpur Medical"},
)


class _Geocoder:
    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                display_name="Bhagwanpur, Vaishali, Bihar",
                latitude=_LAT,
                longitude=_LON,
                importance=0.5,
                country="India",
                state="Bihar",
                district="Vaishali",
                block="Bhagwanpur",
                village="Bhagwanpur",
            )
        ]


class _Source:
    def __init__(self, shops: tuple[dict[str, str], ...]) -> None:
        self.name = SourceName.OSM
        self._shops = shops

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        els = [
            RawOsmElement(
                element_type="node",
                element_id=i + 1,
                latitude=_LAT + (i - 2) * 0.0015,
                longitude=_LON + (i - 2) * 0.0015,
                tags=tags,
                raw={"type": "node", "id": i + 1, "tags": tags},
            )
            for i, tags in enumerate(self._shops)
        ]
        return OverpassFetch(
            elements=els,
            raw_count=len(els),
            dropped_no_coordinates=0,
            endpoint_used="fixture://dpr-test",
            mirror_fallback_used=False,
            query_ql="[out:json];",
        )


class _OverpassClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        return [], "fixture://dpr-test", False


def slot(name: SlotName, raw: str, token: str, norm: ValueNormalization) -> SlotUpdateInput:
    return SlotUpdateInput(slot=name, raw_text=raw, value_token=token, normalization=norm)


def run_pipeline(
    turns: list[tuple[SlotUpdateInput, ...]],
    *,
    tmp_path: Path,
    knowledge_corpus_dir: Path | None = None,
    census_csv: Path | None = _CENSUS_FIXTURE,
    shops: tuple[dict[str, str], ...] = _DEFAULT_SHOPS,
    session_id: str = "dpr-test-session",
) -> ConversationSession:
    corpus_dir = knowledge_corpus_dir or (tmp_path / "empty-corpus")
    corpus_dir.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        cache_enabled=False,
        census_villages_path=str(census_csv) if census_csv is not None else "no-such.csv.gz",
        knowledge_corpus_dir=str(corpus_dir),
        max_radius_m=25_000,
    )
    repo = InMemoryBusinessRepository()
    discovery = DiscoveryService(
        geocoder=_Geocoder(),  # type: ignore[arg-type]
        source=_Source(shops),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery,
        overpass_client=_OverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=FileCorpusStore(corpus_dir, settings=settings),
        repository=repo,
        retriever=None,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_Geocoder(),  # type: ignore[arg-type]
        overpass_client=_OverpassClient(),  # type: ignore[arg-type]
    )
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    handle = service.start_session(
        StartSessionRequest(
            session_id=session_id,
            channel=ChannelId.CLI,
            started_at=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
        )
    )
    for i, updates in enumerate(turns):
        service.send_message(
            MessageRequest(
                session_id=handle.session_id,
                channel=ChannelId.CLI,
                received_at=datetime(2026, 9, 8, 10, i + 1, tzinfo=UTC),
                slot_updates=updates,
            )
        )
    result = sessions.get(handle.session_id)
    assert result is not None
    return result


_AS = ValueNormalization.AS_STATED
_LAKH = ValueNormalization.LAKH_TO_INR
_PCT = ValueNormalization.PERCENT_TO_RATIO


def full_scenario_turns() -> list[tuple[SlotUpdateInput, ...]]:
    """business + location + cash, then the four financial drivers."""
    return [
        (
            slot(
                SlotName.PROPOSED_BUSINESS_TEXT,
                "a pulses grocery store",
                "pulses grocery store",
                _AS,
            ),
            slot(SlotName.LOCATION_TEXT, "in Bhagwanpur, Bihar", "Bhagwanpur, Bihar", _AS),
            slot(SlotName.LIQUID_CASH_INR, "I have 6.5 lakh", "6.5 lakh", _LAKH),
        ),
        (
            slot(SlotName.MONTHLY_REVENUE_INR, "about 90000 a month", "90000", _AS),
            slot(SlotName.COGS_PCT, "stock costs 82%", "82%", _PCT),
            slot(SlotName.PROJECT_COST_INR, "setup 5 lakh", "5 lakh", _LAKH),
            slot(SlotName.FIXED_OPEX_INR, "fixed 9000 a month", "9000", _AS),
        ),
    ]


def minimal_turns() -> list[tuple[SlotUpdateInput, ...]]:
    """Only the two structural blockers + cash — no financial drivers."""
    return [
        (
            slot(
                SlotName.PROPOSED_BUSINESS_TEXT,
                "a pulses grocery store",
                "pulses grocery store",
                _AS,
            ),
            slot(SlotName.LOCATION_TEXT, "in Bhagwanpur, Bihar", "Bhagwanpur, Bihar", _AS),
            slot(SlotName.LIQUID_CASH_INR, "I have 6.5 lakh", "6.5 lakh", _LAKH),
        ),
    ]


__all__ = ["full_scenario_turns", "minimal_turns", "run_pipeline", "slot"]
