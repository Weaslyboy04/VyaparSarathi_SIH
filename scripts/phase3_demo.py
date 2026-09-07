"""Phase 3 opportunity / pivot engine — deterministic demo cases (CLAUDE.md §12).

Six fixture-based scenarios that exercise the shipped Phase 3 engine end to end
**with no live APIs**. Each builder returns a hand-made ``OpportunityEvidence`` +
``EntrepreneurProfile`` in the exact shape the impure acquisition layer would
produce, plus a dict of expectations. ``run_demo`` scores it, prints a concise
human-readable summary for the PPT / video, and checks the expectations.

Run:  ``./.venv/Scripts/python.exe scripts/phase3_demo.py``          (all six)
      ``./.venv/Scripts/python.exe scripts/phase3_demo.py 3``        (one)

The same builders are imported by ``tests/test_opportunity_demos.py`` so the
expectations are asserted in the gate too.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime

from vyaparsarathi.discovery.confidence import coverage_confidence
from vyaparsarathi.market.demand import compute_demand_signals
from vyaparsarathi.market.models import Relationship
from vyaparsarathi.market.opportunity import score_opportunities
from vyaparsarathi.market.opportunity_config import DEFAULT_OPPORTUNITY_CONFIG
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult
from vyaparsarathi.market.relationships import relationship_for
from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.demand import (
    ActivityHit,
    ActivityKind,
    ActivityPoint,
    DemandAcquisitionReport,
    DemandEvidence,
    GeographyLevel,
    PopulationRecord,
    Settlement,
    SettlementHit,
    SettlementType,
)
from vyaparsarathi.models.opportunity import OpportunityEvidence
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import (
    CoverageSummary,
    DiscoveryResult,
    DiscoveryStatus,
    SourceCoverage,
)
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.models.taxonomy import SourceName

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_LAT, _LON, _R = 25.7000, 85.2300, 8_000
_SHORTLIST = tuple(C(v) for v in DEFAULT_OPPORTUNITY_CONFIG.candidate_categories)


# --------------------------------------------------------------------------
# fixture primitives
# --------------------------------------------------------------------------


def _business(name: str, category: C) -> BusinessHit:
    return BusinessHit(
        business=NormalizedBusiness(
            name=name,
            normalized_name=name.lower(),
            category=category,
            latitude=_LAT,
            longitude=_LON,
            source=SourceName.OSM,
            source_id=f"node/{abs(hash(name)) % 999983}",
            data_quality=0.6,
        ),
        distance_m=600.0,
    )


def _discovery(
    counts: dict[C, int], *, raw_records: int = 30, confidence: float = 0.6
) -> DiscoveryResult:
    businesses: list[BusinessHit] = []
    for cat, n in counts.items():
        businesses.extend(_business(f"{cat.value}-{i}", cat) for i in range(n))
    return DiscoveryResult(
        status=DiscoveryStatus.OK,
        query_text="Demo location",
        category=C.GROCERY,
        requested_radius_m=_R,
        query=DiscoveryQuery(
            location_text="Demo location",
            latitude=_LAT,
            longitude=_LON,
            radius_m=_R,
            category=C.GROCERY,
        ),
        businesses=businesses,
        sources_queried=[SourceName.OSM],
        coverage=CoverageSummary(
            per_source=[
                SourceCoverage(
                    source=SourceName.OSM, raw_records=raw_records, normalized=len(businesses)
                )
            ],
            total_after_dedup=len(businesses),
        ),
        confidence=confidence,
    )


def _census_settlement(code: str, persons: int) -> SettlementHit:
    return SettlementHit(
        settlement=Settlement(
            name=code,
            normalized_name=code.lower(),
            place_type=SettlementType.VILLAGE,
            latitude=_LAT,
            longitude=_LON,
            census_code=code,
            population=PopulationRecord(
                persons=persons,
                households=max(1, persons // 5),
                geography_level=GeographyLevel.VILLAGE,
                dataset="census_2011_pca_village",
                reference_year=2011,
                provenance=ProvenanceEntry(
                    source=SourceName.GOVT, source_id=code, retrieved_at=_NOW
                ),
                quality=0.85,
            ),
            data_quality=0.85,
        ),
        distance_m=1_200.0,
    )


def _osm_settlement(name: str) -> SettlementHit:
    return SettlementHit(
        settlement=Settlement(
            name=name,
            normalized_name=name.lower(),
            place_type=SettlementType.VILLAGE,
            latitude=_LAT,
            longitude=_LON,
            census_code=None,
            data_quality=0.4,
        ),
        distance_m=1_500.0,
    )


def _activity(kind: ActivityKind, name: str) -> ActivityHit:
    return ActivityHit(
        activity_point=ActivityPoint(
            kind=kind,
            name=name,
            latitude=_LAT,
            longitude=_LON,
            source=SourceName.OSM,
            source_id=f"node/{abs(hash(name)) % 999983}",
        ),
        distance_m=800.0,
    )


def _demand_geolocated(persons_each: int, settlements: int) -> DemandEvidence:
    """Bihar-style: real Census population, fully geolocated."""
    return DemandEvidence(
        latitude=_LAT,
        longitude=_LON,
        radius_m=_R,
        location_text="Demo location",
        state="Bihar",
        district="Demo",
        settlements=[_census_settlement(f"PC11-{i:04d}", persons_each) for i in range(settlements)],
        acquisition=DemandAcquisitionReport(
            census_file_present=True,
            census_extract_covers_query_area=True,
            census_rows_in_radius=settlements,
        ),
        acquired_at=_NOW,
    )


def _demand_ungeolocated() -> DemandEvidence:
    """Telangana-style: population EXISTS but no row is placed on the map."""
    return DemandEvidence(
        latitude=_LAT,
        longitude=_LON,
        radius_m=_R,
        location_text="Demo location",
        state="Telangana",
        district="Demo",
        settlements=[_osm_settlement(f"Gram {i}") for i in range(6)],
        activity_points=[
            _activity(ActivityKind.SCHOOL, "ZP School"),
            _activity(ActivityKind.MARKETPLACE, "Weekly Bazaar"),
            _activity(ActivityKind.BANK, "Grameen Bank"),
        ],
        acquisition=DemandAcquisitionReport(
            census_file_present=True,
            census_extract_covers_query_area=True,
            census_population_ungeolocated_in_area=6,
        ),
        acquired_at=_NOW,
    )


def _demand_empty() -> DemandEvidence:
    return DemandEvidence(
        latitude=_LAT,
        longitude=_LON,
        radius_m=_R,
        location_text="Demo location",
        acquisition=DemandAcquisitionReport(census_file_present=True),
        acquired_at=_NOW,
    )


def _per_candidate_confidence(discovery: DiscoveryResult, cats: tuple[C, ...]) -> dict[C, float]:
    """Exactly what ``discovery/opportunity_acquisition.py`` computes."""
    per = discovery.coverage.per_source[0]
    out: dict[C, float] = {}
    for cat in cats:
        relevant = [
            h
            for h in discovery.businesses
            if relationship_for(cat, h.business.category) is not Relationship.IRRELEVANT
        ]
        out[cat] = coverage_confidence(
            raw_count=per.raw_records,
            normalized=len(relevant),
            after_dedup=len(relevant),
            unmapped_tag_count=0,
            mirror_fallback_used=per.mirror_fallback_used,
        )
    return out


def _evidence(
    discovery: DiscoveryResult, demand: DemandEvidence, *, cats: tuple[C, ...] = _SHORTLIST
) -> OpportunityEvidence:
    return OpportunityEvidence(
        discovery=discovery,
        demand=demand,
        candidate_categories=list(cats),
        per_category_confidence=_per_candidate_confidence(discovery, cats),
    )


# --------------------------------------------------------------------------
# demo builders  ->  (title, location, evidence, profile)
# --------------------------------------------------------------------------

Demo = tuple[str, str, OpportunityEvidence, EntrepreneurProfile]


def build_demo_1() -> Demo:
    # Bhagwanpur: proposed pulses grocery, but the union fetch found NO grocery
    # and no grocery-substitute shops in the catchment. Large Bihar catchment
    # with real Census population. Some non-grocery businesses make a few
    # alternatives assessable.
    disc = _discovery({C.CLOTHING: 6, C.SALON: 3, C.LIVESTOCK_SERVICES: 4})
    dem = _demand_geolocated(persons_each=25_000, settlements=14)
    profile = EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_subtypes=["pulses"],
        proposed_raw_text="pulses grocery store",
    )
    return "DEMO 1 - BHAGWANPUR", "Bhagwanpur, Vaishali, Bihar", _evidence(disc, dem), profile


def build_demo_2() -> Demo:
    # Sangareddy: population EXISTS for the area but nothing is geolocated
    # (outside the Bihar coverage). Scale comes from an activity proxy, never a
    # fabricated head-count.
    disc = _discovery({C.GROCERY: 3, C.GENERAL_STORE: 2})
    dem = _demand_ungeolocated()
    profile = EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT},
        experience_categories={C.GROCERY},
        proposed_category=C.GROCERY,
        proposed_subtypes=["pulses"],
        proposed_raw_text="pulses grocery store",
    )
    return "DEMO 2 - SANGAREDDY", "Sangareddy, Telangana", _evidence(disc, dem), profile


def build_demo_3() -> Demo:
    # Synthetic strong pivot: proposed grocery is `served` (adequate); dairy is
    # `underserved`, affordable, and beats it by well over 8 points with a
    # matching experience + cold storage.
    disc = _discovery({C.GROCERY: 4})
    dem = _demand_geolocated(persons_each=4_000, settlements=15)  # ~60k -> LARGE
    profile = EntrepreneurProfile(
        liquid_cash_inr=800_000,
        assets={AssetKind.STOREFRONT, AssetKind.COLD_STORAGE},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_raw_text="grocery store",
    )
    return (
        "DEMO 3 - STRONG ALTERNATIVE / PIVOT",
        "Synthetic (strong pivot)",
        _evidence(disc, dem),
        profile,
    )


def build_demo_4() -> Demo:
    # Comparable alternative: proposed general_store and the grocery alternative
    # both land on the SAME label (small crowded catchment); grocery edges ahead
    # on experience but by < 8 points and with no lattice advantage -> no pivot.
    disc = _discovery({C.GROCERY: 3, C.GENERAL_STORE: 3})
    dem = _demand_geolocated(persons_each=4_000, settlements=1)  # ~4k -> SMALL
    profile = EntrepreneurProfile(
        assets={AssetKind.STOREFRONT},
        experience_categories={C.GROCERY},
        proposed_category=C.GENERAL_STORE,
        proposed_raw_text="general store",
    )
    return (
        "DEMO 4 - COMPARABLE ALTERNATIVE",
        "Synthetic (comparable)",
        _evidence(disc, dem),
        profile,
    )


def build_demo_5() -> Demo:
    # Nothing observed anywhere: no businesses, no population, no settlements.
    disc = _discovery({}, raw_records=0, confidence=0.0)
    dem = _demand_empty()
    profile = EntrepreneurProfile(
        liquid_cash_inr=650_000,
        assets={AssetKind.STOREFRONT},
        experience_categories={C.DAIRY},
        proposed_category=C.GROCERY,
        proposed_raw_text="pulses grocery store",
    )
    return (
        "DEMO 5 - INSUFFICIENT EVIDENCE",
        "Synthetic (no evidence)",
        _evidence(disc, dem),
        profile,
    )


def build_demo_6() -> Demo:
    # Capital gate in isolation: pharmacy is the one strong (underserved)
    # alternative but sits above stated capital; everything else is
    # insufficient. Pharmacy must keep its score and sort LAST.
    disc = _discovery({C.PHARMACY: 2, C.CLOTHING: 8})
    dem = _demand_geolocated(persons_each=4_000, settlements=15)  # ~60k -> LARGE
    profile = EntrepreneurProfile(
        liquid_cash_inr=450_000,  # below pharmacy's 500k minimum, above grocery's typical
        proposed_category=C.GROCERY,
        proposed_raw_text="pulses grocery store",
    )
    return "DEMO 6 - CAPITAL GATE", "Synthetic (capital gate)", _evidence(disc, dem), profile


DEMOS: dict[int, Callable[[], Demo]] = {
    1: build_demo_1,
    2: build_demo_2,
    3: build_demo_3,
    4: build_demo_4,
    5: build_demo_5,
    6: build_demo_6,
}


# --------------------------------------------------------------------------
# expectations  (asserted here and in tests/test_opportunity_demos.py)
# --------------------------------------------------------------------------

_BANNED_VERDICT = ("impossible", "ineligible", "unaffordable", "cannot", "not allowed")
# phrases that would assert a LOCAL resource fact we have no data for
_BANNED_RESOURCE_CLAIM = (
    "livestock activity is",
    "livestock is strong",
    "livestock is common",
    "local livestock",
    "agricultural conditions",
    "agri activity is",
    "farming is strong",
    "cattle population",
    "crop production",
)


def _dynamic_blob(res: OpportunityAnalysisResult) -> str:
    """Every string the ENGINE generates at runtime — excludes the fixed,
    config-authored ``caveats`` (which legitimately contain 'impossible' /
    'ineligible' inside a sentence that *denies* those verdicts)."""
    parts = [res.stance_reason, *res.warnings]
    for cand in res.candidates:
        parts.extend(cand.reasons)
        parts.extend(cand.warnings)
        parts.append(cand.capital_fit_reason)
    return " ".join(parts).lower()


def _text_blob(res: OpportunityAnalysisResult) -> str:
    return " ".join([_dynamic_blob(res), *res.caveats]).lower()


def check_common(res: OpportunityAnalysisResult) -> list[str]:
    """Invariants every demo must satisfy."""
    fails: list[str] = []
    blob = _dynamic_blob(res)
    if any(w in blob for w in _BANNED_VERDICT):
        fails.append("engine-generated text leaked a hard 'impossible/ineligible' verdict")
    if any(w in blob for w in _BANNED_RESOURCE_CLAIM):
        fails.append("a reason asserted a local livestock/agri fact we have no data for")
    for cand in res.candidates:
        if cand.opportunity_score is not None and not (0 <= cand.opportunity_score <= 100):
            fails.append(f"{cand.category.value}: score {cand.opportunity_score} out of range")
        if cand.evidence_sufficient and cand.market_label.value == "insufficient_evidence":
            fails.append(f"{cand.category.value}: evidence_sufficient but label insufficient")
    ranks = [c.rank for c in res.candidates]
    if ranks != list(range(1, len(ranks) + 1)):
        fails.append("ranks are not 1..N in order")
    # OUT_OF_REACH always sorts after every non-OUT_OF_REACH candidate
    seen_out = False
    for cand in res.candidates:
        if cand.capital_fit.value == "out_of_reach":
            seen_out = True
        elif seen_out:
            fails.append(f"{cand.category.value} (affordable) ranked after an out_of_reach one")
    return fails


# --------------------------------------------------------------------------
# rendering + run
# --------------------------------------------------------------------------


def _comp(cand, name: str) -> str:  # noqa: ANN001 - local print helper
    for c in cand.components:
        if c.name == name:
            return "  -  " if c.value is None else f"{c.value:5.0f}"
    return "  -  "


def summarise(title: str, location: str, res: OpportunityAnalysisResult) -> str:
    lines = [
        "=" * 78,
        title,
        "=" * 78,
        f"Location:  {location}    Radius: {res.analysis_radius_m / 1000:.0f} km    "
        f"Proposed: {res.proposed_category.value if res.proposed_category else '(none)'}",
        f"Status:    {res.status.value}    Stance: {res.stance.value.upper()}    "
        f"Pivot: {res.recommended_pivot.value if res.recommended_pivot else '-'}",
        f"Confidence (market-data): {res.market_data_confidence:.2f}    "
        f"Profile completeness: {res.profile_completeness:.0%}",
        "",
        f"  {'#':>2}  {'category':<19}{'score':>6}  {'market label':<20}"
        f"{'mkt':>6}{'asset':>7}{'exp':>6}  {'capital':<12}{'ev':>4}{'cov':>6}",
    ]
    for cand in res.candidates:
        star = "*" if cand.is_proposed else " "
        lines.append(
            f" {star}{cand.rank:>2}  {cand.category.value:<19}"
            f"{('  -  ' if cand.opportunity_score is None else cand.opportunity_score):>6}  "
            f"{cand.market_label.value:<20}"
            f"{_comp(cand, 'market_opportunity'):>6}{_comp(cand, 'asset_fit'):>7}"
            f"{_comp(cand, 'experience_fit'):>6}  "
            f"{cand.capital_fit.value:<12}{('Y' if cand.evidence_sufficient else 'n'):>4}"
            f"{cand.coverage_confidence:>6.2f}"
        )
    lines.append("")
    lines.append(f"Stance reason: {res.stance_reason}")
    if res.recommended_pivot is not None:
        piv = next(c for c in res.candidates if c.category is res.recommended_pivot)
        lines.append(f"Pivot capital fit: {piv.capital_fit_reason}")
    lines.append(f"Key caveat: {res.caveats[2] if len(res.caveats) > 2 else res.caveats[0]}")
    return "\n".join(lines)


def run_demo(index: int, *, verbose: bool = True) -> tuple[OpportunityAnalysisResult, list[str]]:
    title, location, evidence, profile = DEMOS[index]()
    res = score_opportunities(evidence, profile)
    # determinism check
    again = score_opportunities(evidence, profile)
    fails = list(check_common(res))
    if res.model_dump(mode="json") != again.model_dump(mode="json"):
        fails.append("non-deterministic: two runs differ")
    fails.extend(_DEMO_CHECKS[index](res, evidence))
    if verbose:
        print(summarise(title, location, res))
        # extra structured lines for demos 2 / 5
        if index == 2:
            ds = compute_demand_signals(evidence.demand)
            print(
                f"Demand: status={ds.status.value}  catchment.persons="
                f"{ds.catchment.persons}  scale_tier(demo)=activity_proxy  "
                f"(no fabricated population number)"
            )
        print("CHECKS: " + ("PASS" if not fails else f"FAIL -> {fails}"))
        print()
    return res, fails


# -- per-demo expectation checks (res, evidence) -> list[failure str] -------


def _d1(res, ev):  # noqa: ANN001
    f = []
    g = next(c for c in res.candidates if c.category is C.GROCERY)
    if g.market_label.value != "insufficient_evidence":
        f.append(f"grocery label is {g.market_label.value}, expected insufficient_evidence")
    if g.coverage_confidence != 0.0:
        f.append(f"grocery coverage_confidence {g.coverage_confidence}, expected 0.0")
    if g.evidence_sufficient:
        f.append("grocery evidence_sufficient should be False (no relevant coverage)")
    if g.market_label.value == "underserved":
        f.append("zero relevant grocery coverage became underserved")
    if not any(c.evidence_sufficient for c in res.candidates):
        f.append("no alternative was assessable from the union discovery")
    # asset/experience change ranking -> re-run with a bare profile
    bare = score_opportunities(ev, EntrepreneurProfile(proposed_category=C.GROCERY))
    if [c.category for c in bare.candidates] == [c.category for c in res.candidates] and all(
        b.opportunity_score == r.opportunity_score
        for b, r in zip(bare.candidates, res.candidates, strict=False)
    ):
        f.append("asset/experience profile did not affect scoring at all")
    # capital visible but does not move the score
    rich = score_opportunities(
        ev,
        EntrepreneurProfile(
            liquid_cash_inr=5_000_000,
            assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
            experience_categories={C.DAIRY},
            proposed_category=C.GROCERY,
        ),
    )
    rb = {c.category: c.opportunity_score for c in rich.candidates}
    for c in res.candidates:
        if rb[c.category] != c.opportunity_score:
            f.append(f"{c.category.value}: score moved when only capital changed")
            break
    return f


def _d2(res, ev):  # noqa: ANN001
    f = []
    if any(c.persons_per_direct_competitor is not None for c in res.candidates):
        f.append("a persons-per-competitor ratio was produced without geolocated population")
    ds = compute_demand_signals(ev.demand)
    if ds.catchment.persons is not None:
        f.append(f"a population number ({ds.catchment.persons}) was fabricated")
    if ds.status.value != "population_not_geolocated":
        f.append(f"demand status {ds.status.value}, expected population_not_geolocated")
    if not (0.0 < res.market_data_confidence < 0.7):
        f.append(f"market_data_confidence {res.market_data_confidence} not in the proxy band")
    a = score_opportunities(ev, DEMOS[2]()[3])
    if a.model_dump(mode="json") != res.model_dump(mode="json"):
        f.append("ranking not deterministic under the activity-proxy path")
    return f


def _d3(res, ev):  # noqa: ANN001
    f = []
    if res.stance.value != "alternative_materially_better":
        f.append(f"stance {res.stance.value}, expected alternative_materially_better")
    if res.recommended_pivot is None:
        f.append("no recommended_pivot")
        return f
    piv = next(c for c in res.candidates if c.category is res.recommended_pivot)
    prop = next(c for c in res.candidates if c.category is res.proposed_category)
    lr = DEFAULT_OPPORTUNITY_CONFIG.label_lattice_rank
    if lr[piv.market_label.value] <= lr[prop.market_label.value]:
        f.append("pivot label is not strictly stronger on the lattice")
    if (piv.opportunity_score or 0) - (prop.opportunity_score or 0) < 8:
        f.append("pivot advantage is < 8 points")
    if not piv.evidence_sufficient:
        f.append("pivot is not evidence_sufficient")
    if piv.capital_fit.value not in {"affordable", "stretch"}:
        f.append("pivot capital fit is not resolved")
    return f


def _d4(res, ev):  # noqa: ANN001
    f = []
    if res.stance.value != "alternatives_comparable":
        f.append(f"stance {res.stance.value}, expected alternatives_comparable")
    if res.recommended_pivot is not None:
        f.append(f"a pivot ({res.recommended_pivot.value}) was recommended")
    prop = next(c for c in res.candidates if c.category is res.proposed_category)
    lr = DEFAULT_OPPORTUNITY_CONFIG.label_lattice_rank
    # an evidence-sufficient alternative that outscores the proposal must exist,
    # and it must fail the pivot bar only because gap < 8 OR no lattice advantage
    higher = [
        c
        for c in res.candidates
        if c.category is not prop.category
        and c.evidence_sufficient
        and (c.opportunity_score or 0) > (prop.opportunity_score or 0)
    ]
    if not higher:
        f.append("no evidence-sufficient alternative scored higher than the proposed business")
        return f
    top = higher[0]
    gap = (top.opportunity_score or 0) - (prop.opportunity_score or 0)
    strictly_better = lr[top.market_label.value] > lr[prop.market_label.value]
    if gap >= 8 and strictly_better:
        f.append("the higher alternative clears both pivot gates - it should have been a pivot")
    return f


def _d5(res, ev):  # noqa: ANN001
    f = []
    if res.status.value != "no_evidence":
        f.append(f"status {res.status.value}, expected no_evidence")
    if res.stance.value != "no_recommendation":
        f.append(f"stance {res.stance.value}, expected no_recommendation")
    if res.recommended_pivot is not None:
        f.append("a pivot was recommended with no evidence")
    if any(c.market_label.value != "insufficient_evidence" for c in res.candidates):
        f.append("some candidate was assessed despite no evidence")
    if "strong opportunity" in _text_blob(res) or "good opportunity" in _text_blob(res):
        f.append("a candidate was described as a strong/good opportunity")
    return f


def _d6(res, ev):  # noqa: ANN001
    f = []
    ph = next(c for c in res.candidates if c.category is C.PHARMACY)
    if ph.market_label.value != "underserved":
        f.append(f"pharmacy label {ph.market_label.value}, expected underserved")
    if ph.capital_fit.value != "out_of_reach":
        f.append(f"pharmacy capital_fit {ph.capital_fit.value}, expected out_of_reach")
    top_score = max(c.opportunity_score or 0 for c in res.candidates)
    if (ph.opportunity_score or 0) != top_score:
        f.append("pharmacy is not the top-scoring candidate")
    if ph.rank != len(res.candidates):
        f.append(f"pharmacy rank {ph.rank}, expected last ({len(res.candidates)})")
    if "below the indicative minimum" not in ph.capital_fit_reason:
        f.append("capital gate not clearly reported on pharmacy")
    if "not a financial assessment" not in ph.capital_fit_reason:
        f.append("capital reason omits the 'not a financial assessment' qualifier")
    # score unchanged when capital is no longer binding
    rich = score_opportunities(
        ev, EntrepreneurProfile(liquid_cash_inr=5_000_000, proposed_category=C.GROCERY)
    )
    ph_rich = next(c for c in rich.candidates if c.category is C.PHARMACY)
    if ph_rich.opportunity_score != ph.opportunity_score:
        f.append("pharmacy score changed when only capital changed")
    if ph_rich.rank != 1:
        f.append("pharmacy did not rise to rank 1 once affordable")
    return f


_DEMO_CHECKS: dict[int, Callable[[OpportunityAnalysisResult, OpportunityEvidence], list[str]]] = {
    1: _d1,
    2: _d2,
    3: _d3,
    4: _d4,
    5: _d5,
    6: _d6,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    indices = [int(a) for a in args] if args else sorted(DEMOS)
    all_fails: dict[int, list[str]] = {}
    for i in indices:
        _, fails = run_demo(i)
        if fails:
            all_fails[i] = fails
    print("=" * 78)
    if all_fails:
        print(f"RESULT: {len(all_fails)} demo(s) FAILED expectations: {all_fails}")
        return 1
    print(f"RESULT: all {len(indices)} demo(s) matched their expected Phase 3 behaviour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
