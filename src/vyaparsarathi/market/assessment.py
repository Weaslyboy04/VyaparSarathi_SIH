"""Deterministic Phase 2D engine — overall market assessment (CLAUDE.md §11, §22).

Fuses a Phase 2B :class:`CompetitionMetricsResult` and a Phase 2C
:class:`DemandSignalsResult` (optionally a Phase 2A
:class:`CompetitorAnalysisResult`, used only to name competitors in findings)
into a single :class:`MarketAssessmentResult` — a label, never a score.

Pure: no network, disk, wall-clock, RNG or LLM. Consumes only result objects
(the ``market/`` no-I/O invariant, enforced by ``tests/test_market_purity.py``).

Control flow, in order:

1. **Rung 0 — upstream status gate.** The *only* field of ``metrics`` / ``demand``
   read before this point is ``.status``. ``metrics.signal`` defaults to
   ``CompetitionSignal.NONE`` and ``direct_count`` to ``0`` on a non-OK 2B, so
   reading them first would turn "could not compute" into "no competition" — the
   exact input that yields ``UNDERSERVED``.
2. **Rung 1 — input consistency.** 2B and 2C must describe the same catchment.
3. **Catchment scale** — tiered: Census population, else an activity/settlement
   proxy capped at ``MODERATE``, else ``UNKNOWN``.
4. **Rungs 2–4** — structural refusals (unknown scale; nothing observed; a
   zero-competitor claim under low coverage).
5. **Label** — a lookup in ``config.label_matrix``.
6. **Findings** and **confidence** — confidence is ``min(2B, 2C) × tier penalty``
   and can never change the label.
"""

from __future__ import annotations

from vyaparsarathi.market.assessment_config import DEFAULT_ASSESSMENT_CONFIG, AssessmentConfig
from vyaparsarathi.market.assessment_findings import FINDING_RULES, _FindingCtx
from vyaparsarathi.market.assessment_models import (
    CatchmentScale,
    CompetitionSummary,
    DemandSummary,
    Finding,
    FindingKind,
    LadderRung,
    MarketAssessmentLabel,
    MarketAssessmentResult,
    MarketAssessmentStatus,
    ScaleBasis,
    ScaleTier,
)
from vyaparsarathi.market.demand_models import DemandSignalsResult, DemandStatus
from vyaparsarathi.market.metrics_models import (
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
    CompetitionSignal,
)
from vyaparsarathi.market.models import CompetitorAnalysisResult, CompetitorAnalysisStatus
from vyaparsarathi.utils.geo import haversine_m
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_SCALE_RANK: dict[CatchmentScale, int] = {
    CatchmentScale.UNKNOWN: 0,
    CatchmentScale.SMALL: 1,
    CatchmentScale.MODERATE: 2,
    CatchmentScale.LARGE: 3,
}


# -- rung 0: upstream status -------------------------------------------


def _upstream_gate(
    metrics: CompetitionMetricsResult,
    demand: DemandSignalsResult,
    analysis: CompetitorAnalysisResult | None,
) -> tuple[MarketAssessmentStatus, str] | None:
    """Map an unhealthy upstream status to a 2D status + message. 2B is checked
    first — a failed competition read means the primary axis is unavailable."""
    if analysis is not None and analysis.status is not CompetitorAnalysisStatus.OK:
        return (
            MarketAssessmentStatus.UNKNOWN_CATEGORY,
            "Phase 2A could not map the proposed business to a category; clarify the "
            "business type.",
        )
    if metrics.status is CompetitionMetricsStatus.UNKNOWN_CATEGORY:
        return (
            MarketAssessmentStatus.UNKNOWN_CATEGORY,
            "Phase 2B could not map the proposed business to a category; clarify the "
            "business type.",
        )
    if metrics.status is CompetitionMetricsStatus.INVALID_RADIUS:
        return (
            MarketAssessmentStatus.INVALID_RADIUS,
            "Phase 2B reported an invalid analysis radius; no market assessment is possible.",
        )
    if demand.status is DemandStatus.LOCATION_UNRESOLVED:
        return (
            MarketAssessmentStatus.LOCATION_UNRESOLVED,
            "Phase 2C had no resolved catchment centre; no market assessment is possible.",
        )
    if demand.status is DemandStatus.SOURCE_UNAVAILABLE:
        return (
            MarketAssessmentStatus.SOURCE_UNAVAILABLE,
            "Phase 2C demand acquisition failed entirely; no market assessment is possible.",
        )
    if demand.status is DemandStatus.INVALID_RADIUS:
        return (
            MarketAssessmentStatus.INVALID_RADIUS,
            "Phase 2C reported an invalid analysis radius; no market assessment is possible.",
        )
    return None  # both healthy enough to continue (2C proxy-tier statuses are fine)


