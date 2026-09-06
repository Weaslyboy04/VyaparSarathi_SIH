"""Phase 2D overall market assessment (CLAUDE.md §11, §12, §22). Pure & offline —
every input is a hand-built 2B / 2C result; no HTTP, no fixtures."""

from __future__ import annotations

import json
from itertools import product

import pytest

from vyaparsarathi.market.assessment import assess_market
from vyaparsarathi.market.assessment_config import AssessmentConfig
from vyaparsarathi.market.assessment_findings import FINDING_RULES
from vyaparsarathi.market.assessment_models import (
    CatchmentScale,
    LadderRung,
    MarketAssessmentLabel,
    MarketAssessmentStatus,
    ScaleTier,
)
from vyaparsarathi.market.demand_models import (
    ActivitySummary,
    CatchmentPopulation,
    DemandCoverage,
    DemandSignalsResult,
    DemandStatus,
)
from vyaparsarathi.market.metrics_models import (
    CompetitionDensity,
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
    CompetitionSignal,
    DistanceStats,
)
from vyaparsarathi.market.models import CompetitorAnalysisResult, CompetitorAnalysisStatus
from vyaparsarathi.market.proposed import proposed_from_category
from vyaparsarathi.models.demand import ActivityKind
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_AREA = 201.06  # pi * 8^2


def _metrics(
    signal: CompetitionSignal,
    direct: int,
    *,
    conf: float = 0.8,
    radius: int = 8_000,
    category: C = C.GROCERY,
    status: CompetitionMetricsStatus = CompetitionMetricsStatus.OK,
    nearest_m: float | None = None,
    adjacent: int = 0,
    missing_distance: int = 0,
    lat: float | None = 25.7,
    lon: float | None = 85.2,
) -> CompetitionMetricsResult:
    have = nearest_m is not None
    return CompetitionMetricsResult(
        status=status,
        proposed_category=category,
        analysis_radius_m=radius,
        query_latitude=lat,
        query_longitude=lon,
        direct_count=direct,
        adjacent_count=adjacent,
        total_relevant_count=direct + adjacent,
        direct_distance=DistanceStats(
            count=1 if have else 0,
            missing_distance=missing_distance,
            nearest_m=nearest_m,
            farthest_m=nearest_m,
            mean_m=nearest_m,
            median_m=nearest_m,
        ),
        density=CompetitionDensity(
            catchment_area_km2=_AREA, direct_per_km2=direct / _AREA if direct else 0.0
        ),
        signal=signal,
        signal_reason="test signal reason",
        data_confidence=conf,
    )


def _demand(
    persons: int | None,
    *,
    radius: int = 8_000,
    status: DemandStatus = DemandStatus.OK,
    coverage: float | None = 1.0,
    settlements: int = 10,
    kinds: tuple[ActivityKind, ...] = (),
    conf: float = 0.6,
    ungeolocated: int = 0,
    is_floor: bool = False,
    lat: float | None = 25.7,
    lon: float | None = 85.2,
    per_1000: float | None = None,
) -> DemandSignalsResult:
    counts = dict.fromkeys(kinds, 1)
    return DemandSignalsResult(
        status=status,
        analysis_radius_m=radius,
        query_latitude=lat,
        query_longitude=lon,
        catchment=CatchmentPopulation(
            persons=persons,
            households=None,
            is_floor=(is_floor if persons is not None else None),
            settlements_found=settlements,
            settlements_with_population=settlements if persons is not None else 0,
            population_coverage=(coverage if persons is not None else None),
            catchment_area_km2=_AREA,
        ),
        activity=ActivitySummary(
            total_points=len(kinds),
            counts_by_kind=counts,
            nearest_m_by_kind=dict.fromkeys(kinds, 500.0),
        ),
        competitors_per_1000_people=per_1000,
        demand_data_confidence=conf,
        coverage=DemandCoverage(
            population_records_ungeolocated_in_area=ungeolocated,
            population_available_not_geolocated=(ungeolocated > 0 and persons is None),
        ),
    )


