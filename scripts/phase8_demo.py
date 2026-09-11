"""Phase 8 — the SIH demo Detailed Project Report (CLAUDE.md §25 Phase 8, §32).

Builds ONE complete advisory session end to end with **no live APIs** — a
fixture geocoder, a fixture OpenStreetMap sample, the committed Census 2011
demo village extract, and the shipped (real, deliberately small) knowledge
corpus under ``data/knowledge/`` — drives it through `AdvisoryService` to a
recommendation, then generates the DPR (PDF + structured JSON).

The scenario is CLAUDE.md §31's: a pulses-grocery store in Bhagwanpur, Bihar,
₹6.5 lakh available. The local sample is deliberately crowded with grocery
shops so the opportunity engine surfaces a better-scoring alternative — the
"catch a poor decision, point to a better one" story §32 asks the demo to
show — while the financial section is a real Phase 4 assessment of the stated
plan, and the scheme section shows the two genuinely-sourced parameters the
shipped corpus supports plus honest "no evidence" for interest rate / tenure
/ moratorium.

Run:
    ./.venv/Scripts/python.exe scripts/phase8_demo.py
    ./.venv/Scripts/python.exe scripts/phase8_demo.py --out-dir build/dpr

`tests/test_phase8_demo.py` imports `build_demo_session` / `build_demo_document`
so the invariants are asserted in the gate too.
"""

from __future__ import annotations

import argparse
import sys
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
from vyaparsarathi.dpr.errors import DprOutputExistsError
from vyaparsarathi.dpr.report_models import DprDocument
from vyaparsarathi.dpr.service import DprArtifacts, DprService
from vyaparsarathi.llm.tools import RunContext
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.taxonomy import SourceName
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.knowledge.loader import FileCorpusStore
from vyaparsarathi.sources.osm.adapter import OverpassFetch
from vyaparsarathi.sources.osm.models import RawOsmElement

_REPO = Path(__file__).resolve().parent.parent
_CENSUS_FIXTURE = _REPO / "tests" / "fixtures" / "census" / "demo_villages.csv"
_KNOWLEDGE_CORPUS = _REPO / "data" / "knowledge"
# A fixed, injected report timestamp — never a clock read (CLAUDE.md §28), so
# the demo report is byte-reproducible.
DEMO_GENERATED_AT = datetime(2026, 9, 9, 9, 30, tzinfo=UTC)

# Near the fixture Census villages (Testland / Test District), so the spatial
# population join actually returns residents for the catchment.
_LAT, _LON = 25.7120, 85.2100


class _FixtureGeocoder:
    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        return [
            PlaceCandidate(
                display_name="Bhagwanpur, Vaishali, Bihar",
                latitude=_LAT,
                longitude=_LON,
                importance=0.55,
                country="India",
                state="Bihar",
                district="Vaishali",
                block="Bhagwanpur",
                village="Bhagwanpur",
            )
        ]


_OSM_SHOPS: tuple[tuple[str, dict[str, str]], ...] = (
    ("Sharma Kirana Store", {"shop": "convenience", "name": "Sharma Kirana Store"}),
    ("Bhagwanpur Bazaar", {"shop": "supermarket", "name": "Bhagwanpur Bazaar"}),
    ("Gupta General Store", {"shop": "general", "name": "Gupta General Store"}),
    ("Maa Vaishno Provision", {"shop": "convenience", "name": "Maa Vaishno Provision"}),
    ("Raj Grocery", {"shop": "convenience", "name": "Raj Grocery"}),
    ("New Anaj Bhandar", {"shop": "grocery", "name": "New Anaj Bhandar"}),
    ("Kisan Agri Centre", {"shop": "agrarian", "name": "Kisan Agri Centre"}),
    ("Bhagwanpur Medical", {"amenity": "pharmacy", "name": "Bhagwanpur Medical"}),
)


class _FixtureOsmSource:
    name = SourceName.OSM

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        elements = [
            RawOsmElement(
                element_type="node",
                element_id=i + 1,
                latitude=_LAT + (i - 3) * 0.0015,
                longitude=_LON + (i - 3) * 0.0015,
                tags=tags,
                raw={"type": "node", "id": i + 1, "tags": tags},
            )
            for i, (_name, tags) in enumerate(_OSM_SHOPS)
        ]
        return OverpassFetch(
            elements=elements,
            raw_count=len(elements),
            dropped_no_coordinates=0,
            endpoint_used="fixture://phase8-demo",
            mirror_fallback_used=False,
            query_ql="[out:json];",
        )


class _FixtureOverpassClient:
    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        return [], "fixture://phase8-demo", False


def _slot(name: SlotName, raw: str, token: str, norm: ValueNormalization) -> SlotUpdateInput:
    return SlotUpdateInput(slot=name, raw_text=raw, value_token=token, normalization=norm)