# -- rung 1: input consistency ---------------------------------------


def _consistency_error(
    metrics: CompetitionMetricsResult,
    demand: DemandSignalsResult,
    analysis: CompetitorAnalysisResult | None,
    cfg: AssessmentConfig,
) -> str | None:
    if metrics.analysis_radius_m != demand.analysis_radius_m:
        return (
            f"Phase 2B and Phase 2C used different analysis radii "
            f"({metrics.analysis_radius_m} m vs {demand.analysis_radius_m} m); "
            "densities and per-capita ratios over different circles are not comparable."
        )
    m_pt = (metrics.query_latitude, metrics.query_longitude)
    d_pt = (demand.query_latitude, demand.query_longitude)
    m_has = None not in m_pt
    d_has = None not in d_pt
    if m_has != d_has:
        return "Phase 2B and Phase 2C disagree on whether a query point is known."
    if m_has and d_has:
        gap = haversine_m(m_pt[0], m_pt[1], d_pt[0], d_pt[1])  # type: ignore[arg-type]
        if gap > cfg.max_point_divergence_m:
            return (
                f"Phase 2B and Phase 2C query points are {gap:.0f} m apart, beyond the "
                f"{cfg.max_point_divergence_m:.0f} m tolerance."
            )
    if analysis is not None and analysis.proposed.category != metrics.proposed_category:
        return (
            f"Phase 2A proposed category ({analysis.proposed.category.value}) does not match "
            f"Phase 2B ({metrics.proposed_category.value})."
        )
    return None


# -- catchment scale -------------------------------------------------


def _min_scale(a: CatchmentScale, b: CatchmentScale) -> CatchmentScale:
    return a if _SCALE_RANK[a] <= _SCALE_RANK[b] else b