# -- the quadrants --------------------------------------------------------


@pytest.mark.parametrize(
    ("persons", "signal", "expected"),
    [
        (30_000, CompetitionSignal.LOW, MarketAssessmentLabel.UNDERSERVED),
        (30_000, CompetitionSignal.HIGH, MarketAssessmentLabel.SERVED),
        (1_200, CompetitionSignal.HIGH, MarketAssessmentLabel.CROWDED),
        (1_200, CompetitionSignal.LOW, MarketAssessmentLabel.THIN_MARKET),
        (8_000, CompetitionSignal.MODERATE, MarketAssessmentLabel.MIXED),
    ],
)
def test_matrix_quadrants(
    persons: int, signal: CompetitionSignal, expected: MarketAssessmentLabel
) -> None:
    direct = {CompetitionSignal.LOW: 1, CompetitionSignal.MODERATE: 4, CompetitionSignal.HIGH: 9}[
        signal
    ]
    r = assess_market(_metrics(signal, direct, conf=0.8), _demand(persons))
    assert r.status is MarketAssessmentStatus.OK
    assert r.label is expected
    assert r.label_basis["rung"] == LadderRung.MATRIX.value


def test_zero_competitors_thin_catchment_is_thin_market_not_underserved() -> None:
    r = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.8), _demand(1_200))
    assert r.label is MarketAssessmentLabel.THIN_MARKET
    assert r.label is not MarketAssessmentLabel.UNDERSERVED
    assert r.label_basis["matrix_key"] == "small/none"


# -- the ladder ------------------------------------------------------


_METRICS_STATUSES = list(CompetitionMetricsStatus)
_DEMAND_STATUSES = list(DemandStatus)


@pytest.mark.parametrize(("mstat", "dstat"), list(product(_METRICS_STATUSES, _DEMAND_STATUSES)))
def test_upstream_status_cartesian_product(
    mstat: CompetitionMetricsStatus, dstat: DemandStatus
) -> None:
    """Every unhealthy upstream combination stops at rung 0 with INSUFFICIENT_EVIDENCE;
    healthy / proxy-tier combinations pass through it."""
    m = _metrics(CompetitionSignal.HIGH, 9, status=mstat)  # booby-trapped fields
    d = _demand(30_000, status=dstat)
    r = assess_market(m, d)

    m_ok = mstat is CompetitionMetricsStatus.OK
    d_blocking = dstat in {
        DemandStatus.LOCATION_UNRESOLVED,
        DemandStatus.SOURCE_UNAVAILABLE,
        DemandStatus.INVALID_RADIUS,
    }
    if m_ok and not d_blocking:
        assert r.status is MarketAssessmentStatus.OK
        assert r.label_basis["rung"] != LadderRung.UPSTREAM_STATUS.value
    else:
        assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
        assert r.label_basis["rung"] == LadderRung.UPSTREAM_STATUS.value
        assert r.status is not MarketAssessmentStatus.OK
        # the booby-trapped competition fields were never read
        assert r.competition_summary is None


def test_competition_none_is_never_low_competition() -> None:
    # a non-OK 2B result whose signal defaults to NONE and direct_count to 0
    m = CompetitionMetricsResult(
        status=CompetitionMetricsStatus.UNKNOWN_CATEGORY,
        proposed_category=C.GROCERY,
        analysis_radius_m=8_000,
    )
    r = assess_market(m, _demand(30_000))
    assert r.status is MarketAssessmentStatus.UNKNOWN_CATEGORY
    assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert r.label_basis["rung"] == LadderRung.UPSTREAM_STATUS.value
    assert r.competition_summary is None  # never touched metrics.signal

    m2 = CompetitionMetricsResult(
        status=CompetitionMetricsStatus.INVALID_RADIUS,
        proposed_category=C.GROCERY,
        analysis_radius_m=8_000,
    )
    r2 = assess_market(m2, _demand(30_000))
    assert r2.status is MarketAssessmentStatus.INVALID_RADIUS
    assert r2.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE


