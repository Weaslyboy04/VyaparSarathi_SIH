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

Default mode is NORMAL: collect business, location, assets, trade experience
and Available Margin Capital, in that order, then show one consolidated
advisory (CLAUDE.md's "COLLECT → VALIDATE → COLLECT → ANALYSE → one advisory"
UX). `--dev` switches to DEVELOPER mode: today's incremental
structured-command harness, printing a (possibly partial) advisory after
every turn — still useful for inspecting individual steps/artifacts.

Run:
    ./.venv/Scripts/python.exe scripts/phase6_chat.py            # live discovery
    ./.venv/Scripts/python.exe scripts/phase6_chat.py --offline  # no network
    ./.venv/Scripts/python.exe scripts/phase6_chat.py --llm      # + LLM extraction
    ./.venv/Scripts/python.exe scripts/phase6_chat.py --dev      # developer harness mode

Commands: /reset  /state  /snapshot  /structure  /capacity  /swot  /dpr  /help  /quit

`/dpr [name]` composes a Phase 8 Detailed Project Report (PDF + JSON) from the
current session's artifacts — no engine is re-run, no LLM is used for the
report. Files land in `build/dpr/` (or at a name/path you give); an existing
file is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

from vyaparsarathi.app.dto import (
    AdvisoryReply,
    AdvisorySnapshot,
    ChannelId,
    MessageRequest,
    ReportStatus,
    StartSessionRequest,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.greeting import GREETING_MESSAGE
from vyaparsarathi.app.runtime import AdvisoryRuntime, build_default_runtime, build_llm_provider
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.conversation.conversation_config import ConversationMode
from vyaparsarathi.conversation.session_models import SlotName, StepId
from vyaparsarathi.conversation.understanding import (
    AssetUpdateInput,
    ExperienceUpdateInput,
    SlotUpdateInput,
)
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.session_memory import InMemorySessionRepository
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.dpr import DprArtifacts, DprOutputExistsError, DprService
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.profile import AssetKind
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
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
  experience: <years>        years of relevant experience (a count; does not
                              feed opportunity scoring -- see 'trades:' below)
  assets: <kinds>            owned physical assets, comma-separated, e.g.
                              "storefront, vehicle" -- or "assets: none"
  trades: <categories>       trade/business categories you have experience
                              in, comma-separated -- or "trades: none"
  decline: <slot name>       explicitly decline to answer a slot (e.g.
                              "decline: years_experience")
  <N>                        answer a numbered disambiguation (just type the number)
Session commands:
  /reset       start a brand-new session
  /state       print slot values and which steps have run
  /snapshot    print the full AdvisorySnapshot (Phase 8 DPR contract) as JSON
  /structure   print the STRUCTURE_FINANCE artifact, if any (needs revenue,
               cost of goods, project cost and monthly opex to derive a
               real project cost -- the ACTUAL business's financing split)
  /capacity    print the SCHEME_CAPACITY artifact, if any (needs only cash --
               the SIH headline: Available Margin Capital -> project
               capacity -> loan, before any of the above is known)
  /swot        print the SWOT artifact, if any
  /dpr [name]  compose a Detailed Project Report (PDF + JSON) from this
               session's artifacts -> build/dpr/ (or <name>.pdf / a path you
               give). Never overwrites an existing file. No engine or LLM
               runs for the report; it only composes what already exists.
  /help        this text
  /quit        exit
Any other line is sent as free text — without --llm, the planner will ask
its own clarifying question rather than this script guessing what it means.
"""


def _slot_update(name: SlotName, raw_text: str, norm: ValueNormalization) -> SlotUpdateInput:
    return SlotUpdateInput(slot=name, raw_text=raw_text, value_token=raw_text, normalization=norm)


_THOUSAND_RE = re.compile(r"\bthousand\b|\d\s*k\b", re.IGNORECASE)


def _money_norm(text: str) -> ValueNormalization:
    lowered = text.lower()
    if "crore" in lowered:
        return ValueNormalization.CRORE_TO_INR
    if "lakh" in lowered or "lac" in lowered:
        return ValueNormalization.LAKH_TO_INR
    if _THOUSAND_RE.search(lowered):
        return ValueNormalization.THOUSAND_TO_INR
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
    __slots__ = (
        "asset_update",
        "assets_declined",
        "declined_slots",
        "experience_declined",
        "experience_update",
        "selected_choice",
        "slot_updates",
        "text",
    )

    def __init__(
        self,
        slot_updates: tuple[SlotUpdateInput, ...] = (),
        declined_slots: tuple[SlotName, ...] = (),
        asset_update: AssetUpdateInput | None = None,
        experience_update: ExperienceUpdateInput | None = None,
        assets_declined: bool = False,
        experience_declined: bool = False,
        selected_choice: int | None = None,
        text: str = "",
    ) -> None:
        self.slot_updates = slot_updates
        self.declined_slots = declined_slots
        self.asset_update = asset_update
        self.experience_update = experience_update
        self.assets_declined = assets_declined
        self.experience_declined = experience_declined
        self.selected_choice = selected_choice
        self.text = text


_DECLINE_WORDS = {"none", "no", "nothing", "n/a"}


def _parse_asset_kinds(value: str) -> tuple[AssetKind, ...]:
    """Raises `ValueError` (with the offending token) on an unknown kind —
    the caller prints it and drops the line, same as `decline:`'s handling."""
    return tuple(
        AssetKind(token.strip().lower().replace(" ", "_"))
        for token in value.split(",")
        if token.strip()
    )


def _parse_categories(value: str) -> tuple[BusinessCategory, ...]:
    return tuple(
        BusinessCategory(token.strip().lower().replace(" ", "_"))
        for token in value.split(",")
        if token.strip()
    )


def _parse_input(line: str) -> _ParsedInput:
    """`key: value` -> exactly one `SlotUpdateInput`; `pick N` or just `N` -> a choice
    selection; `decline: <slot>` -> a declined slot; `assets: <kinds>` /
    `trades: <categories>` -> an asset/experience update, or a decline if the
    value is 'none'; anything else is returned verbatim as free text (never
    interpreted here)."""
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
                    print(
                        f"unknown slot name for 'decline: {value}': not a recognised slot",
                        file=sys.stderr,
                    )
                    return _ParsedInput()
            if key == "assets":
                if value.lower() in _DECLINE_WORDS:
                    return _ParsedInput(assets_declined=True)
                try:
                    items = _parse_asset_kinds(value)
                except ValueError as exc:
                    print(f"unknown asset kind in 'assets: {value}': {exc}", file=sys.stderr)
                    return _ParsedInput()
                if not items:
                    return _ParsedInput()
                return _ParsedInput(asset_update=AssetUpdateInput(items=items, raw_text=value))
            if key == "trades":
                if value.lower() in _DECLINE_WORDS:
                    return _ParsedInput(experience_declined=True)
                try:
                    categories = _parse_categories(value)
                except ValueError as exc:
                    print(f"unknown business category in 'trades: {value}': {exc}", file=sys.stderr)
                    return _ParsedInput()
                if not categories:
                    return _ParsedInput()
                return _ParsedInput(
                    experience_update=ExperienceUpdateInput(items=categories, raw_text=value)
                )

    return _ParsedInput(text=stripped)


# --- DPR generation (Phase 8) ---------------------------------------------

# Module global so a test can point it at a temp directory.
DPR_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "build" / "dpr"


def _dpr_paths(session_id: str, arg: str, *, out_dir: Path, now: datetime) -> tuple[Path, Path]:
    """Resolve the (pdf, json) output pair for `/dpr [arg]`.

    * no arg      -> `build/dpr/dpr_<session>_<utc-stamp>.pdf` (unique; never collides)
    * a bare name -> `build/dpr/<name>.pdf`
    * a path / a `*.pdf` -> used as given (its sibling `.json` beside it)
    """
    arg = arg.strip()
    if not arg:
        stamp = f"{now:%Y%m%dT%H%M%S}Z_{now.microsecond:06d}"
        pdf_path = out_dir / f"dpr_{session_id}_{stamp}.pdf"
    else:
        candidate = Path(arg).expanduser()
        if candidate.suffix.lower() == ".pdf" or candidate.is_absolute() or arg != candidate.name:
            pdf_path = (
                candidate if candidate.suffix.lower() == ".pdf" else candidate.with_suffix(".pdf")
            )
        else:
            pdf_path = out_dir / f"{arg}.pdf"
    return pdf_path, pdf_path.with_suffix(".json")


def build_dpr(
    sessions: SessionRepository,
    session_id: str,
    arg: str = "",
    *,
    out_dir: Path | None = None,
    now: datetime | None = None,
) -> DprArtifacts:
    """Compose a Detailed Project Report for the current session and write the
    PDF + JSON side by side. Never overwrites (raises `DprOutputExistsError`).
    The report is assembled deterministically from stored artifacts — no
    engine or LLM runs here (`vyaparsarathi.dpr`)."""
    now = now or datetime.now(UTC)
    pdf_path, json_path = _dpr_paths(session_id, arg, out_dir=out_dir or DPR_OUTPUT_DIR, now=now)
    return DprService(sessions).generate(
        session_id,
        generated_at=now,
        pdf_path=pdf_path,
        json_path=json_path,
        overwrite=False,
    )


def _print_dpr_result(artifacts: DprArtifacts) -> None:
    doc = artifacts.document
    print(f"DPR generated: {doc.report_id}")
    print(f"  recommendation : {doc.executive_summary.recommendation.display}")
    print(f"  financial      : {doc.financial.feasibility_status.display}")
    print(f"  evidence gaps  : {len(doc.evidence_gaps)}")
    print(f"  PDF  : {artifacts.pdf_path.resolve()}  ({artifacts.pdf_byte_count:,} bytes)")
    print(f"  JSON : {artifacts.json_path.resolve()}")


# --- runtime construction ---------------------------------------------------


def _offline_runtime(*, llm_provider: object, mode: ConversationMode) -> AdvisoryRuntime:
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
        mode=mode,
    )
    return AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        llm_provider=llm_provider,  # type: ignore[arg-type]
    )