def _catchment_scale(
    demand: DemandSignalsResult, cfg: AssessmentConfig
) -> tuple[CatchmentScale, ScaleBasis]:
    c = demand.catchment
    persons = c.persons
    coverage = c.population_coverage
    settlements = c.settlements_found
    distinct_kinds = len(demand.activity.counts_by_kind)
    activity_points = demand.activity.total_points
    thresholds = {
        "population_large": float(cfg.population_large),
        "population_moderate": float(cfg.population_moderate),
        "min_population_coverage_for_scale": cfg.min_population_coverage_for_scale,
        "proxy_settlements_moderate": float(cfg.proxy_settlements_moderate),
        "proxy_settlements_small": float(cfg.proxy_settlements_small),
        "proxy_kinds_moderate": float(cfg.proxy_kinds_moderate),
    }

    pop_usable = (
        persons is not None
        and coverage is not None
        and coverage >= cfg.min_population_coverage_for_scale
    )

    if pop_usable:
        assert persons is not None
        if persons >= cfg.population_large:
            scale = CatchmentScale.LARGE
        elif persons >= cfg.population_moderate:
            scale = CatchmentScale.MODERATE
        else:
            scale = CatchmentScale.SMALL  # includes a real persons == 0
        reason = (
            f"Catchment scale from Census 2011 population: {persons:,} residents "
            f"(coverage {coverage:.0%}) vs moderate {cfg.population_moderate:,} / "
            f"large {cfg.population_large:,} -> {scale.value}."
        )
        return scale, ScaleBasis(
            tier=ScaleTier.POPULATION,
            persons=persons,
            persons_is_lower_bound=bool(c.is_floor),
            population_coverage=coverage,
            settlements_found=settlements,
            settlements_with_population=c.settlements_with_population,
            activity_points=activity_points,
            distinct_activity_kinds=distinct_kinds,
            thresholds=thresholds,
            capped_by_tier=False,
            reason=reason,
        )

    if settlements > 0 or activity_points > 0 or distinct_kinds > 0:
        if (
            settlements >= cfg.proxy_settlements_moderate
            or distinct_kinds >= cfg.proxy_kinds_moderate
        ):
            raw = CatchmentScale.MODERATE
        elif settlements >= cfg.proxy_settlements_small or distinct_kinds >= 1:
            raw = CatchmentScale.SMALL
        else:
            raw = CatchmentScale.UNKNOWN
        cap = CatchmentScale(cfg.proxy_max_scale)
        scale = _min_scale(raw, cap)
        tier = ScaleTier.ACTIVITY_PROXY if scale is not CatchmentScale.UNKNOWN else ScaleTier.NONE
        cov_txt = "unknown" if coverage is None else f"{coverage:.0%}"
        why_pop_absent = (
            "no usable population figure"
            if persons is None
            else f"population coverage {cov_txt} below {cfg.min_population_coverage_for_scale:.0%}"
        )
        reason = (
            f"Catchment scale from activity proxy ({why_pop_absent}): {settlements} settlement(s), "
            f"{distinct_kinds} anchor kind(s) vs thresholds -> {scale.value} "
            f"(proxy tier cannot exceed {cfg.proxy_max_scale})."
        )
        return scale, ScaleBasis(
            tier=tier,
            persons=persons,
            persons_is_lower_bound=bool(c.is_floor),
            population_coverage=coverage,
            settlements_found=settlements,
            settlements_with_population=c.settlements_with_population,
            activity_points=activity_points,
            distinct_activity_kinds=distinct_kinds,
            thresholds=thresholds,
            capped_by_tier=tier is ScaleTier.ACTIVITY_PROXY,
            reason=reason,
        )

    return CatchmentScale.UNKNOWN, ScaleBasis(
        tier=ScaleTier.NONE,
        persons=persons,
        persons_is_lower_bound=bool(c.is_floor),
        population_coverage=coverage,
        settlements_found=settlements,
        settlements_with_population=c.settlements_with_population,
        activity_points=activity_points,
        distinct_activity_kinds=distinct_kinds,
        thresholds=thresholds,
        capped_by_tier=False,
        reason=(
            "No population figure and no settlements or activity anchors in the catchment; "
            "catchment scale is unknown."
        ),
    )


# -- rungs 2-4 + matrix -------------------------------------------


