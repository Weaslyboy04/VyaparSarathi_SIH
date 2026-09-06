"""Phase 2D finding rules — one pure predicate per rule.

Each rule is ``(_FindingCtx) -> Finding | None``. The engine
(:mod:`vyaparsarathi.market.assessment`) runs :data:`FINDING_RULES` in order
**only when the assessment status is OK** (so every rule may trust
``metrics.status is CompetitionMetricsStatus.OK`` and a non-degraded
``demand``). Messages are rendered from templates in
:class:`AssessmentConfig.finding_messages`; no natural language is authored here.
"""

from __future__ import annotations

from dataclasses import dataclass

from vyaparsarathi.market.assessment_config import AssessmentConfig
from vyaparsarathi.market.assessment_models import (
    CatchmentScale,
    EvidenceRef,
    Finding,
    FindingKind,
    ScaleBasis,
    ScaleTier,
)
from vyaparsarathi.market.demand_models import DemandSignalsResult
from vyaparsarathi.market.metrics_models import CompetitionMetricsResult, CompetitionSignal
from vyaparsarathi.market.models import CompetitorAnalysisResult
from vyaparsarathi.models.demand import ActivityKind


@dataclass(frozen=True)
class _FindingCtx:
    metrics: CompetitionMetricsResult
    demand: DemandSignalsResult
    analysis: CompetitorAnalysisResult | None
    scale: CatchmentScale
    scale_basis: ScaleBasis
    cfg: AssessmentConfig


def _finding(
    ctx: _FindingCtx,
    code: str,
    kind: FindingKind,
    values: dict[str, object],
    evidence: list[EvidenceRef],
) -> Finding:
    template = ctx.cfg.finding_messages[code]  # KeyError here is a config bug — tests catch it
    return Finding(code=code, kind=kind, message=template.format(**values), evidence=evidence)


def _radius_km(ctx: _FindingCtx) -> float:
    return ctx.metrics.analysis_radius_m / 1000.0


# --- positive ---------------------------------------------------------


def _no_direct_competitors_confident(ctx: _FindingCtx) -> Finding | None:
    m = ctx.metrics
    if m.direct_count != 0 or m.data_confidence < ctx.cfg.min_confidence_for_absence_claim:
        return None
    return _finding(
        ctx,
        "no_direct_competitors_confident",
        FindingKind.POSITIVE,
        {"radius_km": _radius_km(ctx), "data_confidence": m.data_confidence},
        [
            EvidenceRef(source="competition", field="direct_count", value=0),
            EvidenceRef(
                source="competition",
                field="data_confidence",
                value=m.data_confidence,
                compared_to=ctx.cfg.min_confidence_for_absence_claim,
            ),
        ],
    )


def _nearest_competitor_distant(ctx: _FindingCtx) -> Finding | None:
    d = ctx.metrics.direct_distance
    if d.count == 0 or d.nearest_m is None:
        return None
    if d.nearest_m / 1000.0 <= ctx.cfg.near_competitor_km:
        return None
    return _finding(
        ctx,
        "nearest_competitor_distant",
        FindingKind.POSITIVE,
        {"nearest_km": d.nearest_m / 1000.0, "near_km": ctx.cfg.near_competitor_km},
        [
            EvidenceRef(
                source="competition",
                field="direct_distance.nearest_m",
                value=d.nearest_m,
                compared_to=ctx.cfg.near_competitor_km * 1000.0,
            )
        ],
    )


def _large_catchment_population(ctx: _FindingCtx) -> Finding | None:
    if ctx.scale is not CatchmentScale.LARGE or ctx.scale_basis.tier is not ScaleTier.POPULATION:
        return None
    persons = ctx.demand.catchment.persons
    if persons is None:
        return None
    return _finding(
        ctx,
        "large_catchment_population",
        FindingKind.POSITIVE,
        {"persons": persons, "threshold": ctx.cfg.population_large},
        [
            EvidenceRef(
                source="demand",
                field="catchment.persons",
                value=persons,
                compared_to=float(ctx.cfg.population_large),
            )
        ],
    )