def _build_runtime(*, offline: bool, use_llm: bool, dev: bool) -> AdvisoryRuntime:
    settings = get_settings()
    llm_ready = bool(
        settings.llm_enabled and (settings.llm_base_url or settings.llm_api_key is not None)
    )
    if use_llm and not llm_ready:
        raise SystemExit(
            "error: --llm requires VYAPAR_LLM_ENABLED=true and either VYAPAR_LLM_BASE_URL "
            "(generic HTTP shim) or VYAPAR_LLM_API_KEY (Gemini-native) set in the environment "
            "(see .env.example). Not faking natural-language extraction without a real "
            "provider configured (CLAUDE.md §3.1)."
        )
    mode = ConversationMode.DEVELOPER if dev else ConversationMode.NORMAL
    if offline:
        # `--offline` means no network at all: an LLM provider (which would
        # call out on free text) is wired ONLY when `--llm` is also given.
        provider = build_llm_provider(settings) if use_llm else None
        return _offline_runtime(llm_provider=provider, mode=mode)
    return build_default_runtime(settings, mode=mode)


# --- presentation ------------------------------------------------------


def _print_reply(reply: AdvisoryReply) -> None:
    # Every numbered option is already baked into its own message line by
    # `conversation/render.py::_question_lines` (e.g. "1. Bhagwanpur, Bihar").
    # `message.choices` carries the same options again, structurally, for a
    # channel that renders choices instead of text (or validates a reply) —
    # `to_whatsapp_messages` reads `.choices` only to decide whether to add a
    # "reply with a number" line, never to print the options themselves. The
    # terminal must not print the option list a second time here.
    for message in reply.messages:
        print(message.text)
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
    print(GREETING_MESSAGE)
    print(f"session {handle.session_id} started. Type /help for commands.")
    return handle.session_id