def test_no_upstream_field_read_before_gate() -> None:
    booby = _metrics(CompetitionSignal.HIGH, 999, status=CompetitionMetricsStatus.INVALID_RADIUS)
    r = assess_market(booby, _demand(30_000))
    assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert r.competition_summary is None
    assert "999" not in json.dumps(r.model_dump(), default=str)


def test_rung_absence_not_evidence() -> None:
    low = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.3), _demand(30_000))
    assert low.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert low.label_basis["rung"] == LadderRung.ABSENCE_NOT_EVIDENCE.value
    # same inputs, confidence above the threshold -> the matrix runs
    ok = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.8), _demand(30_000))
    assert ok.label is MarketAssessmentLabel.UNDERSERVED
    assert ok.label_basis["rung"] == LadderRung.MATRIX.value


def test_rung_scale_unknown() -> None:
    r = assess_market(
        _metrics(CompetitionSignal.LOW, 2, conf=0.8),
        _demand(None, settlements=0, kinds=(), status=DemandStatus.NO_SETTLEMENTS_FOUND),
    )
    assert r.status is MarketAssessmentStatus.OK
    assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert r.label_basis["rung"] == LadderRung.SCALE_UNKNOWN.value


def test_rung_nothing_observed() -> None:
    # a real persons==0 catchment gives scale SMALL, so drive "nothing observed"
    # with zero settlements + zero competitors and NO population figure but one
    # activity kind to keep scale != UNKNOWN would flip to rung 2; instead use
    # persons=0 with settlements_found=0.
    d = _demand(0, settlements=0)
    r = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.9), d)
    assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert r.label_basis["rung"] == LadderRung.NOTHING_OBSERVED.value


def test_rung_inconsistent_inputs_radius_and_point_and_category() -> None:
    r = assess_market(
        _metrics(CompetitionSignal.LOW, 2, radius=8_000), _demand(5_000, radius=5_000)
    )
    assert r.status is MarketAssessmentStatus.INCONSISTENT_INPUTS
    assert r.label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert r.label_basis["rung"] == LadderRung.INCONSISTENT_INPUTS.value

    r2 = assess_market(_metrics(CompetitionSignal.LOW, 2, lat=25.7), _demand(5_000, lat=25.9))
    assert r2.status is MarketAssessmentStatus.INCONSISTENT_INPUTS

    analysis = CompetitorAnalysisResult(
        status=CompetitorAnalysisStatus.OK, proposed=proposed_from_category(C.DAIRY)
    )
    r3 = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(5_000), analysis=analysis)
    assert r3.status is MarketAssessmentStatus.INCONSISTENT_INPUTS


# -- catchment scale tiers ---------------------------------------


def test_scale_tier_population() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(30_000))
    assert r.catchment_scale is CatchmentScale.LARGE
    assert r.scale_basis.tier is ScaleTier.POPULATION
    assert r.scale_basis.capped_by_tier is False


def test_scale_tier_activity_proxy_when_persons_none() -> None:
    r = assess_market(
        _metrics(CompetitionSignal.LOW, 2),
        _demand(
            None,
            settlements=37,
            kinds=(ActivityKind.SCHOOL, ActivityKind.BANK),
            ungeolocated=27_800,
        ),
    )
    assert r.scale_basis.tier is ScaleTier.ACTIVITY_PROXY
    assert r.catchment_scale is CatchmentScale.MODERATE
    assert r.scale_basis.capped_by_tier is True


def test_proxy_tier_capped_at_moderate_even_with_many_settlements() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(None, settlements=500))
    assert r.catchment_scale is CatchmentScale.MODERATE  # never LARGE from a proxy
    assert r.scale_basis.tier is ScaleTier.ACTIVITY_PROXY


def test_scale_degrades_to_proxy_when_population_coverage_too_low() -> None:
    r = assess_market(
        _metrics(CompetitionSignal.LOW, 2), _demand(30_000, coverage=0.1, settlements=20)
    )
    assert r.scale_basis.tier is ScaleTier.ACTIVITY_PROXY
    assert r.scale_basis.persons == 30_000  # figure retained, just not trusted for scale