def _multiple_settlements_in_catchment(ctx: _FindingCtx) -> Finding | None:
    n = ctx.demand.catchment.settlements_found
    if n < ctx.cfg.proxy_settlements_small:
        return None
    return _finding(
        ctx,
        "multiple_settlements_in_catchment",
        FindingKind.POSITIVE,
        {"settlements_found": n},
        [EvidenceRef(source="demand", field="catchment.settlements_found", value=n)],
    )


def _anchor_present(ctx: _FindingCtx, kind: ActivityKind, code: str) -> Finding | None:
    count = ctx.demand.activity.counts_by_kind.get(kind, 0)
    if count <= 0:
        return None
    return _finding(
        ctx,
        code,
        FindingKind.POSITIVE,
        {"count": count},
        [EvidenceRef(source="demand", field=f"activity.counts_by_kind.{kind.value}", value=count)],
    )


def _marketplace_anchor_present(ctx: _FindingCtx) -> Finding | None:
    return _anchor_present(ctx, ActivityKind.MARKETPLACE, "marketplace_anchor_present")


def _transport_access_present(ctx: _FindingCtx) -> Finding | None:
    return _anchor_present(ctx, ActivityKind.TRANSPORT_STOP, "transport_access_present")


# --- concerns ------------------------------------------------------


def _small_catchment(ctx: _FindingCtx) -> Finding | None:
    if ctx.scale is not CatchmentScale.SMALL:
        return None
    persons = ctx.demand.catchment.persons
    ev = (
        EvidenceRef(source="demand", field="catchment.persons", value=persons)
        if persons is not None
        else EvidenceRef(
            source="demand",
            field="catchment.settlements_found",
            value=ctx.demand.catchment.settlements_found,
        )
    )
    return _finding(ctx, "small_catchment", FindingKind.CONCERN, {"scale": ctx.scale.value}, [ev])


def _high_competition(ctx: _FindingCtx) -> Finding | None:
    m = ctx.metrics
    if m.signal not in {CompetitionSignal.MODERATE, CompetitionSignal.HIGH}:
        return None
    return _finding(
        ctx,
        "high_competition",
        FindingKind.CONCERN,
        {"signal": m.signal.value, "direct_count": m.direct_count, "radius_km": _radius_km(ctx)},
        [
            EvidenceRef(source="competition", field="signal", value=m.signal.value),
            EvidenceRef(source="competition", field="direct_count", value=m.direct_count),
        ],
    )


def _competitor_very_close(ctx: _FindingCtx) -> Finding | None:
    d = ctx.metrics.direct_distance
    if d.count == 0 or d.nearest_m is None:
        return None
    if d.nearest_m / 1000.0 > ctx.cfg.near_competitor_km:
        return None
    return _finding(
        ctx,
        "competitor_very_close",
        FindingKind.CONCERN,
        {"nearest_km": d.nearest_m / 1000.0, "near_km": ctx.cfg.near_competitor_km},
        [
            EvidenceRef(
                source="competition",
                field="direct_distance.nearest_m",
                value=d.nearest_m,
                compared_to=ctx.cfg.near_competitor_km * 1000.0,
            )
        ],
    )


def _adjacent_substitutes_present(ctx: _FindingCtx) -> Finding | None:
    m = ctx.metrics
    if m.adjacent_count < 1:
        return None
    dominates = m.direct_count == 0 or m.adjacent_count >= ctx.cfg.adjacent_dominance_ratio * max(
        m.direct_count, 1
    )
    if not dominates:
        return None
    return _finding(
        ctx,
        "adjacent_substitutes_present",
        FindingKind.CONCERN,
        {"adjacent_count": m.adjacent_count, "direct_count": m.direct_count},
        [
            EvidenceRef(source="competition", field="adjacent_count", value=m.adjacent_count),
            EvidenceRef(source="competition", field="direct_count", value=m.direct_count),
        ],
    )


# --- data caveats -------------------------------------------------


def _population_not_geolocated(ctx: _FindingCtx) -> Finding | None:
    cov = ctx.demand.coverage
    if not cov.population_available_not_geolocated:
        return None
    return _finding(
        ctx,
        "population_not_geolocated",
        FindingKind.DATA_CAVEAT,
        {},
        [
            EvidenceRef(
                source="demand",
                field="coverage.population_available_not_geolocated",
                value=True,
            ),
            EvidenceRef(
                source="demand",
                field="coverage.population_records_ungeolocated_in_area",
                value=cov.population_records_ungeolocated_in_area,
            ),
        ],
    )


