"""`app/service.py::AdvisoryService` — the Phase 6 milestone (CLAUDE.md §25
Phase 6): the entire Phase 1-5 pipeline running multi-turn through the
channel-neutral backend API, deterministically. Most tests here run with
`llm_enabled=False` (no LLM provider constructed at all); a few use a
`ScriptedLlmProvider` fake to exercise the extraction-context wiring — never
a live network call. All fakes; no network.
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
    ReportStatus,
    StartSessionRequest,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.conversation.conversation_config import ConversationMode
from vyaparsarathi.conversation.session_models import SlotName, StepId
from vyaparsarathi.conversation.understanding import AssetUpdateInput, SlotUpdateInput
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.llm.fake import ScriptedLlmProvider
from vyaparsarathi.llm.llm_models import LlmResponse
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.profile import AssetKind
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
    geocoder: object | None = None,
    mode: ConversationMode = ConversationMode.DEVELOPER,
    llm_provider: object | None = None,
    llm_reply_authoring_enabled: bool = False,
) -> tuple[AdvisoryService, InMemorySessionRepository]:
    # `geocode_candidates or [...]` below treats `[]` as falsy (Python), so an
    # empty-candidates scenario can't be expressed via `geocode_candidates`
    # alone — pass a custom `geocoder=` (e.g. one that raises) for that.
    geocoder = geocoder or FakeGeocoder(
        geocode_candidates or [_cand("Bhagwanpur, Bihar", 25.75, 84.55)]
    )
    source = FakeSource(_fetch(elements or []))
    repo = InMemoryBusinessRepository()
    from vyaparsarathi.config import Settings

    settings = Settings(
        cache_enabled=False,
        census_villages_path=str(tmp_path / "no-such-file.csv.gz"),
        llm_reply_authoring_enabled=llm_reply_authoring_enabled,
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
        mode=mode,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=geocoder,
        overpass_client=FakeOverpassClient(),  # type: ignore[arg-type]
        llm_provider=llm_provider,  # type: ignore[arg-type]
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


def test_reply_authoring_stays_off_by_default_even_with_a_provider_configured(
    tmp_path: Path,
) -> None:
    """The opt-in flag defaults to False: a configured LLM provider must
    never be called for reply authoring unless explicitly enabled — the
    provider here would raise if `.complete()` were ever invoked."""
    provider = ScriptedLlmProvider({})  # empty: any call raises LlmUnavailableError
    service, _sessions = _service(tmp_path, llm_provider=provider)
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
    assert reply.narrative.generated_by["recommendation"] == "template"
    assert provider.calls == []


def test_reply_authoring_enabled_swaps_in_an_accepted_rewrite(tmp_path: Path) -> None:
    """End-to-end: with the flag explicitly on and a provider configured, an
    accepted, grounded rewrite is marked 'llm' in the final reply."""
    provider = ScriptedLlmProvider(
        {"explanation": [LlmResponse(text="ok", prompt_id="explanation") for _ in range(10)]}
    )
    service, _sessions = _service(tmp_path, llm_provider=provider, llm_reply_authoring_enabled=True)
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
    assert reply.narrative.generated_by["recommendation"] == "llm"
    assert reply.narrative.sections["recommendation"] == "ok"
    assert len(provider.calls) >= 1


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


def test_delete_session_removes_it_entirely(tmp_path: Path) -> None:
    """Unlike `end_session` (marks `ended=True`, a permanent terminal state),
    `delete_session` is a channel "/clear"-style reset: the session id
    becomes genuinely unknown again, so a channel that reuses a stable id
    (a phone number, a chat id) can start over from scratch."""
    service, _ = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    assert service.get_session(handle.session_id) is not None

    service.delete_session(handle.session_id)

    assert service.get_session(handle.session_id) is None
    with pytest.raises(SessionNotFoundError):
        service.send_message(_msg(handle.session_id))


def test_delete_session_is_a_no_op_for_an_unknown_session(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    service.delete_session("never-existed")  # must not raise


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


def test_choice_selection_resolves_ambiguous_location_and_progresses_pipeline(
    tmp_path: Path,
) -> None:
    """When a user selects one of the ambiguous location choices (e.g., choice 1),
    the location slot is resolved and the pipeline can progress (in DEVELOPER mode,
    this reaches a recommendation immediately)."""
    candidates = [
        _cand("Bhagwanpur, Bihar", 25.75, 84.55),
        _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
    ]
    service, sessions = _service(tmp_path, geocode_candidates=candidates)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    # First turn: ambiguous location
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur", "Bhagwanpur", ValueNormalization.AS_STATED),
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR),
    )
    reply1 = service.send_message(_msg(handle.session_id, slot_updates=updates))
    assert reply1.expects is ExpectedInput.CHOICE
    assert reply1.state is AdvisoryPhase.BLOCKED

    # Second turn: choose the first location candidate
    reply2 = service.send_message(_msg(handle.session_id, selected_choice=1))
    # After resolving the ambiguity, the pipeline should progress past disambiguation
    assert reply2.state in (AdvisoryPhase.COMPLETE, AdvisoryPhase.ANALYSING)
    assert reply2.expects is ExpectedInput.NONE

    session = sessions.get(handle.session_id)
    assert session is not None
    # The location slot should now be USER_PROVIDED (not AMBIGUOUS)
    assert session.slot(SlotName.LOCATION_TEXT).value == "Bhagwanpur, Bihar"
    # The selected candidate should be recorded
    assert session.selected_geocode_candidate == 1


def test_invalid_choice_keeps_ambiguous_and_asks_again(tmp_path: Path) -> None:
    """When a user selects an out-of-range choice (e.g., choice 5 when
    only 2 options exist), the location stays AMBIGUOUS and the system asks again."""
    candidates = [
        _cand("Bhagwanpur, Bihar", 25.75, 84.55),
        _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
    ]
    service, sessions = _service(tmp_path, geocode_candidates=candidates)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    # First turn: ambiguous location
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur", "Bhagwanpur", ValueNormalization.AS_STATED),
    )
    service.send_message(_msg(handle.session_id, slot_updates=updates))

    # Second turn: choose an invalid (out-of-range) choice
    reply2 = service.send_message(_msg(handle.session_id, selected_choice=5))
    # Should ask again for a valid choice (still BLOCKED, still expects CHOICE)
    assert reply2.expects is ExpectedInput.CHOICE
    assert reply2.state is AdvisoryPhase.BLOCKED

    session = sessions.get(handle.session_id)
    # The location slot should still be AMBIGUOUS
    slot = session.slot(SlotName.LOCATION_TEXT)
    from vyaparsarathi.conversation.session_models import SlotState

    assert slot.state is SlotState.AMBIGUOUS
    # The warning about out-of-range choice is recorded in session warnings
    assert any("out of range" in w.lower() for w in session.session_warnings)


def test_natural_language_choice_utterance_resolves_via_llm_extraction_context(
    tmp_path: Path,
) -> None:
    """End-to-end Priority 2 acceptance test: free text ("the first one")
    resolves an ambiguous location because the extraction context told the
    LLM which choices were currently valid — and the backend still
    independently re-validates the resulting selected_choice against the
    session's real live options (CLAUDE.md §3.1: the LLM never bypasses
    validation, it only supplies a candidate the backend then checks)."""
    candidates = [
        _cand("Bhagwanpur, Bihar", 25.75, 84.55),
        _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
    ]
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text=(
                        '{"intent": "provide_info", "slot_updates": ['
                        '{"slot": "proposed_business_text", "raw_text": '
                        '"I want a grocery shop in Bhagwanpur", "value_token": '
                        '"grocery shop", "normalization": "as_stated"}, '
                        '{"slot": "location_text", "raw_text": '
                        '"I want a grocery shop in Bhagwanpur", "value_token": '
                        '"Bhagwanpur", "normalization": "as_stated"}]}'
                    ),
                    prompt_id="extraction",
                ),
                LlmResponse(
                    text='{"intent": "select_candidate", "selected_choice": 1}',
                    prompt_id="extraction",
                ),
            ]
        }
    )
    service, sessions = _service(tmp_path, geocode_candidates=candidates, llm_provider=provider)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )

    reply1 = service.send_message(
        _msg(handle.session_id, text="I want a grocery shop in Bhagwanpur")
    )
    assert reply1.expects is ExpectedInput.CHOICE

    reply2 = service.send_message(_msg(handle.session_id, text="the first one"))
    assert reply2.expects is ExpectedInput.NONE
    assert reply2.state in (AdvisoryPhase.COMPLETE, AdvisoryPhase.ANALYSING)

    session = sessions.get(handle.session_id)
    assert session is not None
    assert session.slot(SlotName.LOCATION_TEXT).value == "Bhagwanpur, Bihar"
    assert any(t.llm_used for t in session.turns)


def test_llm_selected_choice_out_of_range_is_still_rejected_by_the_backend(
    tmp_path: Path,
) -> None:
    """Even if a misbehaving/hallucinating LLM response names a choice
    number outside the currently pending options, the backend's own
    validation (`conversation/deltas.py`) rejects it — the context passed to
    the LLM is a hint, never a trust boundary."""
    candidates = [
        _cand("Bhagwanpur, Bihar", 25.75, 84.55),
        _cand("Bhagwanpur, Uttar Pradesh", 27.0, 80.0),
    ]
    provider = ScriptedLlmProvider(
        {
            "extraction": [
                LlmResponse(
                    text='{"intent": "select_candidate", "selected_choice": 99}',
                    prompt_id="extraction",
                ),
            ]
        }
    )
    service, sessions = _service(tmp_path, geocode_candidates=candidates, llm_provider=provider)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur", "Bhagwanpur", ValueNormalization.AS_STATED),
    )
    service.send_message(_msg(handle.session_id, slot_updates=updates))

    reply2 = service.send_message(_msg(handle.session_id, text="option ninety-nine please"))
    assert reply2.expects is ExpectedInput.CHOICE  # still asking, not corrupted

    from vyaparsarathi.conversation.session_models import SlotState

    session = sessions.get(handle.session_id)
    assert session.slot(SlotName.LOCATION_TEXT).state is SlotState.AMBIGUOUS


def test_devanagari_numeral_short_circuits_without_consuming_a_turn(tmp_path: Path) -> None:
    """A message with a Devanagari numeral never reaches the LLM extractor at
    all — it gets a clear clarification instead, and the turn is not
    consumed (turn_index and session state stay exactly as they were)."""
    provider = ScriptedLlmProvider({})  # must never be called
    service, sessions = _service(tmp_path, llm_provider=provider)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    before = sessions.get(handle.session_id)
    assert before is not None
    turn_index_before = before.turn_index

    reply = service.send_message(_msg(handle.session_id, text="mere paas ५०,००० hain"))
    assert reply.expects is ExpectedInput.FREE_TEXT
    assert "english digits" in reply.messages[0].text.lower()
    assert provider.calls == []  # the LLM was never invoked

    after = sessions.get(handle.session_id)
    assert after is not None
    assert after.turn_index == turn_index_before
    assert after.turns == before.turns


def test_provider_unavailable_gives_a_distinct_message_and_does_not_consume_a_turn(
    tmp_path: Path,
) -> None:
    """When the LLM provider itself cannot be reached (Priority 6), the user
    is told the SERVICE is the problem, not their answer — distinct wording
    from the generic 'I didn't understand', and no turn is consumed (a retry
    sees exactly the same pending state)."""
    provider = ScriptedLlmProvider({})  # empty queue -> LlmUnavailableError on any call
    service, sessions = _service(tmp_path, llm_provider=provider)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    before = sessions.get(handle.session_id)
    assert before is not None
    turn_index_before = before.turn_index

    reply = service.send_message(_msg(handle.session_id, text="I want a grocery shop"))
    assert reply.expects is ExpectedInput.FREE_TEXT
    assert "temporary problem" in reply.messages[0].text.lower()
    assert "didn't understand" not in reply.messages[0].text.lower()

    after = sessions.get(handle.session_id)
    assert after is not None
    assert after.turn_index == turn_index_before
    assert after.turns == before.turns


def test_unparseable_response_uses_the_normal_clarification_not_the_provider_message(
    tmp_path: Path,
) -> None:
    """A reachable provider that never returns usable JSON (UNPARSEABLE)
    behaves exactly as before this change — the normal pending-question
    clarification, never the provider-unavailable wording — since the
    service itself is not the problem in this case."""
    provider = ScriptedLlmProvider(
        {
            "extraction": [LlmResponse(text="not json", prompt_id="extraction")],
            "repair": [LlmResponse(text="still not json", prompt_id="repair")],
        }
    )
    service, sessions = _service(tmp_path, llm_provider=provider)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    reply = service.send_message(_msg(handle.session_id, text="asdkjfh qwoeiru"))
    assert "temporary problem" not in reply.messages[0].text.lower()

    session = sessions.get(handle.session_id)
    assert session is not None
    assert session.turn_index == 1  # this turn WAS consumed, unlike the provider-outage case


class _RaisingGeocoder:
    """Duck-types `.geocode()` but always raises, simulating a hard geocoding
    failure (e.g. the Nominatim 403 this fix addresses) rather than the
    `LOCATION_AMBIGUOUS`/`NO_RESULTS` paths other tests already cover."""

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        from vyaparsarathi.errors import GeocodingError

        raise GeocodingError(f"Nominatim returned HTTP 403 for {query!r}")


def test_geocoding_failure_stops_the_pipeline_with_an_explicit_error(tmp_path: Path) -> None:
    """A geocoder that raises `GeocodingError` (e.g. the Nominatim 403 this
    fix addresses) is caught inside `discovery/service.py::discover()`, which
    converts it (never crashes, never fabricates — CLAUDE.md §6.1) into
    `DiscoveryResult(status=LOCATION_NOT_FOUND)`. The advisory pipeline must
    stop there: no market, opportunity, or recommendation artifact is ever
    produced from that failed prerequisite, and the reply must never read
    COMPLETE/ANALYSING."""
    service, sessions = _service(tmp_path, geocoder=_RaisingGeocoder())
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    updates = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Nowhereville", "Nowhereville", ValueNormalization.AS_STATED),
    )
    reply = service.send_message(_msg(handle.session_id, slot_updates=updates))

    assert reply.state is AdvisoryPhase.FAILED
    assert reply.state not in (AdvisoryPhase.COMPLETE, AdvisoryPhase.ANALYSING)
    assert len(reply.messages) == 1
    assert "location" in reply.messages[0].text.lower()

    session = sessions.get(handle.session_id)
    assert session is not None
    assert StepId.DISCOVER in session.artifacts  # kept, never discarded
    for blocked in (
        StepId.ANALYZE,
        StepId.METRICS,
        StepId.DEMAND_EVIDENCE,
        StepId.OPPORTUNITY_EVIDENCE,
        StepId.OPPORTUNITY,
        StepId.RECOMMEND,
        StepId.SWOT,
    ):
        assert blocked not in session.artifacts


def test_acceptance_scenario_assets_with_structured_detail_and_no_invented_money(
    tmp_path: Path,
) -> None:
    """The task's exact assets scenario: 'Yes, I have 2 cows, one 100 sq ft
    shop, and a bike.' -> supported asset kinds recorded (livestock,
    storefront, vehicle), original text retained for provenance, structured
    detail preserved as notes, and — critically — no monetary value is ever
    invented from a physical asset."""
    from vyaparsarathi.conversation.understanding import AssetUpdateInput

    service, sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    raw = "Yes, I have 2 cows, one 100 sq ft shop, and a bike."
    asset_update = AssetUpdateInput(
        items=(AssetKind.LIVESTOCK, AssetKind.STOREFRONT, AssetKind.VEHICLE),
        raw_text=raw,
        notes=("2 cows", "100 sq ft shop"),
    )
    service.send_message(_msg(handle.session_id, asset_update=asset_update))

    session = sessions.get(handle.session_id)
    assert session is not None
    assert session.assets.current.items == frozenset(
        {AssetKind.LIVESTOCK, AssetKind.STOREFRONT, AssetKind.VEHICLE}
    )
    assert session.assets.current.raw_text == raw
    assert session.assets.current.notes == ("2 cows", "100 sq ft shop")
    # No slot anywhere was given a monetary value by this turn — an asset
    # mention must never be silently turned into cash.
    assert session.slot(SlotName.LIQUID_CASH_INR).value is None
    assert session.slot(SlotName.PROMOTER_CASH_CONTRIBUTION_INR).value is None


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


def test_snapshot_carries_structure_and_swot_artifacts_with_the_shipped_default(
    tmp_path: Path,
) -> None:
    """Tier 1: STRUCTURE_FINANCE and SWOT are part of the real DAG and must
    reach a complete session's snapshot. The shipped default now declares
    both SIH bands (`config/sih_scheme.py::DEFAULT_SIH_SCHEME_TABLE`), so a
    plan with a real, derivable project cost structures a real split —
    `test_structure_to_finance.py` covers the explicit NOT_CONFIGURED
    degrade path (an empty table) separately."""
    from vyaparsarathi.conversation.session_models import StepId

    service, _ = _service(tmp_path)
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
        monthly_revenue_inr=("40000", "40000", ValueNormalization.AS_STATED),
        cogs_pct=("70%", "70%", ValueNormalization.PERCENT_TO_RATIO),
        project_cost_inr=("3 lakh", "3 lakh", ValueNormalization.LAKH_TO_INR),
        fixed_opex_inr=("4000", "4000", ValueNormalization.AS_STATED),
    )
    service.send_message(_msg(handle.session_id, slot_updates=updates))
    snap = service.snapshot(handle.session_id)
    assert snap is not None
    assert StepId.STRUCTURE_FINANCE.value in snap.artifacts
    assert StepId.SWOT.value in snap.artifacts
    structure_payload = snap.artifacts[StepId.STRUCTURE_FINANCE.value]["payload"]
    assert structure_payload["status"] == "structured"
    assert structure_payload["scheme_name"] == "Term Loan"


def test_correcting_cash_invalidates_structure_and_swot_without_rerunning_impure_steps(
    tmp_path: Path,
) -> None:
    from vyaparsarathi.conversation.session_models import StepId

    service, sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    first = _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR),
        monthly_revenue_inr=("40000", "40000", ValueNormalization.AS_STATED),
        cogs_pct=("70%", "70%", ValueNormalization.PERCENT_TO_RATIO),
        project_cost_inr=("3 lakh", "3 lakh", ValueNormalization.LAKH_TO_INR),
        fixed_opex_inr=("4000", "4000", ValueNormalization.AS_STATED),
    )
    service.send_message(_msg(handle.session_id, slot_updates=first))

    correction = _provide_info(
        liquid_cash_inr=("actually only 4 lakh", "4 lakh", ValueNormalization.LAKH_TO_INR)
    )
    service.send_message(_msg(handle.session_id, slot_updates=correction))

    session = sessions.get(handle.session_id)
    last_turn = session.turns[-1]
    assert StepId.STRUCTURE_FINANCE in last_turn.steps_invalidated
    assert StepId.SWOT in last_turn.steps_invalidated
    assert StepId.DISCOVER not in last_turn.steps_invalidated


# --- Phase B: the NORMAL-mode collect-then-deliver UX ----------------------


def test_normal_mode_transcript_collects_then_delivers_one_advisory(tmp_path: Path) -> None:
    """The exact desired transcript: business -> location -> assets ->
    trade experience -> Available Margin Capital -> the four viability
    drivers (revenue, margin, project cost, fixed opex), one turn each, with
    NO market stance / SWOT / recommendation shown before the last turn —
    then ONE consolidated advisory."""
    service, sessions = _service(tmp_path, mode=ConversationMode.NORMAL)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )

    def _turn(**kwargs: object) -> str:
        reply = service.send_message(_msg(handle.session_id, **kwargs))
        return " ".join(m.text for m in reply.messages)

    r1 = _turn(
        slot_updates=_provide_info(
            proposed_business_text=(
                "I want to open a pulses grocery store",
                "I want to open a pulses grocery store",
                ValueNormalization.AS_STATED,
            )
        )
    )
    assert "location" in r1.lower() or "where" in r1.lower()
    assert "market" not in r1.lower() and "swot" not in r1.lower()

    r2 = _turn(
        slot_updates=_provide_info(
            location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED)
        )
    )
    assert "asset" in r2.lower() or "shop" in r2.lower()
    assert "market" not in r2.lower() and "swot" not in r2.lower()

    r3 = _turn(
        asset_update=AssetUpdateInput(items=(AssetKind.STOREFRONT,), raw_text="I own a shop")
    )
    assert "experience" in r3.lower() or "trade" in r3.lower()
    assert "market" not in r3.lower() and "swot" not in r3.lower()

    r4 = _turn(experience_declined=True)
    assert "years" in r4.lower() or "experience" in r4.lower()
    assert "market" not in r4.lower() and "swot" not in r4.lower()

    r4b = _turn(declined_slots=(SlotName.YEARS_EXPERIENCE,))
    assert "cash" in r4b.lower() or "margin capital" in r4b.lower()
    assert "market" not in r4b.lower() and "swot" not in r4b.lower()

    r5 = _turn(
        slot_updates=_provide_info(
            liquid_cash_inr=("I have 1 lakh", "1 lakh", ValueNormalization.LAKH_TO_INR)
        )
    )
    # Tier-A is now complete, but the four viability drivers are still
    # unasked — rung 7 asks about them one at a time rather than delivering
    # an "insufficient evidence" result with the same questions listed.
    assert "sell" in r5.lower() or "revenue" in r5.lower()
    assert "market" not in r5.lower() and "swot" not in r5.lower()

    r6 = _turn(
        slot_updates=_provide_info(
            monthly_revenue_inr=("40000", "40000", ValueNormalization.AS_STATED)
        )
    )
    assert "cost of goods" in r6.lower() or "margin" in r6.lower()
    assert "market" not in r6.lower() and "swot" not in r6.lower()

    r7 = _turn(
        slot_updates=_provide_info(cogs_pct=("70%", "70%", ValueNormalization.PERCENT_TO_RATIO))
    )
    assert "set up" in r7.lower() or "cost" in r7.lower()
    assert "market" not in r7.lower() and "swot" not in r7.lower()

    r8 = _turn(
        slot_updates=_provide_info(
            project_cost_inr=("3 lakh", "3 lakh", ValueNormalization.LAKH_TO_INR)
        )
    )
    assert "fixed cost" in r8.lower() or "rent" in r8.lower()
    assert "market" not in r8.lower() and "swot" not in r8.lower()

    reply9 = service.send_message(
        _msg(
            handle.session_id,
            slot_updates=_provide_info(
                fixed_opex_inr=("4000", "4000", ValueNormalization.AS_STATED)
            ),
        )
    )
    final_text = " ".join(m.text for m in reply9.messages)
    assert "market stance" in final_text.lower()
    assert "recommendation" in final_text.lower()

    # The whole DAG has still run in the background the entire time — the
    # gate only ever withheld the DISPLAY, never the underlying evidence.
    session = sessions.get(handle.session_id)
    assert session is not None
    assert StepId.RECOMMEND in session.artifacts
    assert StepId.SCHEME_CAPACITY in session.artifacts
    capacity_payload = session.artifacts[StepId.SCHEME_CAPACITY].payload
    assert capacity_payload["status"] == "calculated"


def test_normal_mode_never_shows_an_advisory_before_tier_a_is_complete(tmp_path: Path) -> None:
    service, _sessions = _service(tmp_path, mode=ConversationMode.NORMAL)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    reply = service.send_message(
        _msg(
            handle.session_id,
            slot_updates=_provide_info(
                proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED)
            ),
        )
    )
    assert reply.state is AdvisoryPhase.COLLECTING
    assert "recommendation" not in reply.narrative.sections
    assert "market" not in reply.narrative.sections
    assert "swot" not in reply.narrative.sections


def test_developer_mode_still_delivers_partial_advisories_every_turn(tmp_path: Path) -> None:
    """The pre-Phase-B behaviour, explicitly still available via DEVELOPER
    mode (the default `_service()` uses): once business + location are
    given, the very next turn already shows a full (if evidence-incomplete)
    advisory, unlike NORMAL mode's collect-first gate."""
    service, _sessions = _service(tmp_path)  # mode defaults to DEVELOPER
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    reply = service.send_message(
        _msg(
            handle.session_id,
            slot_updates=_provide_info(
                proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
                location_text=(
                    "Bhagwanpur, Bihar",
                    "Bhagwanpur, Bihar",
                    ValueNormalization.AS_STATED,
                ),
            ),
        )
    )
    assert "recommendation" in reply.narrative.sections


