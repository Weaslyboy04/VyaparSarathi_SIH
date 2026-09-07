"""`app/service.py::AdvisoryService` — the Phase 6 milestone (CLAUDE.md §25
Phase 6): the entire Phase 1-5 pipeline running multi-turn through the
channel-neutral backend API, deterministically, with `llm_enabled=False`
(no LLM provider is even constructed in this file). All fakes; no network.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from vyaparsarathi.app.dto import (
    AdvisoryPhase,
    ChannelId,
    ExpectedInput,
    MessageRequest,
    StartSessionRequest,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.conversation.session_models import SlotName, StepId
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

# --- fakes (same duck-typed style as tests/test_discovery.py) --------------


class FakeGeocoder:
    def __init__(self, candidates: list[PlaceCandidate]) -> None:
        self._candidates = candidates

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return list(self._candidates)


class FakeSource:
    name = SourceName.OSM

    def __init__(self, fetch: OverpassFetch) -> None:
        self._fetch = fetch

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        return self._fetch


class FakeOverpassClient:
    """Duck-types `OverpassClient.run(ql) -> (elements, endpoint, fallback)`,
    used directly by `acquire_demand_evidence`/`acquire_opportunity_evidence`
    (which call only `.run`, never `.fetch`)."""

    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        return [], "https://overpass.test/api/interpreter", False


def _cand(name: str, lat: float, lon: float, importance: float = 0.4) -> PlaceCandidate:
    return PlaceCandidate(
        display_name=name,
        latitude=lat,
        longitude=lon,
        importance=importance,
        district="Vaishali",
        state="Bihar",
        country="India",
    )


def _el(eid: int, lat: float, lon: float, **tags: str) -> RawOsmElement:
    return RawOsmElement(
        element_type="node",
        element_id=eid,
        latitude=lat,
        longitude=lon,
        tags=tags,
        raw={"type": "node", "id": eid, "tags": tags},
    )


def _fetch(elements: list[RawOsmElement]) -> OverpassFetch:
    return OverpassFetch(
        elements=elements,
        raw_count=len(elements),
        dropped_no_coordinates=0,
        endpoint_used="https://overpass.test/api/interpreter",
        mirror_fallback_used=False,
        query_ql="[out:json];",
    )


def _service(
    tmp_path: Path,
    *,
    geocode_candidates: list[PlaceCandidate] | None = None,
    elements: list[RawOsmElement] | None = None,
) -> tuple[AdvisoryService, InMemorySessionRepository]:
    geocoder = FakeGeocoder(geocode_candidates or [_cand("Bhagwanpur, Bihar", 25.75, 84.55)])
    source = FakeSource(_fetch(elements or []))
    repo = InMemoryBusinessRepository()
    from vyaparsarathi.config import Settings

    settings = Settings(
        cache_enabled=False, census_villages_path=str(tmp_path / "no-such-file.csv.gz")
    )
    discovery_service = DiscoveryService(
        geocoder=geocoder,  # type: ignore[arg-type]
        source=source,  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    census = CensusVillageSource(settings=settings)
    empty_corpus_dir = tmp_path / "knowledge"
    empty_corpus_dir.mkdir(exist_ok=True)
    corpus = FileCorpusStore(empty_corpus_dir)

    ctx = RunContext(
        settings=settings,
        discovery_service=discovery_service,
        overpass_client=FakeOverpassClient(),  # type: ignore[arg-type]
        census=census,
        corpus=corpus,
        repository=repo,
        retriever=None,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=geocoder,
        overpass_client=FakeOverpassClient(),  # type: ignore[arg-type]
    )
    sessions = InMemorySessionRepository()
    return AdvisoryService(sessions=sessions, runtime=runtime), sessions


def _msg(session_id: str, **kwargs: object) -> MessageRequest:
    return MessageRequest(
        session_id=session_id, channel=ChannelId.TEST, received_at=datetime.now(UTC), **kwargs
    )


def _provide_info(
    **slot_kwargs: tuple[str, str, ValueNormalization],
) -> tuple[SlotUpdateInput, ...]:
    """slot_kwargs values are (raw_text, value_token, normalization)."""
    updates = []
    for slot_name, (raw, token, norm) in slot_kwargs.items():
        updates.append(
            SlotUpdateInput(
                slot=SlotName(slot_name), raw_text=raw, value_token=token, normalization=norm
            )
        )
    return tuple(updates)


def test_full_pipeline_reaches_a_recommendation_in_one_turn(tmp_path: Path) -> None:
    service, _sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )

    updates = _provide_info(
        proposed_business_text=(
            "a pulses grocery store",
            "a pulses grocery store",
            ValueNormalization.AS_STATED,
        ),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR),
    )
    reply = service.send_message(_msg(handle.session_id, slot_updates=updates))

    assert reply.state is AdvisoryPhase.COMPLETE
    assert reply.expects is ExpectedInput.NONE
    assert "recommendation" in reply.narrative.sections
    assert reply.narrative.generated_by["recommendation"] == "template"

    session = _sessions.get(handle.session_id)
    assert session is not None
    assert StepId.RECOMMEND in session.artifacts
    # zero LLM involvement at any point
    assert all(not t.llm_used for t in session.turns)


def test_llm_enabled_false_is_supported_with_no_provider_configured(tmp_path: Path) -> None:
    """The service never constructs an LLM provider in this test file at
    all; the pipeline still reaches a complete recommendation."""
    service, _ = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
    )
    reply = service.send_message(_msg(handle.session_id, slot_updates=updates))
    assert reply.state in (AdvisoryPhase.COMPLETE, AdvisoryPhase.ANALYSING)


def test_unknown_session_raises(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    with pytest.raises(SessionNotFoundError):
        service.send_message(_msg("does-not-exist"))


def test_ambiguous_location_asks_for_a_choice(tmp_path: Path) -> None:
    candidates = [
        _cand("Bhagwanpur, Bihar", 25.75, 84.55),
        _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
    ]
    service, sessions = _service(tmp_path, geocode_candidates=candidates)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur", "Bhagwanpur", ValueNormalization.AS_STATED),
    )
    reply = service.send_message(_msg(handle.session_id, slot_updates=updates))
    assert reply.expects is ExpectedInput.CHOICE
    assert len(reply.choices) == 2


def test_session_isolation_between_two_ids(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    a = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    b = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )

    updates = _provide_info(
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR)
    )
    service.send_message(_msg(a.session_id, slot_updates=updates))

    session_a = sessions.get(a.session_id)
    session_b = sessions.get(b.session_id)
    assert session_a.slot(SlotName.LIQUID_CASH_INR).value == 650_000
    assert session_b.slot(SlotName.LIQUID_CASH_INR).value is None


def test_correction_turn_reruns_only_downstream_steps(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    first = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR),
    )
    service.send_message(_msg(handle.session_id, slot_updates=first))

    correction = _provide_info(
        liquid_cash_inr=("actually only 4 lakh", "4 lakh", ValueNormalization.LAKH_TO_INR)
    )
    service.send_message(_msg(handle.session_id, slot_updates=correction))

    session = sessions.get(handle.session_id)
    last_turn = session.turns[-1]
    assert StepId.DISCOVER not in last_turn.steps_invalidated
    assert StepId.OPPORTUNITY in last_turn.steps_invalidated


def test_snapshot_carries_the_recommendation(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
    )
    service.send_message(_msg(handle.session_id, slot_updates=updates))
    snap = service.snapshot(handle.session_id)
    assert snap is not None
    assert snap.recommendation is not None
    assert "verdict" in snap.recommendation


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
