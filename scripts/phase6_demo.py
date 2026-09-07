"""Phase 6 — the application backend + LLM orchestration, end to end
(CLAUDE.md §25 Phase 6, §31, §32).

Runs the Bhagwanpur pulses-grocery scenario (CLAUDE.md §31) through
`AdvisoryService` exactly as a channel would: `start_session` then a handful
of `send_message` turns, entirely through the structured (`llm_enabled=False`)
path — no LLM provider is constructed anywhere in this script, and no
network call is made (a fake geocoder / Overpass client / Census / knowledge
corpus stand in, the same style `tests/test_app_service.py` uses).

Two transcripts, matching `phase5_demo.py`'s honesty posture:

* **A** — against an EMPTY knowledge corpus (what this repository ships by
  default). Every scheme parameter resolves `NO_EVIDENCE`; no interest rate,
  tenure, or margin appears anywhere in the narrative. This is not a bug —
  refusing to invent a scheme rule is the feature CLAUDE.md §32 asks this
  system to demonstrate.
* **B** — against the visibly synthetic corpus under
  ``tests/fixtures/knowledge/`` (see its own README: no figure in it
  describes a real Indian credit scheme). A rate resolves, a loan is built,
  and DSCR appears — printed under an explicit "SYNTHETIC FIXTURE DATA"
  banner, exactly as `phase5_demo.py` prints its own numbers.
* **C** (Tier 1) — the empty knowledge corpus again, but with a demo-only
  declared SIH financing structure set for the duration of this one
  transcript (`config/sih_scheme.py::DEFAULT_SIH_SCHEME_CONFIG` is `None` —
  unconfigured — everywhere else in this repository). Shows
  `STRUCTURE_FINANCE` deriving a real margin/loan split and Phase 4 pricing a
  real EMI/DSCR from it, printed under an explicit "ILLUSTRATIVE DEMO
  CONFIGURATION" banner — this split is this demo script's own invented
  number, not a retrieved or real scheme figure (CLAUDE.md §30).

Run:  ``./.venv/Scripts/python.exe scripts/phase6_demo.py``
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import vyaparsarathi.llm.tools as tools_module
from vyaparsarathi.app.dto import (
    AdvisoryReply,
    ChannelId,
    MessageRequest,
    StartSessionRequest,
)
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.config import Settings
from vyaparsarathi.config.sih_scheme import SihSchemeConfig
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

_FIXTURES_KNOWLEDGE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "knowledge"

# Demo-only — set for the duration of `run_transcript_with_demo_scheme` only,
# then restored. NOT the shipped default (`config/sih_scheme.py`'s own
# `DEFAULT_SIH_SCHEME_CONFIG` stays `None`); this split is this script's own
# illustrative invention, never a real SIH26091 figure. [decision]
_DEMO_SCHEME_CONFIG = SihSchemeConfig(
    scheme_name="Illustrative demo structure (NOT a real SIH26091 figure)",
    promoter_contribution_pct=Decimal("0.10"),
    loan_pct=Decimal("0.90"),
    interest_rate_pct=Decimal("11"),
    tenure_months=60,
    moratorium_months=6,
    rationale=(
        "scripts/phase6_demo.py's own illustrative demo value, set only for Transcript "
        "C — not sourced from the SIH26091 problem statement, not a retrieved scheme rule."
    ),
)


# --- offline fakes (same style as tests/test_app_service.py) ---------------


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


def _build_service(
    *, knowledge_corpus_dir: Path
) -> tuple[AdvisoryService, InMemorySessionRepository]:
    settings = Settings(cache_enabled=False, census_villages_path="no-such-file.csv.gz")
    repo = InMemoryBusinessRepository()
    discovery_service = DiscoveryService(
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        source=_FakeSource(),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    corpus = FileCorpusStore(knowledge_corpus_dir, settings=settings)
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery_service,
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=corpus,
        repository=repo,
        retriever=None,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_FakeGeocoder(),  # type: ignore[arg-type]
        overpass_client=_FakeOverpassClient(),  # type: ignore[arg-type]
    )
    sessions = InMemorySessionRepository()
    return AdvisoryService(sessions=sessions, runtime=runtime), sessions


def _msg(session_id: str, **kwargs: object) -> MessageRequest:
    return MessageRequest(
        session_id=session_id, channel=ChannelId.CLI, received_at=datetime.now(UTC), **kwargs
    )


def _slot(name: SlotName, raw: str, token: str, norm: ValueNormalization) -> SlotUpdateInput:
    return SlotUpdateInput(slot=name, raw_text=raw, value_token=token, normalization=norm)


def run_transcript(knowledge_corpus_dir: Path) -> tuple[list[AdvisoryReply], object]:
    """Runs the Bhagwanpur scenario in two turns. Returns
    `(replies, final_session)`."""
    service, sessions = _build_service(knowledge_corpus_dir=knowledge_corpus_dir)
    handle = service.start_session(
        StartSessionRequest(channel=ChannelId.CLI, started_at=datetime.now(UTC))
    )

    turn1 = service.send_message(
        _msg(
            handle.session_id,
            slot_updates=(
                _slot(
                    SlotName.PROPOSED_BUSINESS_TEXT,
                    "I want to open a pulses grocery store",
                    "I want to open a pulses grocery store",
                    ValueNormalization.AS_STATED,
                ),
                _slot(
                    SlotName.LOCATION_TEXT,
                    "in Bhagwanpur, Bihar",
                    "Bhagwanpur, Bihar",
                    ValueNormalization.AS_STATED,
                ),
                _slot(
                    SlotName.LIQUID_CASH_INR,
                    "I have 6.5 lakh",
                    "6.5 lakh",
                    ValueNormalization.LAKH_TO_INR,
                ),
            ),
        )
    )

    turn2 = service.send_message(
        _msg(
            handle.session_id,
            slot_updates=(
                _slot(
                    SlotName.MONTHLY_REVENUE_INR,
                    "I expect about 40000 a month",
                    "40000",
                    ValueNormalization.AS_STATED,
                ),
                _slot(
                    SlotName.COGS_PCT,
                    "my cost of stock is around 70%",
                    "70%",
                    ValueNormalization.PERCENT_TO_RATIO,
                ),
                _slot(
                    SlotName.PROJECT_COST_INR,
                    "setting up will cost 3 lakh",
                    "3 lakh",
                    ValueNormalization.LAKH_TO_INR,
                ),
                _slot(
                    SlotName.FIXED_OPEX_INR,
                    "monthly costs are about 4000",
                    "4000",
                    ValueNormalization.AS_STATED,
                ),
            ),
        )
    )

    session = sessions.get(handle.session_id)
    return [turn1, turn2], session


def run_transcript_with_demo_scheme(
    knowledge_corpus_dir: Path,
) -> tuple[list[AdvisoryReply], object]:
    """Transcript C: identical Bhagwanpur scenario, but with
    `_DEMO_SCHEME_CONFIG` set on `llm.tools` for the duration of this one
    call, then restored — the shipped default
    (`config/sih_scheme.py::DEFAULT_SIH_SCHEME_CONFIG`) is `None` everywhere
    else in this repository."""
    previous = tools_module.DEFAULT_SIH_SCHEME_CONFIG
    tools_module.DEFAULT_SIH_SCHEME_CONFIG = _DEMO_SCHEME_CONFIG
    try:
        return run_transcript(knowledge_corpus_dir)
    finally:
        tools_module.DEFAULT_SIH_SCHEME_CONFIG = previous


def check_common() -> list[str]:
    """Invariants both transcripts must satisfy — an empty list means every
    check passed. Mirrors `phase5_demo.py::check_common`'s shape."""
    problems: list[str] = []

    replies_a, session_a = run_transcript(_empty_corpus_dir())
    all_text_a = " ".join(m.text for r in replies_a for m in r.messages).lower()
    if "interest rate" in all_text_a or "% per annum" in all_text_a:
        problems.append("transcript A (empty corpus) must never state an interest rate")
    knowledge_a = session_a.artifacts.get(StepId.FINANCE_KNOWLEDGE)
    if knowledge_a is not None:
        resolutions = knowledge_a.payload.get("resolutions", [])
        if any(r.get("status") == "resolved" for r in resolutions):
            problems.append("transcript A (empty corpus) must resolve zero parameters")
    structure_a = session_a.artifacts.get(StepId.STRUCTURE_FINANCE)
    if structure_a is not None and structure_a.payload.get("status") == "structured":
        problems.append("transcript A (unconfigured scheme) must never structure a financing split")
    if "illustrative demo structure" in all_text_a:
        problems.append("transcript A must never leak Transcript C's demo scheme name")

    replies_b, session_b = run_transcript(_FIXTURES_KNOWLEDGE)
    if not replies_b:
        problems.append("transcript B produced no replies")

    replies_c, session_c = run_transcript_with_demo_scheme(_empty_corpus_dir())
    if not replies_c:
        problems.append("transcript C produced no replies")
    structure_c = session_c.artifacts.get(StepId.STRUCTURE_FINANCE)
    if structure_c is None or structure_c.payload.get("status") != "structured":
        problems.append("transcript C (demo scheme configured) must structure a financing split")
    # After Transcript C's demo-scheme monkeypatch is restored, a fresh
    # (unrelated) run must go straight back to unconfigured — the module
    # global must never leak across calls.
    _replies_after, session_after = run_transcript(_empty_corpus_dir())
    structure_after = session_after.artifacts.get(StepId.STRUCTURE_FINANCE)
    if structure_after is not None and structure_after.payload.get("status") == "structured":
        problems.append("the demo scheme leaked past run_transcript_with_demo_scheme's cleanup")

    return problems