def test_persons_zero_is_small_not_unknown() -> None:
    r = assess_market(_metrics(CompetitionSignal.HIGH, 9), _demand(0, settlements=5))
    assert r.catchment_scale is CatchmentScale.SMALL
    assert r.scale_basis.tier is ScaleTier.POPULATION
    assert r.label is MarketAssessmentLabel.CROWDED


def test_scale_basis_tier_is_always_populated() -> None:
    for d in (_demand(30_000), _demand(None, settlements=20), _demand(None, settlements=0)):
        r = assess_market(_metrics(CompetitionSignal.LOW, 2), d)
        assert r.scale_basis.tier in set(ScaleTier)


# -- floor population ------------------------------------------


def test_floor_population_raises_caveat_and_marks_thin_market_provisional() -> None:
    r = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.9), _demand(1_200, is_floor=True))
    assert r.label is MarketAssessmentLabel.THIN_MARKET
    assert any(f.code == "population_is_floor" for f in r.data_caveats)
    assert any("lower-bound population" in w for w in r.warnings)


# -- confidence -----------------------------------------------


def test_confidence_is_min_of_the_two_inputs_times_tier_penalty() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2, conf=0.9), _demand(30_000, conf=0.4))
    assert r.assessment_data_confidence == pytest.approx(0.4)  # min, population tier -> penalty 1.0
    proxy = assess_market(
        _metrics(CompetitionSignal.LOW, 2, conf=0.9), _demand(None, settlements=20, conf=0.4)
    )
    assert proxy.assessment_data_confidence == pytest.approx(0.4 * 0.7)


def test_low_confidence_never_changes_the_label() -> None:
    hi = assess_market(_metrics(CompetitionSignal.LOW, 1, conf=0.95), _demand(30_000, conf=0.95))
    lo = assess_market(_metrics(CompetitionSignal.LOW, 1, conf=0.40), _demand(30_000, conf=0.40))
    assert hi.label is lo.label is MarketAssessmentLabel.UNDERSERVED
    assert hi.assessment_data_confidence != lo.assessment_data_confidence
    assert any(f.code == "low_market_data_confidence" for f in lo.data_caveats)
    assert not any(f.code == "low_market_data_confidence" for f in hi.data_caveats)


# -- findings ----------------------------------------------


def test_findings_are_bucketed_and_evidence_resolves_to_the_actual_upstream_value() -> None:
    metrics = _metrics(CompetitionSignal.HIGH, 9, conf=0.8, nearest_m=250.0, adjacent=5)
    demand = _demand(
        1_200, is_floor=True, kinds=(ActivityKind.MARKETPLACE, ActivityKind.TRANSPORT_STOP)
    )
    r = assess_market(metrics, demand)

    all_findings = [*r.positive_signals, *r.concerns, *r.data_caveats]
    assert all_findings
    codes = [f.code for f in all_findings]
    assert len(codes) == len(set(codes))  # unique within a result

    sources = {"competition": metrics, "demand": demand}
    for finding in all_findings:
        assert finding.message and finding.code
        assert finding.evidence  # every fired rule attaches at least one ref
        for ref in finding.evidence:
            if ref.source == "analysis":
                continue
            obj: object = sources[ref.source]
            for part in ref.field.split("."):
                obj = obj[part] if isinstance(obj, dict) else getattr(obj, part)
            if isinstance(obj, float) and isinstance(ref.value, (int, float)):
                assert obj == pytest.approx(ref.value)
            else:
                assert obj == ref.value


def test_every_finding_code_has_a_message_template() -> None:
    cfg = AssessmentConfig()
    # exercise a broad input so many rules fire, then assert no KeyError leaked
    r = assess_market(
        _metrics(
            CompetitionSignal.HIGH, 9, conf=0.3, nearest_m=200.0, adjacent=9, missing_distance=0
        ),
        _demand(None, settlements=2, kinds=(ActivityKind.MARKETPLACE,), ungeolocated=100),
    )
    for f in (*r.positive_signals, *r.concerns, *r.data_caveats):
        assert f.code in cfg.finding_messages
    # every template key is exactly one rule code (rule fn name minus its leading _)
    fn_codes = {fn.__name__[1:] for fn in FINDING_RULES}
    assert set(cfg.finding_messages) == fn_codes


