"""Deterministic competition-metrics engine (CLAUDE.md §11, STEP 2-8).

Consumes a Phase 2A :class:`CompetitorAnalysisResult` (the classifications) plus
the originating Phase 1 :class:`DiscoveryResult` (for the analysis radius, the
query point, and the data-coverage confidence) and produces a
:class:`CompetitionMetricsResult`.

No LLM, no network, no database, no wall-clock, no RNG. Distances are **not**
recomputed — the query-relative ``distance_m`` carried on each classified
competitor from the Phase 1 ``BusinessHit`` is used as-is (STEP 4). Competitors
are never re-classified here (STEP 3).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence

from vyaparsarathi.market.classifier import analyze_from_discovery
from vyaparsarathi.market.metrics_config import DEFAULT_METRICS_CONFIG, CompetitionMetricsConfig
from vyaparsarathi.market.metrics_models import (
    CompetitionDensity,
    CompetitionMetricsResult,
    CompetitionMetricsStatus,
    CompetitionSignal,
    DistanceBand,
    DistanceStats,
)
from vyaparsarathi.market.models import (
    ClassifiedCompetitor,
    CompetitorAnalysisResult,
    CompetitorAnalysisStatus,
)
from vyaparsarathi.models.results import DiscoveryResult
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_SIGNAL_RANK: dict[CompetitionSignal, int] = {
    CompetitionSignal.NONE: 0,
    CompetitionSignal.LOW: 1,
    CompetitionSignal.MODERATE: 2,
    CompetitionSignal.HIGH: 3,
}


def _radius_and_point(discovery: DiscoveryResult) -> tuple[int, float | None, float | None]:
    """Analysis radius (metres) and query point, preferring the resolved query."""
    if discovery.query is not None:
        q = discovery.query
        return q.radius_m, q.latitude, q.longitude
    if discovery.resolved_place is not None:
        rp = discovery.resolved_place
        return discovery.requested_radius_m, rp.latitude, rp.longitude
    return discovery.requested_radius_m, None, None


def _within_radius(
    competitors: Sequence[ClassifiedCompetitor], radius_m: int
) -> tuple[list[ClassifiedCompetitor], int]:
    """Drop competitors whose known distance is beyond the analysis radius.

    Phase 1 already filters to the radius; this is a defensive guard for
    hand-built inputs (STEP 5). Competitors with an unknown distance are kept —
    they were still identified as competitors.
    """
    kept: list[ClassifiedCompetitor] = []
    dropped = 0
    for c in competitors:
        if c.distance_m is not None and c.distance_m > radius_m:
            dropped += 1
        else:
            kept.append(c)
    return kept, dropped


def _distances(competitors: Iterable[ClassifiedCompetitor]) -> list[float]:
    return sorted(c.distance_m for c in competitors if c.distance_m is not None)


def _distance_stats(competitors: Sequence[ClassifiedCompetitor]) -> DistanceStats:
    xs = _distances(competitors)
    missing = sum(1 for c in competitors if c.distance_m is None)
    if not xs:
        return DistanceStats(count=0, missing_distance=missing)
    return DistanceStats(
        count=len(xs),
        missing_distance=missing,
        nearest_m=xs[0],
        farthest_m=xs[-1],
        mean_m=sum(xs) / len(xs),
        median_m=statistics.median(xs),
    )


def _distance_bands(
    direct: Sequence[ClassifiedCompetitor],
    relevant: Sequence[ClassifiedCompetitor],
    bands_m: Iterable[int],
    radius_m: int,
) -> list[DistanceBand]:
    direct_d = _distances(direct)
    relevant_d = _distances(relevant)
    bands: list[DistanceBand] = []
    for edge in sorted({int(b) for b in bands_m}):
        bands.append(
            DistanceBand(
                max_distance_m=edge,
                direct_count=sum(1 for d in direct_d if d <= edge),
                relevant_count=sum(1 for d in relevant_d if d <= edge),
                exceeds_radius=edge > radius_m,
            )
        )
    return bands


def _density(count: int, area_km2: float | None) -> float | None:
    if area_km2 is None or area_km2 <= 0.0:
        return None
    return count / area_km2


def _count_level(n: int, cfg: CompetitionMetricsConfig) -> CompetitionSignal:
    if n <= 0:
        return CompetitionSignal.NONE
    if n >= cfg.signal_count_high:
        return CompetitionSignal.HIGH
    if n >= cfg.signal_count_moderate:
        return CompetitionSignal.MODERATE
    return CompetitionSignal.LOW


def _density_level(d: float | None, cfg: CompetitionMetricsConfig) -> CompetitionSignal:
    if d is None or d <= 0.0:
        return CompetitionSignal.NONE
    if d >= cfg.signal_density_high_per_km2:
        return CompetitionSignal.HIGH
    if d >= cfg.signal_density_moderate_per_km2:
        return CompetitionSignal.MODERATE
    return CompetitionSignal.LOW


def _signal_for(
    direct_count: int, direct_density: float | None, cfg: CompetitionMetricsConfig
) -> tuple[CompetitionSignal, str, dict[str, float | None]]:
    """The stronger of the count-based and density-based levels, plus a reason.

    MVP heuristic (STEP 6) — a transparent label over two measured inputs, not a
    validated saturation score.
    """
    by_count = _count_level(direct_count, cfg)
    by_density = _density_level(direct_density, cfg)
    signal = max(by_count, by_density, key=lambda s: _SIGNAL_RANK[s])
    density_txt = "n/a" if direct_density is None else f"{direct_density:.3f}/km2"
    reason = (
        f"{direct_count} direct competitor(s) within the analysis radius "
        f"(count level: {by_count.value}); direct density {density_txt} "
        f"(density level: {by_density.value}); reported signal is the stronger of "
        f"the two: {signal.value}."
    )
    basis: dict[str, float | None] = {
        "direct_count": float(direct_count),
        "direct_density_per_km2": direct_density,
        "count_moderate_threshold": float(cfg.signal_count_moderate),
        "count_high_threshold": float(cfg.signal_count_high),
        "density_moderate_threshold_per_km2": cfg.signal_density_moderate_per_km2,
        "density_high_threshold_per_km2": cfg.signal_density_high_per_km2,
    }
    return signal, reason, basis


def compute_competition_metrics(
    analysis: CompetitorAnalysisResult,
    discovery: DiscoveryResult,
    *,
    config: CompetitionMetricsConfig | None = None,
) -> CompetitionMetricsResult:
    """Compute Phase 2B competition metrics for ``analysis`` at ``discovery``'s location."""
    cfg = config or DEFAULT_METRICS_CONFIG
    radius_m, q_lat, q_lon = _radius_and_point(discovery)
    proposed = analysis.proposed
    location_text = analysis.location_text or discovery.query_text

    base: dict[str, object] = {
        "location_text": location_text,
        "proposed_category": proposed.category,
        "proposed_subtypes": list(proposed.subtypes),
        "analysis_radius_m": radius_m,
        "query_latitude": q_lat,
        "query_longitude": q_lon,
        "data_confidence": discovery.confidence,
        "config": cfg,
    }

    warnings: list[str] = []
    if analysis.location_text and analysis.location_text != discovery.query_text:
        warnings.append(
            "Phase 2A and Phase 1 refer to different locations "
            f"({analysis.location_text!r} vs {discovery.query_text!r}); "
            "metrics use the Phase 1 point."
        )
    # Carry forward the Phase 1 coverage caveat so a thin-data run stays visible.
    warnings.append(
        "Counts reflect businesses identified by the configured sources, not a "
        "guaranteed-exhaustive market inventory; absence from the data is not "
        "proof of absence on the ground."
    )

    if analysis.status is CompetitorAnalysisStatus.UNKNOWN_CATEGORY:
        return CompetitionMetricsResult(
            status=CompetitionMetricsStatus.UNKNOWN_CATEGORY,
            warnings=[*analysis.warnings, *warnings],
            **base,
        )

    if radius_m <= 0:
        bad_radius = f"Analysis radius is {radius_m} m; density and bands are undefined."
        return CompetitionMetricsResult(
            status=CompetitionMetricsStatus.INVALID_RADIUS,
            warnings=[bad_radius, *warnings],
            **base,
        )

    direct, dropped_direct = _within_radius(analysis.direct_competitors, radius_m)
    adjacent, dropped_adjacent = _within_radius(analysis.adjacent_competitors, radius_m)
    relevant = [*direct, *adjacent]
    if dropped_direct or dropped_adjacent:
        warnings.append(
            f"Excluded {dropped_direct + dropped_adjacent} competitor(s) beyond the "
            f"{radius_m} m analysis radius."
        )

    area_km2 = math.pi * (radius_m / 1000.0) ** 2
    direct_density = _density(len(direct), area_km2)
    signal, signal_reason, signal_basis = _signal_for(len(direct), direct_density, cfg)

    logger.info(
        "competition metrics (%s, r=%dm): %d direct, %d adjacent, signal=%s",
        proposed.category.value,
        radius_m,
        len(direct),
        len(adjacent),
        signal.value,
    )

    return CompetitionMetricsResult(
        status=CompetitionMetricsStatus.OK,
        direct_count=len(direct),
        adjacent_count=len(adjacent),
        total_relevant_count=len(relevant),
        direct_distance=_distance_stats(direct),
        relevant_distance=_distance_stats(relevant),
        distance_bands=_distance_bands(direct, relevant, cfg.distance_bands_m, radius_m),
        density=CompetitionDensity(
            catchment_area_km2=area_km2,
            direct_per_km2=direct_density,
            relevant_per_km2=_density(len(relevant), area_km2),
        ),
        signal=signal,
        signal_reason=signal_reason,
        signal_basis=signal_basis,
        warnings=warnings,
        **base,
    )


def metrics_from_discovery(
    discovery: DiscoveryResult,
    subtypes: Iterable[str] = (),
    *,
    config: CompetitionMetricsConfig | None = None,
) -> CompetitionMetricsResult:
    """Convenience: run Phase 2A on ``discovery`` then compute Phase 2B metrics."""
    analysis = analyze_from_discovery(discovery, subtypes)
    return compute_competition_metrics(analysis, discovery, config=config)
