"""`channels/whatsapp/gateway.py` end to end: a neutral webhook `dict` ->
`WhatsAppGateway` -> the real `AdvisoryService` (offline fakes for every
engine, a `ScriptedLlmProvider` for extraction) -> neutral outbound bubbles,
recorded by `FakeWhatsAppTransport`. No network (CLAUDE.md §28).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.channels.whatsapp import (
    FakeWhatsAppTransport,
    MalformedWebhookError,
    WhatsAppGateway,
    channel_session_id,
)
from vyaparsarathi.channels.whatsapp.models import OutboundKind
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
) -> tuple[WhatsAppGateway, AdvisoryService, FakeWhatsAppTransport]:
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
    gateway = WhatsAppGateway(service, clock=lambda: _FROZEN)
    return gateway, service, FakeWhatsAppTransport()


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


def _text_webhook(sender: str, text: str) -> dict:
    return {"from": sender, "type": "text", "text": text}


def test_happy_path_returns_multiple_bubbles_and_records_them(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(tmp_path, responses=_extraction(_HAPPY))

    out, report = gateway.handle_webhook(_text_webhook("+919812345678", _HAPPY_TEXT))
    transport.send(out)

    assert len(out) >= 2  # multi-bubble advisory
    assert all(m.to == "+919812345678" for m in out)
    assert transport.sent == list(out)
    assert "recommendation" in " ".join(transport.bodies).lower()
    assert report is None  # not requested on the delivery turn itself

    snap = service.snapshot(channel_session_id("+919812345678"))
    assert snap is not None
    assert StepId.RECOMMEND.value in snap.artifacts  # pipeline ran through the channel


def test_first_message_from_a_new_sender_is_greeted(tmp_path: Path) -> None:
    from vyaparsarathi.app.greeting import GREETING_MESSAGE

    gateway, _service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    sender = "+919812345678"

    out, _report = gateway.handle_webhook(_text_webhook(sender, "hi"))
    transport.send(out)

    assert out[0].to == sender
    assert out[0].body == GREETING_MESSAGE


def test_second_message_from_a_known_sender_is_not_greeted_again(tmp_path: Path) -> None:
    from vyaparsarathi.app.greeting import GREETING_MESSAGE

    gateway, _service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    sender = "+919812345678"

    gateway.handle_webhook(_text_webhook(sender, "hi"))
    out, _report = gateway.handle_webhook(_text_webhook(sender, "still not sure"))

    assert GREETING_MESSAGE not in [m.body for m in out]


def test_session_continuity_across_two_messages_from_the_same_sender(tmp_path: Path) -> None:
    gateway, service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    sender = "+919812345678"

    gateway.handle_webhook(_text_webhook(sender, "hi"))
    gateway.handle_webhook(_text_webhook(sender, "still not sure"))

    snap = service.snapshot(channel_session_id(sender))
    assert snap is not None
    assert snap.turn_index == 2  # one session, two turns — identity preserved


def test_two_senders_get_isolated_sessions(tmp_path: Path) -> None:
    gateway, service, _t = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    gateway.handle_webhook(_text_webhook("+91111", "hello from A"))
    gateway.handle_webhook(_text_webhook("+91222", "hello from B"))

    a = service.snapshot(channel_session_id("+91111"))
    b = service.snapshot(channel_session_id("+91222"))
    assert a is not None and b is not None
    assert a.session_id != b.session_id
    assert a.turn_index == 1 and b.turn_index == 1


def test_numbered_choice_rendered_as_text_then_answered_by_reply_id(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(
        tmp_path,
        responses=_extraction(
            {
                "intent": "provide_info",
                "slot_updates": [
                    {
                        "slot": "proposed_business_text",
                        "raw_text": "a grocery shop",
                        "value_token": "a grocery shop",
                        "normalization": "as_stated",
                    },
                    {
                        "slot": "location_text",
                        "raw_text": "Bhagwanpur",
                        "value_token": "Bhagwanpur",
                        "normalization": "as_stated",
                    },
                ],
            }
        ),
        candidates=[
            _cand("Bhagwanpur, Bihar", 25.75, 84.55),
            _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
        ],
    )
    sender = "+919812345678"

    first, _report1 = gateway.handle_webhook(_text_webhook(sender, "a grocery shop in Bhagwanpur"))
    transport.send(first)
    bodies = [m.body for m in first]
    assert "Reply with a number from 1 to 2." in bodies
    assert any(b.startswith("1. ") for b in bodies) and any(b.startswith("2. ") for b in bodies)

    second, _report2 = gateway.handle_webhook(
        {
            "from": sender,
            "type": "interactive",
            "interactive": {"reply_id": "1", "title": "Bhagwanpur, Bihar"},
        }
    )
    assert second  # a reply came back, no crash
    snap = service.snapshot(channel_session_id(sender))
    assert snap is not None and snap.turn_index == 2


def test_voice_note_gets_a_deferred_notice_and_starts_no_session(tmp_path: Path) -> None:
    gateway, service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    sender = "+919812345678"

    out, report = gateway.handle_webhook({"from": sender, "type": "voice"})
    transport.send(out)

    assert len(out) == 1
    assert out[0].kind is OutboundKind.DEFERRED_NOTICE
    assert "voice" in out[0].body.lower()
    assert report is None
    assert service.get_session(channel_session_id(sender)) is None  # no turn ran


def test_media_gets_a_distinct_deferred_notice(tmp_path: Path) -> None:
    gateway, _service, _t = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    out, report = gateway.handle_webhook({"from": "+91", "type": "image"})
    assert out[0].kind is OutboundKind.DEFERRED_NOTICE
    assert "attachment" in out[0].body.lower()
    assert report is None


def test_clear_command_deletes_the_session_without_running_a_turn(tmp_path: Path) -> None:
    from vyaparsarathi.app.clear_command import CLEAR_CONFIRMATION_MESSAGE

    gateway, service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    sender = "+919812345678"
    gateway.handle_webhook(_text_webhook(sender, "hi"))
    assert service.get_session(channel_session_id(sender)) is not None

    out, report = gateway.handle_webhook(_text_webhook(sender, "/clear"))
    transport.send(out)

    assert report is None
    assert len(out) == 1
    assert out[0].body == CLEAR_CONFIRMATION_MESSAGE
    assert service.get_session(channel_session_id(sender)) is None


def test_message_after_clear_starts_a_genuinely_fresh_session(tmp_path: Path) -> None:
    from vyaparsarathi.app.greeting import GREETING_MESSAGE

    gateway, service, transport = _gateway(
        tmp_path, responses=_extraction({"intent": "unclear"}, repeats=2)
    )
    sender = "+919812345678"
    gateway.handle_webhook(_text_webhook(sender, "hi"))
    gateway.handle_webhook(_text_webhook(sender, "/clear"))

    out, _report = gateway.handle_webhook(_text_webhook(sender, "hi again"))
    transport.send(out)

    assert out[0].body == GREETING_MESSAGE
    snap = service.snapshot(channel_session_id(sender))
    assert snap is not None and snap.turn_index == 1


def test_malformed_webhook_raises_and_sends_nothing(tmp_path: Path) -> None:
    gateway, _service, transport = _gateway(tmp_path, responses=_extraction({"intent": "unclear"}))
    with pytest.raises(MalformedWebhookError):
        gateway.handle_webhook({"type": "text", "text": "no sender"})
    assert transport.sent == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