def _population_is_floor(ctx: _FindingCtx) -> Finding | None:
    c = ctx.demand.catchment
    if c.is_floor is not True or c.persons is None:
        return None
    return _finding(
        ctx,
        "population_is_floor",
        FindingKind.DATA_CAVEAT,
        {
            "persons": c.persons,
            "with_pop": c.settlements_with_population,
            "found": c.settlements_found,
        },
        [
            EvidenceRef(source="demand", field="catchment.is_floor", value=True),
            EvidenceRef(
                source="demand",
                field="catchment.population_coverage",
                value=c.population_coverage,
            ),
        ],
    )


def _population_unknown(ctx: _FindingCtx) -> Finding | None:
    if ctx.demand.catchment.persons is not None:
        return None
    return _finding(
        ctx,
        "population_unknown",
        FindingKind.DATA_CAVEAT,
        {},
        [EvidenceRef(source="demand", field="catchment.persons", value=None)],
    )


def _stale_population_data(ctx: _FindingCtx) -> Finding | None:
    c = ctx.demand.catchment
    if c.persons is None:
        return None
    age = ctx.cfg.reference_year - ctx.cfg.census_reference_year
    return _finding(
        ctx,
        "stale_population_data",
        FindingKind.DATA_CAVEAT,
        {"age": age},
        # The stale datum is the population figure itself; the year is in the
        # message. `reference_years` is a list, so it is not an EvidenceRef value.
        [EvidenceRef(source="demand", field="catchment.persons", value=c.persons)],
    )


def _low_market_data_confidence(ctx: _FindingCtx) -> Finding | None:
    lo = min(ctx.metrics.data_confidence, ctx.demand.demand_data_confidence)
    if lo >= ctx.cfg.low_confidence_threshold:
        return None
    return _finding(
        ctx,
        "low_market_data_confidence",
        FindingKind.DATA_CAVEAT,
        {"confidence": lo},
        [
            EvidenceRef(
                source="competition", field="data_confidence", value=ctx.metrics.data_confidence
            ),
            EvidenceRef(
                source="demand",
                field="demand_data_confidence",
                value=ctx.demand.demand_data_confidence,
            ),
        ],
    )


def _competitor_absence_low_coverage(ctx: _FindingCtx) -> Finding | None:
    m = ctx.metrics
    if m.direct_count != 0 or m.data_confidence >= ctx.cfg.min_confidence_for_absence_claim:
        return None
    return _finding(
        ctx,
        "competitor_absence_low_coverage",
        FindingKind.DATA_CAVEAT,
        {
            "confidence": m.data_confidence,
            "threshold": ctx.cfg.min_confidence_for_absence_claim,
        },
        [
            EvidenceRef(source="competition", field="direct_count", value=0),
            EvidenceRef(
                source="competition",
                field="data_confidence",
                value=m.data_confidence,
                compared_to=ctx.cfg.min_confidence_for_absence_claim,
            ),
        ],
    )


def _distances_unavailable(ctx: _FindingCtx) -> Finding | None:
    d = ctx.metrics.direct_distance
    if ctx.metrics.direct_count == 0 or d.count > 0:
        return None
    return _finding(
        ctx,
        "distances_unavailable",
        FindingKind.DATA_CAVEAT,
        {"missing": d.missing_distance},
        [
            EvidenceRef(
                source="competition",
                field="direct_distance.missing_distance",
                value=d.missing_distance,
            )
        ],
    )


# Ordered so the rendered output is deterministic.
FINDING_RULES = (
    _no_direct_competitors_confident,
    _nearest_competitor_distant,
    _large_catchment_population,
    _multiple_settlements_in_catchment,
    _marketplace_anchor_present,
    _transport_access_present,
    _small_catchment,
    _high_competition,
    _competitor_very_close,
    _adjacent_substitutes_present,
    _population_not_geolocated,
    _population_is_floor,
    _population_unknown,
    _stale_population_data,
    _low_market_data_confidence,
    _competitor_absence_low_coverage,
    _distances_unavailable,
)