def _decide_label(
    metrics: CompetitionMetricsResult,
    demand: DemandSignalsResult,
    scale: CatchmentScale,
    scale_basis: ScaleBasis,
    cfg: AssessmentConfig,
) -> tuple[MarketAssessmentLabel, LadderRung, str | None, str, list[str]]:
    if scale is CatchmentScale.UNKNOWN:
        return (
            MarketAssessmentLabel.INSUFFICIENT_EVIDENCE,
            LadderRung.SCALE_UNKNOWN,
            None,
            "Catchment scale could not be established from population or activity proxies; "
            "no market label.",
            [],
        )
    if demand.catchment.settlements_found == 0 and metrics.direct_count == 0:
        return (
            MarketAssessmentLabel.INSUFFICIENT_EVIDENCE,
            LadderRung.NOTHING_OBSERVED,
            None,
            "No settlements and no competitors were observed in the catchment; there is "
            "nothing to assess.",
            [],
        )
    if (
        metrics.signal is CompetitionSignal.NONE
        and metrics.data_confidence < cfg.min_confidence_for_absence_claim
    ):
        return (
            MarketAssessmentLabel.INSUFFICIENT_EVIDENCE,
            LadderRung.ABSENCE_NOT_EVIDENCE,
            None,
            f"Zero direct competitors were found, but competition-data confidence "
            f"({metrics.data_confidence:.2f}) is below {cfg.min_confidence_for_absence_claim:.2f}; "
            "absence of evidence is not evidence of absence.",
            [],
        )

    scale_key = scale.value
    signal_key = metrics.signal.value
    label = MarketAssessmentLabel(cfg.label_matrix[scale_key][signal_key])
    warns: list[str] = []
    if metrics.signal is CompetitionSignal.NONE:
        warns.append(
            "No direct competitors were found; treated as low competition for the market label, "
            "but source coverage may be the cause rather than an empty market."
        )
    if label is MarketAssessmentLabel.THIN_MARKET and demand.catchment.is_floor:
        warns.append(
            "This thin-market reading rests on a lower-bound population figure; the true "
            "catchment may be larger."
        )
    reason = (
        f"Catchment is {scale_key} ({scale_basis.reason}) and competition is {signal_key} "
        f"({metrics.signal_reason}); matrix[{scale_key}][{signal_key}] -> {label.value}."
    )
    return label, LadderRung.MATRIX, f"{scale_key}/{signal_key}", reason, warns


# -- confidence ---------------------------------------------------


def _confidence(
    metrics: CompetitionMetricsResult,
    demand: DemandSignalsResult,
    scale_basis: ScaleBasis,
    cfg: AssessmentConfig,
) -> tuple[float, dict[str, float | None]]:
    lo = min(metrics.data_confidence, demand.demand_data_confidence)
    penalty = cfg.proxy_tier_penalty if scale_basis.tier is ScaleTier.ACTIVITY_PROXY else 1.0
    conf = round(lo * penalty, 3)
    basis: dict[str, float | None] = {
        "competition_data_confidence": round(metrics.data_confidence, 3),
        "demand_data_confidence": round(demand.demand_data_confidence, 3),
        "weaker_input_value": round(lo, 3),
        "min_input_is_competition": 1.0
        if metrics.data_confidence <= demand.demand_data_confidence
        else 0.0,
        "tier_penalty": penalty,
    }
    return conf, basis


def _persons_per_direct_competitor(
    metrics: CompetitionMetricsResult, demand: DemandSignalsResult
) -> float | None:
    persons = demand.catchment.persons
    if persons is None or metrics.status is not CompetitionMetricsStatus.OK:
        return None
    if metrics.direct_count <= 0:
        return None
    return round(persons / metrics.direct_count, 1)


def _summaries(
    metrics: CompetitionMetricsResult, demand: DemandSignalsResult
) -> tuple[CompetitionSummary, DemandSummary]:
    comp = CompetitionSummary(
        signal=metrics.signal,
        direct_count=metrics.direct_count,
        adjacent_count=metrics.adjacent_count,
        nearest_direct_m=metrics.direct_distance.nearest_m,
        direct_per_km2=metrics.density.direct_per_km2,
        data_confidence=metrics.data_confidence,
    )
    dem = DemandSummary(
        persons=demand.catchment.persons,
        persons_is_lower_bound=bool(demand.catchment.is_floor),
        population_coverage=demand.catchment.population_coverage,
        settlements_found=demand.catchment.settlements_found,
        activity_points=demand.activity.total_points,
        distinct_activity_kinds=len(demand.activity.counts_by_kind),
        population_available_not_geolocated=demand.coverage.population_available_not_geolocated,
        demand_data_confidence=demand.demand_data_confidence,
    )
    return comp, dem


