"""Phase 6 chat harness (CLAUDE.md §25 Phase 6). A thin, channel-neutral REPL
around `AdvisoryService` — the smallest test/demo interface for exercising
the real, already-wired Phase 1-6 backend across multiple turns.

This is a TEST/DEMO CHANNEL, not a replacement backend: it builds
`MessageRequest`s and prints `AdvisoryReply`s. It imports no engine from
`market/`, `finance/`, `knowledge/`, or `conversation/` — every domain
decision is made by `AdvisoryService.send_message`, exactly as
`scripts/phase6_demo.py` and `tests/test_app_service.py` already exercise it.
No second orchestration path exists here.

Two input paths, both honest about CLAUDE.md §3.1 (no LLM => no arbitrary
free-text extraction attempted here or anywhere in this repository):

* **Structured (default).** `key: value` lines map directly onto one
  `SlotUpdateInput` each — the same deterministic, `llm_enabled=False` path
  `phase6_demo.py` runs. A bare line with no recognised key is sent as-is;
  the planner then asks its own clarifying question rather than this script
  guessing what it meant.
* **`--llm`.** Sends the raw line as free text and lets
  `llm/structured.py::extract_understanding` parse it — requires
  `VYAPAR_LLM_ENABLED=true` and `VYAPAR_LLM_BASE_URL` in the environment.
  Fails fast with a clear error if they are not set, rather than silently
  falling back to the structured path.

Run:
    ./.venv/Scripts/python.exe scripts/phase6_chat.py            # live discovery
    ./.venv/Scripts/python.exe scripts/phase6_chat.py --offline  # no network
    ./.venv/Scripts/python.exe scripts/phase6_chat.py --llm      # + LLM extraction

Commands: /reset  /state  /snapshot  /structure  /swot  /help  /quit
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from vyaparsarathi.app.dto import (
    AdvisoryReply,
    AdvisorySnapshot,
    ChannelId,
    MessageRequest,
    StartSessionRequest,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.runtime import AdvisoryRuntime, build_default_runtime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.conversation.session_models import SlotName, StepId
from vyaparsarathi.conversation.understanding import SlotUpdateInput
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.llm.provider import HttpLlmProvider
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.models import RawOsmElement

# --- offline fakes (same style/data as scripts/phase6_demo.py and
# tests/test_app_service.py — each demo/test script owns its own small
# fakes rather than cross-importing another script, which breaks when this
# file is invoked directly: `python scripts/phase6_chat.py` puts scripts/
# itself, not the repo root, on sys.path) --------------------------------


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
            ),
            RawOsmElement(
                element_type="node",
                element_id=2,
                latitude=25.760,
                longitude=84.560,
                tags={"shop": "supermarket", "name": "Bhagwanpur Bazaar"},
                raw={"type": "node", "id": 2},
            ),
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

    def close(self) -> None:
        """`AdvisoryRuntime.close()` calls `overpass_client.close()`
        unconditionally (unlike `geocoder`/`llm_provider`, which it checks
        for the attribute first) — the real `OverpassClient` has one; this
        fake must too."""


# --- key: value -> SlotUpdateInput -----------------------------------------

_TEXT_SLOTS: dict[str, SlotName] = {
    "business": SlotName.PROPOSED_BUSINESS_TEXT,
    "location": SlotName.LOCATION_TEXT,
}
_MONEY_SLOTS: dict[str, SlotName] = {
    "cash": SlotName.LIQUID_CASH_INR,
    "contribution": SlotName.PROMOTER_CASH_CONTRIBUTION_INR,
    "revenue": SlotName.MONTHLY_REVENUE_INR,
    "cost": SlotName.PROJECT_COST_INR,
    "opex": SlotName.FIXED_OPEX_INR,
    "loan": SlotName.LOAN_PRINCIPAL_INR,
}
_PERCENT_RATIO_SLOTS: dict[str, SlotName] = {"cogs": SlotName.COGS_PCT}
_PERCENT_RATE_SLOTS: dict[str, SlotName] = {"rate": SlotName.LOAN_INTEREST_RATE_PCT}
_MONTHS_SLOTS: dict[str, SlotName] = {
    "tenure": SlotName.LOAN_TENURE_MONTHS,
    "moratorium": SlotName.LOAN_MORATORIUM_MONTHS,
}
_COUNT_SLOTS: dict[str, SlotName] = {
    "radius": SlotName.RADIUS_M,  # metres, per the slot's own stored unit
    "experience": SlotName.YEARS_EXPERIENCE,
}

_HELP = """\
Structured commands (each maps to exactly one stated slot — nothing here is
guessed or extracted from prose):
  business: <text>          proposed business
  location: <text>          e.g. "Bhagwanpur, Bihar"
  cash: <amount>             liquid cash, e.g. "6.5 lakh" or "650000"
  contribution: <amount>     promoter cash contribution
  revenue: <amount>          expected monthly revenue
  cogs: <pct>                cost of goods, e.g. "70%" or "0.70"
  cost: <amount>             one-time project/setup cost
  opex: <amount>             monthly fixed operating costs
  loan: <amount>              requested loan principal
  rate: <pct>                loan interest rate, e.g. "11%"
  tenure: <months>           loan tenure, e.g. "60" or "5 years"
  moratorium: <months>       moratorium period
  radius: <metres>           discovery/catchment radius in metres
  experience: <years>        years of relevant experience
  <N>                        answer a numbered disambiguation (just type the number)