def run(*, offline: bool, use_llm: bool, dev: bool) -> int:
    runtime = _build_runtime(offline=offline, use_llm=use_llm, dev=dev)
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    net_note = "offline (no network)" if offline else "live discovery"
    llm_note = "LLM extraction ON" if runtime.llm_provider is not None else "LLM extraction OFF"
    conv_note = (
        "DEVELOPER mode: incremental structured-command harness, today's behaviour"
        if dev
        else "NORMAL mode: collects then delivers one consolidated advisory (--dev for the harness)"
    )
    print(f"VyaparSarathi — Phase 6 chat harness [{net_note}, {llm_note}]")
    print(conv_note)
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
            stripped = line.strip()
            lowered = stripped.lower()

            if lowered in ("/quit", "/exit"):
                break
            if lowered == "/help":
                print(_HELP)
                continue
            if lowered == "/reset":
                session_id = _start_session(service)
                continue
            if lowered == "/dpr" or lowered.startswith("/dpr "):
                arg = stripped[5:].strip()
                snap = service.snapshot(session_id)
                if snap is not None and StepId.RECOMMEND.value not in snap.artifacts:
                    print(
                        "[note] no recommendation has been reached yet — the report will "
                        "contain explicit evidence-gap sections for what is still missing."
                    )
                try:
                    _print_dpr_result(build_dpr(sessions, session_id, arg))
                except DprOutputExistsError as exc:
                    print(f"[error] {exc}")
                continue
            if lowered in ("/state", "/snapshot", "/structure", "/capacity", "/swot"):
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
                elif lowered == "/capacity":
                    _print_artifact(snap, StepId.SCHEME_CAPACITY, "SCHEME_CAPACITY")
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
                asset_update=parsed.asset_update,
                experience_update=parsed.experience_update,
                assets_declined=parsed.assets_declined,
                experience_declined=parsed.experience_declined,
                selected_choice=parsed.selected_choice,
            )
            try:
                reply = service.send_message(request)
            except SessionNotFoundError:
                print("session was lost; starting a new one.")
                session_id = _start_session(service)
                continue
            _print_reply(reply)
            if reply.report is not None and reply.report.status is ReportStatus.REQUESTED:
                # Same deterministic, composition-only path `/dpr` already
                # uses — the explicit "yes, generate the report" request just
                # triggers it automatically instead of requiring the /dpr
                # shortcut too.
                try:
                    _print_dpr_result(build_dpr(sessions, session_id))
                except DprOutputExistsError as exc:
                    print(f"[error] {exc}")
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
    parser.add_argument(
        "--dev",
        action="store_true",
        help=(
            "DEVELOPER mode: today's incremental structured-command harness (every "
            "reply, partial included). Default is NORMAL mode: collect the minimum "
            "required inputs, then show one consolidated advisory."
        ),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        return run(offline=args.offline, use_llm=args.llm, dev=args.dev)
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(exc.code, file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