def assess_market(
    metrics: CompetitionMetricsResult,
    demand: DemandSignalsResult,
    *,
    analysis: CompetitorAnalysisResult | None = None,
    config: AssessmentConfig | None = None,
) -> MarketAssessmentResult:
    """Combine Phase 2B competition and Phase 2C demand into a market label."""
    cfg = config or DEFAULT_ASSESSMENT_CONFIG
    base: dict[str, object] = {
        "proposed_category": metrics.proposed_category,
        "proposed_subtypes": list(metrics.proposed_subtypes),
        "location_text": metrics.location_text,
        "analysis_radius_m": metrics.analysis_radius_m,
        "assessment_caveats": list(cfg.assessment_caveats),
        "config": cfg,
    }

    # --- rung 0: upstream status (read ONLY .status) --------------------
    gated = _upstream_gate(metrics, demand, analysis)
    if gated is not None:
        status, message = gated
        return MarketAssessmentResult(
            status=status,
            label=MarketAssessmentLabel.INSUFFICIENT_EVIDENCE,
            label_reason=message,
            label_basis={"rung": LadderRung.UPSTREAM_STATUS.value, "matrix_key": None},
            warnings=[message],
            **base,
        )

    comp_summary, dem_summary = _summaries(metrics, demand)

    # --- rung 1: input consistency ---------------------------------
    mismatch = _consistency_error(metrics, demand, analysis, cfg)
    if mismatch is not None:
        return MarketAssessmentResult(
            status=MarketAssessmentStatus.INCONSISTENT_INPUTS,
            competition_signal=metrics.signal,
            competition_summary=comp_summary,
            demand_summary=dem_summary,
            label=MarketAssessmentLabel.INSUFFICIENT_EVIDENCE,
            label_reason=mismatch,
            label_basis={"rung": LadderRung.INCONSISTENT_INPUTS.value, "matrix_key": None},
            warnings=[mismatch],
            **base,
        )

    # --- catchment scale + label + confidence ---------------------
    scale, scale_basis = _catchment_scale(demand, cfg)
    label, rung, matrix_key, label_reason, ladder_warnings = _decide_label(
        metrics, demand, scale, scale_basis, cfg
    )
    conf, conf_basis = _confidence(metrics, demand, scale_basis, cfg)

    warnings = [*ladder_warnings]
    if (
        demand.status is DemandStatus.OK
        and demand.catchment.persons is not None
        and demand.catchment.persons > 0
        and demand.competitors_per_1000_people is None
    ):
        warnings.append(
            "Phase 2C was not given the Phase 2B result, so competitors-per-1,000-residents "
            "is absent from its output; Phase 2D reports persons_per_direct_competitor instead."
        )

    # --- findings (assessment status is OK here) -----------------
    ctx = _FindingCtx(
        metrics=metrics,
        demand=demand,
        analysis=analysis,
        scale=scale,
        scale_basis=scale_basis,
        cfg=cfg,
    )
    findings: list[Finding] = [f for rule in FINDING_RULES if (f := rule(ctx)) is not None]
    positive = [f for f in findings if f.kind is FindingKind.POSITIVE]
    concerns = [f for f in findings if f.kind is FindingKind.CONCERN]
    caveats = [f for f in findings if f.kind is FindingKind.DATA_CAVEAT]

    logger.info(
        "market assessment (%s, r=%dm): scale=%s/%s competition=%s -> %s (rung %s, conf %.2f)",
        metrics.proposed_category.value,
        metrics.analysis_radius_m,
        scale.value,
        scale_basis.tier.value,
        metrics.signal.value,
        label.value,
        rung.value,
        conf,
    )

    return MarketAssessmentResult(
        status=MarketAssessmentStatus.OK,
        catchment_scale=scale,
        scale_basis=scale_basis,
        competition_signal=metrics.signal,
        competition_summary=comp_summary,
        demand_summary=dem_summary,
        label=label,
        label_reason=label_reason,
        label_basis={"rung": rung.value, "matrix_key": matrix_key},
        persons_per_direct_competitor=_persons_per_direct_competitor(metrics, demand),
        positive_signals=positive,
        concerns=concerns,
        data_caveats=caveats,
        assessment_data_confidence=conf,
        assessment_data_confidence_basis=conf_basis,
        warnings=warnings,
        **base,
    )