Session commands:
  /reset       start a brand-new session
  /state       print slot values and which steps have run
  /snapshot    print the full AdvisorySnapshot (Phase 8 DPR contract) as JSON
  /structure   print the STRUCTURE_FINANCE artifact, if any
  /swot        print the SWOT artifact, if any
  /help        this text
  /quit        exit
Any other line is sent as free text — without --llm, the planner will ask
its own clarifying question rather than this script guessing what it means.
"""


def _slot_update(name: SlotName, raw_text: str, norm: ValueNormalization) -> SlotUpdateInput:
    return SlotUpdateInput(slot=name, raw_text=raw_text, value_token=raw_text, normalization=norm)


def _money_norm(text: str) -> ValueNormalization:
    lowered = text.lower()
    if "crore" in lowered:
        return ValueNormalization.CRORE_TO_INR
    if "lakh" in lowered or "lac" in lowered:
        return ValueNormalization.LAKH_TO_INR
    return ValueNormalization.AS_STATED


def _percent_ratio_norm(text: str) -> ValueNormalization:
    return ValueNormalization.PERCENT_TO_RATIO if "%" in text else ValueNormalization.AS_STATED


def _percent_rate_norm(text: str) -> ValueNormalization:
    return (
        ValueNormalization.PERCENT_AS_ANNUAL_RATE if "%" in text else ValueNormalization.AS_STATED
    )


def _months_norm(text: str) -> ValueNormalization:
    return (
        ValueNormalization.YEARS_TO_MONTHS
        if "year" in text.lower()
        else ValueNormalization.AS_STATED
    )


class _ParsedInput:
    __slots__ = ("slot_updates", "declined_slots", "selected_choice", "text")

    def __init__(
        self,
        slot_updates: tuple[SlotUpdateInput, ...] = (),
        declined_slots: tuple[SlotName, ...] = (),
        selected_choice: int | None = None,
        text: str = "",
    ) -> None:
        self.slot_updates = slot_updates
        self.declined_slots = declined_slots
        self.selected_choice = selected_choice
        self.text = text


def _parse_input(line: str) -> _ParsedInput:
    """`key: value` -> exactly one `SlotUpdateInput`; `pick N` or just `N` -> a choice
    selection; `decline: <slot>` -> a declined slot; anything else is
    returned verbatim as free text (never interpreted here)."""
    stripped = line.strip()
    lowered = stripped.lower()

    # Handle `pick N` or plain `N` for choice selection
    if lowered.startswith("pick "):
        try:
            return _ParsedInput(selected_choice=int(stripped.split(None, 1)[1]))
        except (IndexError, ValueError):
            pass

    # Also accept plain numbers as choice selection
    try:
        choice_num = int(stripped)
        return _ParsedInput(selected_choice=choice_num)
    except ValueError:
        pass

    if ":" in stripped:
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if value:
            if key in _TEXT_SLOTS:
                return _ParsedInput(
                    slot_updates=(
                        _slot_update(_TEXT_SLOTS[key], value, ValueNormalization.AS_STATED),
                    )
                )
            if key in _MONEY_SLOTS:
                return _ParsedInput(
                    slot_updates=(_slot_update(_MONEY_SLOTS[key], value, _money_norm(value)),)
                )
            if key in _PERCENT_RATIO_SLOTS:
                return _ParsedInput(
                    slot_updates=(
                        _slot_update(_PERCENT_RATIO_SLOTS[key], value, _percent_ratio_norm(value)),
                    )
                )
            if key in _PERCENT_RATE_SLOTS:
                return _ParsedInput(
                    slot_updates=(
                        _slot_update(_PERCENT_RATE_SLOTS[key], value, _percent_rate_norm(value)),
                    )
                )
            if key in _MONTHS_SLOTS:
                return _ParsedInput(
                    slot_updates=(_slot_update(_MONTHS_SLOTS[key], value, _months_norm(value)),)
                )
            if key in _COUNT_SLOTS:
                return _ParsedInput(
                    slot_updates=(
                        _slot_update(_COUNT_SLOTS[key], value, ValueNormalization.AS_STATED),
                    )
                )
            if key == "decline":
                try:
                    return _ParsedInput(declined_slots=(SlotName(value.lower().replace(" ", "_")),))
                except ValueError:
                    print(f"unknown slot name for /decline: {value!r}", file=sys.stderr)
                    return _ParsedInput()

    return _ParsedInput(text=stripped)


# --- runtime construction ---------------------------------------------------


def _offline_runtime(*, llm_provider: object) -> AdvisoryRuntime:
    """No network at all — the same offline-fixture style
    `scripts/phase6_demo.py` and `tests/test_app_service.py` each use."""
    settings = Settings(cache_enabled=False, census_villages_path="no-such-file.csv.gz")
    repo = InMemoryBusinessRepository()
    discovery_service = DiscoveryService(
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        source=_FakeSource(),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    corpus_dir = Path(__file__).resolve().parent.parent / "data" / "knowledge"
    corpus = FileCorpusStore(corpus_dir, settings=settings)
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery_service,
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=corpus,
        repository=repo,
        retriever=None,
    )
    return AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        llm_provider=llm_provider,  # type: ignore[arg-type]
    )


def _build_runtime(*, offline: bool, use_llm: bool) -> AdvisoryRuntime:
    settings = get_settings()
    llm_ready = bool(settings.llm_enabled and settings.llm_base_url)
    if use_llm and not llm_ready:
        raise SystemExit(
            "error: --llm requires VYAPAR_LLM_ENABLED=true and VYAPAR_LLM_BASE_URL set in "
            "the environment (see .env.example). Not faking natural-language extraction "
            "without a real provider configured (CLAUDE.md §3.1)."
        )
    if offline:
        provider = HttpLlmProvider(settings) if llm_ready else None
        return _offline_runtime(llm_provider=provider)
    return build_default_runtime(settings)


# --- presentation ------------------------------------------------------


def _print_reply(reply: AdvisoryReply) -> None:
    for message in reply.messages:
        print(message.text)
        for choice in message.choices:
            print(f"  {choice.index}. {choice.label}")
    for warning in reply.warnings:
        print(f"[warning] {warning}")
    print(f"[{reply.state.value} / {reply.severity.value}]")


def _print_state(snapshot: AdvisorySnapshot) -> None:
    print(f"turn {snapshot.turn_index}  updated {snapshot.updated_at.isoformat()}")
    print("slots:")
    any_slot = False
    for name in sorted(snapshot.slots):
        slot = snapshot.slots[name]
        current = slot.get("current", {})
        state = current.get("state")
        if state == "missing":
            continue
        any_slot = True
        value = current.get("value")
        suffix = f" = {value}" if value is not None else ""
        print(f"  {name}: {state}{suffix}")
    if not any_slot:
        print("  (nothing stated yet)")
    print(
        "steps completed: "
        + (", ".join(sorted(snapshot.artifacts)) if snapshot.artifacts else "(none yet)")
    )
    if snapshot.warnings:
        print("session warnings:")
        for w in snapshot.warnings:
            print(f"  - {w}")


def _print_artifact(snapshot: AdvisorySnapshot, step: StepId, label: str) -> None:
    artifact = snapshot.artifacts.get(step.value)
    if artifact is None:
        print(f"{label} has not run yet — provide more information first.")
        return
    print(json.dumps(artifact["payload"], indent=2, default=str))


# --- the REPL ------------------------------------------------------


def _start_session(service: AdvisoryService) -> str:
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.CLI, started_at=datetime.now(UTC))
    )
    print(f"session {handle.session_id} started. Type /help for commands.")
    return handle.session_id


def run(*, offline: bool, use_llm: bool) -> int:
    runtime = _build_runtime(offline=offline, use_llm=use_llm)
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    mode = "offline (no network)" if offline else "live discovery"
    llm_note = "LLM extraction ON" if runtime.llm_provider is not None else "LLM extraction OFF"
    print(f"VyaparSarathi — Phase 6 chat harness [{mode}, {llm_note}]")
    session_id = _start_session(service)

    try:
        while True:
            try:
                line = input("> ")
            except EOFError:
                print()
                break
            if not line.strip():
                continue
            lowered = line.strip().lower()

            if lowered in ("/quit", "/exit"):
                break
            if lowered == "/help":
                print(_HELP)
                continue
            if lowered == "/reset":
                session_id = _start_session(service)
                continue
            if lowered in ("/state", "/snapshot", "/structure", "/swot"):
                snap = service.snapshot(session_id)
                if snap is None:
                    print("no session state yet.")
                    continue
                if lowered == "/state":
                    _print_state(snap)
                elif lowered == "/snapshot":
                    print(snap.model_dump_json(indent=2))
                elif lowered == "/structure":
                    _print_artifact(snap, StepId.STRUCTURE_FINANCE, "STRUCTURE_FINANCE")
                else:
                    _print_artifact(snap, StepId.SWOT, "SWOT")
                continue

            parsed = _parse_input(line)
            request = MessageRequest(
                session_id=session_id,
                text=parsed.text,
                channel=ChannelId.CLI,
                received_at=datetime.now(UTC),
                slot_updates=parsed.slot_updates,
                declined_slots=parsed.declined_slots,
                selected_choice=parsed.selected_choice,
            )
            try:
                reply = service.send_message(request)
            except SessionNotFoundError:
                print("session was lost; starting a new one.")
                session_id = _start_session(service)
                continue
            _print_reply(reply)
    finally:
        runtime.close()
    return 0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="phase6_chat", description=__doc__)
    parser.add_argument(
        "--offline", action="store_true", help="use offline fakes; no network at all"
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="require and validate a configured LLM provider for free-text extraction",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return run(offline=args.offline, use_llm=args.llm)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