def build_demo_session() -> ConversationSession:
    """Run the CLAUDE.md §31 scenario through `AdvisoryService` (no LLM,
    structured input, no network) and return the finished session."""
    settings = Settings(
        cache_enabled=False,
        census_villages_path=str(_CENSUS_FIXTURE),
        knowledge_corpus_dir=str(_KNOWLEDGE_CORPUS),
        max_radius_m=25_000,
    )
    repo = InMemoryBusinessRepository()
    discovery = DiscoveryService(
        geocoder=_FixtureGeocoder(),  # type: ignore[arg-type]
        source=_FixtureOsmSource(),  # type: ignore[arg-type]
        repository=repo,
        settings=settings,
    )
    corpus = FileCorpusStore(_KNOWLEDGE_CORPUS, settings=settings)
    ctx = RunContext(
        settings=settings,
        discovery_service=discovery,
        overpass_client=_FixtureOverpassClient(),  # type: ignore[arg-type]
        census=CensusVillageSource(settings=settings),
        corpus=corpus,
        repository=repo,
        retriever=None,
    )
    runtime = AdvisoryRuntime(
        settings=settings,
        run_context=ctx,
        geocoder=_FixtureGeocoder(),  # type: ignore[arg-type]
        overpass_client=_FixtureOverpassClient(),  # type: ignore[arg-type]
    )
    sessions = InMemorySessionRepository()
    service = AdvisoryService(sessions=sessions, runtime=runtime)

    handle = service.start_session(
        StartSessionRequest(
            session_id="phase8-demo-bhagwanpur",
            channel=ChannelId.CLI,
            started_at=datetime(2026, 9, 8, 10, 0, tzinfo=UTC),
        )
    )
    service.send_message(
        MessageRequest(
            session_id=handle.session_id,
            channel=ChannelId.CLI,
            received_at=datetime(2026, 9, 8, 10, 1, tzinfo=UTC),
            slot_updates=(
                _slot(
                    SlotName.PROPOSED_BUSINESS_TEXT,
                    "I want to open a pulses grocery store",
                    "pulses grocery store",
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
                    "I have 6.5 lakh saved",
                    "6.5 lakh",
                    ValueNormalization.LAKH_TO_INR,
                ),
            ),
        )
    )
    service.send_message(
        MessageRequest(
            session_id=handle.session_id,
            channel=ChannelId.CLI,
            received_at=datetime(2026, 9, 8, 10, 5, tzinfo=UTC),
            slot_updates=(
                _slot(
                    SlotName.MONTHLY_REVENUE_INR,
                    "about 90000 a month once it picks up",
                    "90000",
                    ValueNormalization.AS_STATED,
                ),
                _slot(
                    SlotName.COGS_PCT,
                    "cost of stock is roughly 82%",
                    "82%",
                    ValueNormalization.PERCENT_TO_RATIO,
                ),
                _slot(
                    SlotName.PROJECT_COST_INR,
                    "setting up will cost about 5 lakh",
                    "5 lakh",
                    ValueNormalization.LAKH_TO_INR,
                ),
                _slot(
                    SlotName.FIXED_OPEX_INR,
                    "monthly fixed costs around 9000",
                    "9000",
                    ValueNormalization.AS_STATED,
                ),
            ),
        )
    )
    session = sessions.get(handle.session_id)
    assert session is not None
    return session


def build_demo_document() -> DprDocument:
    return DprService(InMemorySessionRepository()).build_from_session(
        build_demo_session(), generated_at=DEMO_GENERATED_AT
    )


def generate(out_dir: Path, *, overwrite: bool) -> DprArtifacts:
    session = build_demo_session()
    service = DprService(InMemorySessionRepository())
    out_dir.mkdir(parents=True, exist_ok=True)
    return service.generate_from_session(
        session,
        generated_at=DEMO_GENERATED_AT,
        pdf_path=out_dir / "vyaparsarathi_dpr_demo.pdf",
        json_path=out_dir / "vyaparsarathi_dpr_demo.json",
        overwrite=overwrite,
    )


def _summary(doc: DprDocument) -> str:
    lines = [
        f"Report ID           : {doc.report_id}",
        f"Input fingerprint   : {doc.input_fingerprint[:24]}…",
        f"Recommendation      : {doc.executive_summary.recommendation.display}",
        f"  reason            : {doc.executive_summary.verdict_reason}",
        f"Market reading       : {doc.market.market_label.display}",
        f"Proposed opp. score  : {doc.opportunity.proposed_score.display}",
        f"Recommended pivot    : {doc.opportunity.recommended_pivot.display}",
        f"Financial status     : {doc.financial.feasibility_status.display}",
        f"Scheme corpus        : {doc.scheme_knowledge.corpus_note}",
        f"Citations            : {', '.join(doc.citations) or 'none'}",
        f"Evidence gaps        : {len(doc.evidence_gaps)}",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="phase8_demo", description=__doc__)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=_REPO / "build" / "dpr",
        help="directory for the PDF + JSON (default: build/dpr/)",
    )
    parser.add_argument(
        "--overwrite", action="store_true", help="replace existing demo output files"
    )
    args = parser.parse_args(argv)

    print("VyaparSarathi — Phase 8 DPR demo (Bhagwanpur, Bihar; CLAUDE.md §31)\n")
    try:
        artifacts = generate(args.out_dir, overwrite=args.overwrite)
    except DprOutputExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(_summary(artifacts.document))
    print(f"\nPDF  : {artifacts.pdf_path}  ({artifacts.pdf_byte_count:,} bytes)")
    print(f"JSON : {artifacts.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