def _empty_corpus_dir() -> Path:
    import tempfile

    d = Path(tempfile.mkdtemp()) / "empty-knowledge"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _print_transcript(label: str, replies: list[AdvisoryReply]) -> None:
    print(f"\n=== {label} ===")
    for i, reply in enumerate(replies, start=1):
        print(f"-- turn {i} (state={reply.state.value}, severity={reply.severity.value}) --")
        for m in reply.messages:
            print(m.text)


def main(argv: list[str] | None = None) -> int:
    del argv
    print("VyaparSarathi — Phase 6 demo (Bhagwanpur, Bihar; CLAUDE.md §31 scenario)")

    replies_a, _session_a = run_transcript(_empty_corpus_dir())
    _print_transcript("Transcript A — shipped (empty) knowledge corpus", replies_a)

    print(
        "\n*** SYNTHETIC FIXTURE DATA BELOW — tests/fixtures/knowledge/ is invented "
        "for testing; no figure describes a real Indian credit scheme (see its README). ***"
    )
    replies_b, _session_b = run_transcript(_FIXTURES_KNOWLEDGE)
    _print_transcript("Transcript B — synthetic fixture knowledge corpus", replies_b)

    print(
        "\n*** ILLUSTRATIVE DEMO CONFIGURATION BELOW — this margin/loan split and rate "
        "are this script's own invented demo values (see _DEMO_SCHEME_CONFIG above), set "
        "only for this transcript; the shipped default (config/sih_scheme.py) is "
        "unconfigured. Not a real SIH26091 figure. ***"
    )
    replies_c, _session_c = run_transcript_with_demo_scheme(_empty_corpus_dir())
    _print_transcript(
        "Transcript C — Tier 1 SIH financing structure (illustrative demo config)", replies_c
    )

    problems = check_common()
    if problems:
        print("\nFAILED invariants:")
        for p in problems:
            print(f" - {p}")
        return 1
    print("\nAll invariants passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
