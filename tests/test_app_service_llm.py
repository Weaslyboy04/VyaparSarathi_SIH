"""`app/service.py::AdvisoryService` on the LLM path (CLAUDE.md §3.1, §25
Phase 6): a free-text turn parsed by a *scripted* `LlmProvider`, then the
same deterministic Phase 1-5 pipeline underneath. No test here calls Gemini
or any network — the LLM is `llm/fake.py::ScriptedLlmProvider`, every engine
is a fake, and the knowledge corpus is empty on disk.

What these tests pin (the four non-negotiables from the Phase 6 brief):

* tool/engine numbers are preserved in the reply, never re-authored;
* the LLM cannot apply a number that is not a verbatim pointer into its own
  `raw_text` — a fabricated token is dropped, the slot stays MISSING;
* missing RAG evidence is disclosed (zero parameters resolve), never
  papered over with an invented interest rate;
* an unparseable / unclear turn degrades to exactly one planner question.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from vyaparsarathi.app.dto import (
    AdvisoryPhase,
    ChannelId,
    ExpectedInput,
    MessageRequest,
    StartSessionRequest,
)
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.conversation_config import ConversationMode
from vyaparsarathi.conversation.session_models import SlotName, SlotState, StepId
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

# --- fakes (same duck-typed style as tests/test_app_service.py) -----------


class _FakeGeocoder:
    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                display_name="Bhagwanpur, Vaishali, Bihar",
                latitude=25.75,
                longitude=84.55,
                importance=0.4,
                district="Vaishali",
                state="Bihar",
                country="India",
            )
        ]


class _FakeSource:
    name = SourceName.OSM

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        elements = [
            RawOsmElement(
                element_type="node",
                element_id=1,
                latitude=25.751,
                longitude=84.551,
                tags={"shop": "convenience", "name": "Sharma Kirana Store"},
                raw={"type": "node", "id": 1},
            )
        ]
        return OverpassFetch(
            elements=elements,
            raw_count=len(elements),
            dropped_no_coordinates=0,
            endpoint_used="offline-fixture",
            mirror_fallback_used=False,
            query_ql="[out:json];",
        )


class _FakeOverpassClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        return [], "offline-fixture", False


def _service(
    tmp_path: Path,
    *,
    responses: dict[str, list[LlmResponse]],
    mode: ConversationMode = ConversationMode.DEVELOPER,
) -> tuple[AdvisoryService, InMemorySessionRepository]:
    settings = Settings(
        cache_enabled=False,
        census_villages_path=str(tmp_path / "no-such-file.csv.gz"),
    )
    repo = InMemoryBusinessRepository()
    discovery_service = DiscoveryService(
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        source=_FakeSource(),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    empty_corpus = tmp_path / "knowledge"
    empty_corpus.mkdir(exist_ok=True)
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery_service,
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=FileCorpusStore(empty_corpus),
        repository=repo,
        retriever=None,
        mode=mode,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        llm_provider=ScriptedLlmProvider(responses),
    )
    sessions = InMemorySessionRepository()
    return AdvisoryService(sessions=sessions, runtime=runtime), sessions


def _extraction(payload: dict[str, object]) -> dict[str, list[LlmResponse]]:
    return {"extraction": [LlmResponse(text=json.dumps(payload), prompt_id="extraction")]}


_HAPPY_MESSAGE = "I want to open a grocery shop in Bhagwanpur, Bihar and I have 6.5 lakh"
_HAPPY_EXTRACTION = {
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


def _start(service: AdvisoryService) -> str:
    return service.start_session(
        StartSessionRequest(channel=ChannelId.TEST, started_at=datetime.now(UTC))
    ).session_id


def _send(service: AdvisoryService, session_id: str, text: str) -> object:
    return service.send_message(
        MessageRequest(
            session_id=session_id,
            text=text,
            channel=ChannelId.TEST,
            received_at=datetime.now(UTC),
        )
    )


def test_free_text_turn_reaches_a_recommendation_via_scripted_llm(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path, responses=_extraction(_HAPPY_EXTRACTION))
    session_id = _start(service)

    reply = _send(service, session_id, _HAPPY_MESSAGE)

    assert reply.state is AdvisoryPhase.COMPLETE
    session = sessions.get(session_id)
    assert session is not None
    assert session.turns[-1].llm_used is True
    assert StepId.RECOMMEND in session.artifacts
    # the extracted cash figure was re-derived deterministically, not trusted
    assert session.slot(SlotName.LIQUID_CASH_INR).value == 650000


def test_engine_numbers_are_preserved_not_reauthored(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, responses=_extraction(_HAPPY_EXTRACTION))
    session_id = _start(service)
    _send(service, session_id, _HAPPY_MESSAGE)

    snap = service.snapshot(session_id)
    assert snap is not None
    capacity = snap.artifacts[StepId.SCHEME_CAPACITY.value]["payload"]
    # SCHEME_CAPACITY is a pure finance/capacity.py output derived from the
    # ₹6.5L margin capital alone — exact arithmetic, produced before any LLM
    # ran (10% promoter margin on a ₹50L feasible cost, 90% indicated loan).
    assert capacity["status"] == "calculated"
    assert Decimal(capacity["margin_capital_inr"]) == Decimal("650000")
    assert Decimal(capacity["required_promoter_margin_inr"]) == Decimal("500000")
    assert Decimal(capacity["indicated_loan_inr"]) == Decimal("4500000")


def test_llm_cannot_apply_a_number_absent_from_its_own_raw_text(tmp_path: Path) -> None:
    payload = {
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
                "raw_text": "Bhagwanpur, Bihar",
                "value_token": "Bhagwanpur, Bihar",
                "normalization": "as_stated",
            },
            {
                # fabricated: "50 lakh" never appears in raw_text
                "slot": "liquid_cash_inr",
                "raw_text": "I have some savings put aside",
                "value_token": "50 lakh",
                "normalization": "lakh_to_inr",
            },
        ],
    }
    service, sessions = _service(
        tmp_path, responses=_extraction(payload), mode=ConversationMode.NORMAL
    )
    session_id = _start(service)
    _send(service, session_id, "a grocery shop in Bhagwanpur, Bihar, I have some savings put aside")

    session = sessions.get(session_id)
    assert session is not None
    # the bad update was dropped; the slot never took a value
    assert session.slot(SlotName.LIQUID_CASH_INR).state is SlotState.MISSING
    dropped = " ".join(session.turns[-1].warnings)
    assert "liquid_cash_inr" in dropped and "50 lakh" in dropped
    # the two well-formed updates in the same turn still applied
    assert session.slot(SlotName.PROPOSED_BUSINESS_TEXT).state is SlotState.USER_PROVIDED
    assert session.slot(SlotName.LOCATION_TEXT).state is SlotState.USER_PROVIDED


def test_reply_prose_is_always_the_deterministic_template(tmp_path: Path) -> None:
    service, _ = _service(tmp_path, responses=_extraction(_HAPPY_EXTRACTION))
    session_id = _start(service)
    reply = _send(service, session_id, _HAPPY_MESSAGE)

    # every rendered section is template-authored — no LLM narrative is wired
    # into send_message yet (docs/phase-6.md), so prose cannot carry a fact.
    assert reply.narrative.generated_by
    assert set(reply.narrative.generated_by.values()) == {"template"}
    blob = " ".join(m.text for m in reply.messages).lower()
    assert "interest rate" not in blob
    assert "per annum" not in blob


def test_missing_rag_evidence_is_disclosed_never_invented(tmp_path: Path) -> None:
    service, sessions = _service(tmp_path, responses=_extraction(_HAPPY_EXTRACTION))
    session_id = _start(service)
    _send(service, session_id, _HAPPY_MESSAGE)

    session = sessions.get(session_id)
    assert session is not None
    knowledge = session.artifacts.get(StepId.FINANCE_KNOWLEDGE)
    assert knowledge is not None
    resolutions = knowledge.payload.get("resolutions", [])
    # empty corpus on disk -> not a single scheme parameter resolves
    assert resolutions
    assert all(r.get("status") != "resolved" for r in resolutions)


def test_unclear_extraction_degrades_to_one_planner_question(tmp_path: Path) -> None:
    service, sessions = _service(
        tmp_path, responses=_extraction({"intent": "unclear"}), mode=ConversationMode.NORMAL
    )
    session_id = _start(service)
    reply = _send(service, session_id, "uhh I'm not sure what I want to do")

    assert reply.state is AdvisoryPhase.COLLECTING
    assert reply.expects is ExpectedInput.FREE_TEXT
    # exactly one question, nothing analysed yet
    assert len([m for m in reply.messages if m.text.strip()]) == 1
    session = sessions.get(session_id)
    assert session is not None
    assert StepId.RECOMMEND not in session.artifacts


def test_repair_path_then_success_still_runs_the_pipeline(tmp_path: Path) -> None:
    service, sessions = _service(
        tmp_path,
        responses={
            "extraction": [LlmResponse(text="not json at all", prompt_id="extraction")],
            "repair": [LlmResponse(text=json.dumps(_HAPPY_EXTRACTION), prompt_id="repair")],
        },
    )
    session_id = _start(service)
    reply = _send(service, session_id, _HAPPY_MESSAGE)

    assert reply.state is AdvisoryPhase.COMPLETE
    session = sessions.get(session_id)
    assert session is not None
    assert StepId.RECOMMEND in session.artifacts


# --- Phase 6 polish: one natural, multi-fact transcript to a full advisory -


_MULTI_FACT_FIRST_MESSAGE = (
    "I want to open a grocery shop in Bhagwanpur, Bihar. I own two bikes, "
    "two cows, and a 100 sq ft shop on the main road. I have ₹6.5 lakh capital."
)
_MULTI_FACT_FIRST_EXTRACTION = {
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
            "raw_text": "I have ₹6.5 lakh capital",
            "value_token": "6.5 lakh",
            "normalization": "lakh_to_inr",
        },
    ],
    "asset_update": {
        "items": ["vehicle", "livestock", "storefront"],
        "raw_text": "I own two bikes, two cows, and a 100 sq ft shop on the main road",
        "notes": ["two bikes", "two cows", "100 sq ft shop on the main road"],
    },
}


def test_normal_mode_multi_fact_first_message_only_asks_for_what_remains(
    tmp_path: Path,
) -> None:
    """The exact scenario the Phase 6 UX brief describes: one message states
    business, location, cash, and three separate asset kinds (with their
    original notes preserved) in one go. NORMAL mode must fold all of it into
    the session from a single LLM call and then ask only for what is
    genuinely still missing — never re-asking about assets, and never
    presenting a partial "insufficient evidence" result before the
    viability drivers have even been asked about."""
    service, sessions = _service(
        tmp_path,
        mode=ConversationMode.NORMAL,
        responses=_extraction(_MULTI_FACT_FIRST_EXTRACTION),
    )
    session_id = _start(service)

    reply = _send(service, session_id, _MULTI_FACT_FIRST_MESSAGE)

    # Business, location, cash and all three assets were extracted from the
    # one message — the only Tier-A item left is trade experience.
    assert "experience" in " ".join(m.text for m in reply.messages).lower()

    session = sessions.get(session_id)
    assert session is not None
    assert session.slot(SlotName.PROPOSED_BUSINESS_TEXT).value == "open a grocery shop"
    assert session.slot(SlotName.LOCATION_TEXT).value == "Bhagwanpur, Bihar"
    assert session.slot(SlotName.LIQUID_CASH_INR).value == 650000
    from vyaparsarathi.models.profile import AssetKind

    assert session.assets.current.items == frozenset(
        {AssetKind.VEHICLE, AssetKind.LIVESTOCK, AssetKind.STOREFRONT}
    )
    assert set(session.assets.current.notes) == {
        "two bikes",
        "two cows",
        "100 sq ft shop on the main road",
    }


def test_normal_mode_full_transcript_reaches_one_advisory_then_offers_a_report(
    tmp_path: Path,
) -> None:
    """The full natural transcript: one multi-fact message, a declined
    experience question, the four viability drivers one at a time, then —
    and only then — one consolidated advisory that offers a PDF report.
    A subsequent "yes" (no LLM call needed — `report_intent.py` is
    deterministic) is what actually requests the report."""
    responses = {
        "extraction": [
            LlmResponse(text=json.dumps(_MULTI_FACT_FIRST_EXTRACTION), prompt_id="extraction"),
            LlmResponse(
                text=json.dumps({"intent": "decline_slot", "experience_declined": True}),
                prompt_id="extraction",
            ),
            LlmResponse(
                text=json.dumps(
                    {"intent": "decline_slot", "declined_slots": ["years_experience"]}
                ),
                prompt_id="extraction",
            ),
            LlmResponse(
                text=json.dumps(
                    {
                        "intent": "provide_info",
                        "slot_updates": [
                            {
                                "slot": "monthly_revenue_inr",
                                "raw_text": "about 40000 a month",
                                "value_token": "40000",
                                "normalization": "as_stated",
                            }
                        ],
                    }
                ),
                prompt_id="extraction",
            ),
            LlmResponse(
                text=json.dumps(
                    {
                        "intent": "provide_info",
                        "slot_updates": [
                            {
                                "slot": "cogs_pct",
                                "raw_text": "around 70%",
                                "value_token": "70%",
                                "normalization": "percent_to_ratio",
                            }
                        ],
                    }
                ),
                prompt_id="extraction",
            ),
            LlmResponse(
                text=json.dumps(
                    {
                        "intent": "provide_info",
                        "slot_updates": [
                            {
                                "slot": "project_cost_inr",
                                "raw_text": "3 lakh",
                                "value_token": "3 lakh",
                                "normalization": "lakh_to_inr",
                            }
                        ],
                    }
                ),
                prompt_id="extraction",
            ),
            LlmResponse(
                text=json.dumps(
                    {
                        "intent": "provide_info",
                        "slot_updates": [
                            {
                                "slot": "fixed_opex_inr",
                                "raw_text": "4000",
                                "value_token": "4000",
                                "normalization": "as_stated",
                            }
                        ],
                    }
                ),
                prompt_id="extraction",
            ),
        ]
    }
    service, sessions = _service(tmp_path, mode=ConversationMode.NORMAL, responses=responses)
    session_id = _start(service)

    r1 = _send(service, session_id, _MULTI_FACT_FIRST_MESSAGE)
    assert "experience" in " ".join(m.text for m in r1.messages).lower()

    r2 = _send(service, session_id, "no, I don't have experience in this")
    assert "experience" in " ".join(m.text for m in r2.messages).lower()

    r2b = _send(service, session_id, "none, I'm starting fresh")
    assert "sell" in " ".join(m.text for m in r2b.messages).lower()

    r3 = _send(service, session_id, "I expect to sell about 40000 a month")
    assert "cost of goods" in " ".join(m.text for m in r3.messages).lower()

    r4 = _send(service, session_id, "around 70% goes to cost of goods")
    assert "set up" in " ".join(m.text for m in r4.messages).lower()

    r5 = _send(service, session_id, "it'll cost about 3 lakh to set up")
    assert "fixed cost" in " ".join(m.text for m in r5.messages).lower()

    r6 = _send(service, session_id, "fixed costs are about 4000 a month")
    final_text = " ".join(m.text for m in r6.messages)
    assert r6.state is AdvisoryPhase.COMPLETE
    assert "recommendation" in final_text.lower()
    assert "Would you like me to generate a PDF project report?" in final_text
    assert r6.report is None  # not yet requested

    r7 = _send(service, session_id, "yes, generate the report")
    assert r7.report is not None
    from vyaparsarathi.app.dto import ReportStatus

    assert r7.report.status is ReportStatus.REQUESTED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