def test_competitor_absence_low_coverage_caveat_fires() -> None:
    r = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.2), _demand(30_000))
    assert any(f.code == "competitor_absence_low_coverage" for f in r.data_caveats)


def test_distances_unavailable_uses_distance_count_not_direct_count() -> None:
    m = _metrics(CompetitionSignal.HIGH, 3, conf=0.8, nearest_m=None, missing_distance=3)
    r = assess_market(m, _demand(30_000))
    assert any(f.code == "distances_unavailable" for f in r.data_caveats)
    assert not any(f.code == "nearest_competitor_distant" for f in r.positive_signals)
    assert not any(f.code == "competitor_very_close" for f in r.concerns)


# -- persons_per_direct_competitor -----------------------------


def test_persons_per_direct_competitor_present_only_with_population_and_competitors() -> None:
    r = assess_market(_metrics(CompetitionSignal.MODERATE, 4), _demand(20_000))
    assert r.persons_per_direct_competitor == pytest.approx(5_000.0)
    assert (
        assess_market(
            _metrics(CompetitionSignal.NONE, 0), _demand(20_000)
        ).persons_per_direct_competitor
        is None
    )
    assert (
        assess_market(
            _metrics(CompetitionSignal.MODERATE, 4), _demand(None, settlements=20)
        ).persons_per_direct_competitor
        is None
    )


def test_warns_when_ratio_was_computable_but_2c_lacked_the_2b_result() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(20_000, per_1000=None))
    assert any("competitors-per-1,000" in w for w in r.warnings)


# -- Phase 3 boundary --------------------------------------


def _all_keys(obj: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            keys |= _all_keys(v)
    return keys


def test_no_score_or_recommendation_field_anywhere() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(20_000))
    keys = {k.lower() for k in _all_keys(r.model_dump())}
    banned_substrings = ("score", "opportunity", "rating", "rank", "recommend", "viab", "verdict")
    offending = {k for k in keys for b in banned_substrings if b in k}
    assert not offending, offending
    # labels are market-state nouns, never action verbs
    for label in MarketAssessmentLabel:
        assert label.value not in {"proceed", "pivot", "avoid", "recommend", "viable"}


def test_signature_takes_no_capital_or_profile() -> None:
    import inspect

    params = set(inspect.signature(assess_market).parameters)
    assert params == {"metrics", "demand", "analysis", "config"}


# -- determinism + serialization -----------------------------


def test_deterministic_and_order_independent() -> None:
    m = _metrics(CompetitionSignal.MODERATE, 4, nearest_m=300.0)
    d = _demand(8_000, kinds=(ActivityKind.SCHOOL,))
    assert assess_market(m, d).model_dump() == assess_market(m, d).model_dump()


def test_json_round_trip_with_config_echoed() -> None:
    r = assess_market(_metrics(CompetitionSignal.LOW, 2), _demand(20_000))
    payload = json.loads(r.model_dump_json())
    assert payload["status"] == "ok"
    assert payload["config"]["population_large"] == 20_000
    assert payload["label_basis"]["rung"] == "matrix"
    assert payload["assessment_caveats"]


def test_config_matrix_is_tunable() -> None:
    cfg = AssessmentConfig(
        label_matrix={
            "small": {"none": "mixed", "low": "mixed", "moderate": "mixed", "high": "mixed"},
            "moderate": {"none": "mixed", "low": "mixed", "moderate": "mixed", "high": "mixed"},
            "large": {"none": "mixed", "low": "mixed", "moderate": "mixed", "high": "mixed"},
        }
    )
    r = assess_market(_metrics(CompetitionSignal.NONE, 0, conf=0.9), _demand(1_200), config=cfg)
    assert r.label is MarketAssessmentLabel.MIXED
