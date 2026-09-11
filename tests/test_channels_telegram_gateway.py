"""`channels/telegram/gateway.py` end to end: a real Telegram `getUpdates`
update -> `TelegramGateway` -> the real `AdvisoryService` (offline fakes for
every engine, a `ScriptedLlmProvider` for extraction) -> neutral outbound
bubbles, recorded by `FakeTelegramTransport`. No network (CLAUDE.md §28).
Mirrors `tests/test_channels_whatsapp_gateway.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vyaparsarathi.app.dto import ReportStatus
from vyaparsarathi.app.greeting import GREETING_MESSAGE
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.channels.telegram import (
    FakeTelegramTransport,
    MalformedUpdateError,
    TelegramGateway,
    channel_session_id,
)
from vyaparsarathi.channels.telegram.models import OutboundKind
from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.session_models import StepId
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.llm.fake import ScriptedLlmProvider
from vyaparsarathi.llm.llm_models import LlmResponse
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.models import RawOsmElement

_FROZEN = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _FakeGeocoder:
    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return list(self._candidates)


class _FakeSource:
    name = SourceName.OSM

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        el = RawOsmElement(
            element_type="node",
            element_id=1,
            latitude=25.751,
            longitude=84.551,
            tags={"shop": "convenience", "name": "Sharma Kirana"},
            raw={"type": "node", "id": 1},
        )
        return OverpassFetch(
            elements=[el],
            raw_count=1,
            dropped_no_coordinates=0,
            endpoint_used="offline-fixture",
            mirror_fallback_used=False,
            query_ql="[out:json];",
        )


class _FakeOverpassClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        return [], "offline-fixture", False


def _cand(name: str, lat: float, lon: float) -> PlaceCandidate:
    return PlaceCandidate(
        display_name=name,
        latitude=lat,
        longitude=lon,
        importance=0.4,
        district="Vaishali",
        state="Bihar",
        country="India",
    )


def _gateway(
    tmp_path: Path,
    *,
    responses: dict[str, list[LlmResponse]],
    candidates: list[PlaceCandidate] | None = None,
) -> tuple[TelegramGateway, AdvisoryService, FakeTelegramTransport]:
    settings = Settings(
        cache_enabled=False, census_villages_path=str(tmp_path / "no-such-file.csv.gz")
    )
    repo = InMemoryBusinessRepository()
    geocoder = _FakeGeocoder(candidates or [_cand("Bhagwanpur, Bihar", 25.75, 84.55)])
    discovery = DiscoveryService(
        geocoder=geocoder,  # type: ignore[arg-type]
        source=_FakeSource(),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    empty_corpus = tmp_path / "knowledge"
    empty_corpus.mkdir(exist_ok=True)
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery,
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=FileCorpusStore(empty_corpus),
        repository=repo,
        retriever=None,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=geocoder,  # type: ignore[arg-type]
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        llm_provider=ScriptedLlmProvider(responses),
    )
    service = AdvisoryService(sessions=InMemorySessionRepository(), runtime=runtime)
    gateway = TelegramGateway(service, clock=lambda: _FROZEN)
    return gateway, service, FakeTelegramTransport()


def _extraction(payload: dict[str, object], *, repeats: int = 1) -> dict[str, list[LlmResponse]]:
    return {
        "extraction": [
            LlmResponse(text=json.dumps(payload), prompt_id="extraction") for _ in range(repeats)
        ]
    }


_HAPPY = {
    "intent": "provide_info",
    "slot_updates": [
        {
            "slot": "proposed_business_text",
            "raw_text": "open a grocery shop",
            "value_token": "open a grocery shop",
            "normalization": "as_stated",
        },
        {
            "slot": "location_text",
            "raw_text": "in Bhagwanpur, Bihar",
            "value_token": "Bhagwanpur, Bihar",
            "normalization": "as_stated",
        },
        {
            "slot": "liquid_cash_inr",
            "raw_text": "I have 6.5 lakh",
            "value_token": "6.5 lakh",
            "normalization": "lakh_to_inr",
        },
    ],
}
_HAPPY_TEXT = "I want to open a grocery shop in Bhagwanpur, Bihar and I have 6.5 lakh"


def _text_update(chat_id: int, text: str) -> dict:
    return {"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat_id}, "text": text}}


def test_happy_path_returns_multiple_bubbles_and_records_them(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(tmp_path, responses=_extraction(_HAPPY))

    out, report = gateway.handle_update(_text_update(987, _HAPPY_TEXT))
    transport.send(out)

    assert len(out) >= 2
    assert all(m.chat_id == "987" for m in out)
    assert transport.sent == list(out)
    assert "recommendation" in " ".join(transport.bodies).lower()
    assert report is None

    snap = service.snapshot(channel_session_id("987"))
    assert snap is not None
    assert StepId.RECOMMEND.value in snap.artifacts


def test_first_message_from_a_new_chat_is_greeted(tmp_path: Path) -> None:
    gateway, _service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    out, _report = gateway.handle_update(_text_update(987, "hi"))
    transport.send(out)
    assert out[0].chat_id == "987"
    assert out[0].body == GREETING_MESSAGE


def test_second_message_is_not_greeted_again(tmp_path: Path) -> None:
    gateway, _service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    gateway.handle_update(_text_update(987, "hi"))
    out, _report = gateway.handle_update(_text_update(987, "still not sure"))
    assert GREETING_MESSAGE not in [m.body for m in out]


def test_session_continuity_across_two_messages(tmp_path: Path) -> None:
    gateway, service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    gateway.handle_update(_text_update(987, "hi"))
    gateway.handle_update(_text_update(987, "still not sure"))
    snap = service.snapshot(channel_session_id("987"))
    assert snap is not None and snap.turn_index == 2


def test_two_chats_get_isolated_sessions(tmp_path: Path) -> None:
    gateway, service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    gateway.handle_update(_text_update(111, "hello from A"))
    gateway.handle_update(_text_update(222, "hello from B"))
    a = service.snapshot(channel_session_id("111"))
    b = service.snapshot(channel_session_id("222"))
    assert a is not None and b is not None
    assert a.session_id != b.session_id


def test_voice_note_gets_a_deferred_notice_and_starts_no_session(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    update = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 987}, "voice": {}}}
    out, report = gateway.handle_update(update)
    transport.send(out)
    assert len(out) == 1
    assert out[0].kind is OutboundKind.DEFERRED_NOTICE
    assert "voice" in out[0].body.lower()
    assert report is None
    assert service.get_session(channel_session_id("987")) is None


def test_media_gets_a_distinct_deferred_notice(tmp_path: Path) -> None:
    gateway, _service, _t = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    update = {"update_id": 1, "message": {"message_id": 1, "chat": {"id": 987}, "photo": []}}
    out, report = gateway.handle_update(update)
    assert out[0].kind is OutboundKind.DEFERRED_NOTICE
    assert "attachment" in out[0].body.lower()
    assert report is None


def test_clear_command_deletes_the_session_without_running_a_turn(tmp_path: Path) -> None:
    from vyaparsarathi.app.clear_command import CLEAR_CONFIRMATION_MESSAGE

    gateway, service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    gateway.handle_update(_text_update(987, "hi"))
    assert service.get_session(channel_session_id("987")) is not None

    out, report = gateway.handle_update(_text_update(987, "/clear"))
    transport.send(out)

    assert report is None
    assert len(out) == 1
    assert out[0].body == CLEAR_CONFIRMATION_MESSAGE
    assert service.get_session(channel_session_id("987")) is None


def test_message_after_clear_starts_a_genuinely_fresh_session(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    gateway.handle_update(_text_update(987, "hi"))
    gateway.handle_update(_text_update(987, "/clear"))

    out, _report = gateway.handle_update(_text_update(987, "hi again"))
    transport.send(out)

    assert out[0].body == GREETING_MESSAGE  # greeted again, exactly like a new sender
    snap = service.snapshot(channel_session_id("987"))
    assert snap is not None and snap.turn_index == 1  # a brand-new session, not turn 3


def test_clear_command_is_case_insensitive_and_has_aliases(tmp_path: Path) -> None:
    for command in ("/clear", "/CLEAR", "/reset", "/restart", "  /clear  "):
        gateway, service, _t = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
        gateway.handle_update(_text_update(987, "hi"))
        gateway.handle_update(_text_update(987, command))
        assert service.get_session(channel_session_id("987")) is None, command


def test_malformed_update_raises_and_sends_nothing(tmp_path: Path) -> None:
    gateway, _service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    with pytest.raises(MalformedUpdateError):
        gateway.handle_update({"update_id": 1, "edited_message": {"chat": {"id": 1}, "text": "x"}})
    assert transport.sent == []


def test_report_offer_flows_through_after_full_advisory(tmp_path: Path) -> None:
    full = {
        "intent": "provide_info",
        "slot_updates": [
            *_HAPPY["slot_updates"],
            {
                "slot": "monthly_revenue_inr",
                "raw_text": "40000",
                "value_token": "40000",
                "normalization": "as_stated",
            },
            {
                "slot": "cogs_pct",
                "raw_text": "70%",
                "value_token": "70%",
                "normalization": "percent_to_ratio",
            },
            {
                "slot": "project_cost_inr",
                "raw_text": "3 lakh",
                "value_token": "3 lakh",
                "normalization": "lakh_to_inr",
            },
            {
                "slot": "fixed_opex_inr",
                "raw_text": "4000",
                "value_token": "4000",
                "normalization": "as_stated",
            },
        ],
    }
    gateway, _service, transport = _gateway(tmp_path, responses=_extraction(full))
    out, report = gateway.handle_update(_text_update(987, _HAPPY_TEXT))
    transport.send(out)
    assert report is None
    assert any("PDF project report" in b.body for b in out)

    out2, report2 = gateway.handle_update(_text_update(987, "yes, generate the report"))
    assert report2 is not None
    assert report2.status is ReportStatus.REQUESTED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