# --- Phase 6 polish: the explicit "would you like a PDF report?" offer -----


def _full_slot_updates() -> tuple:
    return _provide_info(
        proposed_business_text=("grocery", "grocery", ValueNormalization.AS_STATED),
        location_text=("Bhagwanpur, Bihar", "Bhagwanpur, Bihar", ValueNormalization.AS_STATED),
        liquid_cash_inr=("I have 6.5 lakh", "6.5 lakh", ValueNormalization.LAKH_TO_INR),
        monthly_revenue_inr=("40000", "40000", ValueNormalization.AS_STATED),
        cogs_pct=("70%", "70%", ValueNormalization.PERCENT_TO_RATIO),
        project_cost_inr=("3 lakh", "3 lakh", ValueNormalization.LAKH_TO_INR),
        fixed_opex_inr=("4000", "4000", ValueNormalization.AS_STATED),
    )


def test_delivered_advisory_asks_whether_to_generate_a_report(tmp_path: Path) -> None:
    service, _sessions = _service(tmp_path)  # DEVELOPER mode: delivers this turn
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    reply = service.send_message(_msg(handle.session_id, slot_updates=_full_slot_updates()))
    assert reply.report is None  # no report intent classified on the delivery turn itself
    assert "report_offer" in reply.narrative.sections
    assert "PDF" in reply.narrative.sections["report_offer"]


def test_affirmative_reply_after_delivery_requests_the_report(tmp_path: Path) -> None:
    service, _sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    service.send_message(_msg(handle.session_id, slot_updates=_full_slot_updates()))

    reply = service.send_message(_msg(handle.session_id, text="yes, generate the report"))
    assert reply.report is not None
    assert reply.report.status is ReportStatus.REQUESTED
    assert "preparing" in " ".join(m.text for m in reply.messages).lower()


def test_decline_reply_after_delivery_does_not_request_the_report(tmp_path: Path) -> None:
    service, _sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    service.send_message(_msg(handle.session_id, slot_updates=_full_slot_updates()))

    reply = service.send_message(_msg(handle.session_id, text="not now"))
    assert reply.report is not None
    assert reply.report.status is ReportStatus.DECLINED


def test_report_intent_never_fires_mid_collection(tmp_path: Path) -> None:
    """A bare "yes" before an advisory has been delivered is not a report
    request — nothing in `readiness`/`planner` is ever waiting on it, so it
    must fall through to the normal (here: unclear-intent) handling."""
    service, _sessions = _service(tmp_path, mode=ConversationMode.NORMAL)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    reply = service.send_message(_msg(handle.session_id, text="yes"))
    assert reply.report is None


def test_report_intent_does_not_consume_a_turn_or_mutate_the_session(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    )
    service.send_message(_msg(handle.session_id, slot_updates=_full_slot_updates()))
    turn_index_before = sessions.get(handle.session_id).turn_index

    service.send_message(_msg(handle.session_id, text="yes please, make a pdf"))

    turn_index_after = sessions.get(handle.session_id).turn_index
    assert turn_index_after == turn_index_before


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
