"""The single configuration layer for Phase 2B competition metrics (CLAUDE.md §11).

Everything tunable about *how competition around the proposed location is
measured* lives here and nowhere else, exactly as ``relationships.py`` is the
single config layer for Phase 2A classification. The metrics engine
(``metrics.py``) imports these values; it hard-codes none of its own.

All thresholds are **MVP heuristics, not empirically validated**. They exist to
turn raw competitor classifications into transparent, comparable numbers for
Phase 2D — they are not a "market saturation score" (CLAUDE.md §11, STEP 6).
Environment-driven deployment knobs still belong in ``config/settings.py``;
these are analysis parameters, like the relationship tables.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CompetitionMetricsConfig(BaseModel):
    """Tunable parameters for :func:`vyaparsarathi.market.metrics.compute_competition_metrics`.

    Frozen so a shared default instance cannot be mutated by accident. Pass a
    custom instance to the engine to override; it is echoed into the result so
    every number stays traceable to the config that produced it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Cumulative "competitors within X metres" bands. Ascending, metres.
    # [tunable] MVP analysis bands, not empirically optimised. A band wider than
    # the analysis radius is still reported but flagged ``exceeds_radius``.
    distance_bands_m: tuple[int, ...] = (500, 1_000, 2_000, 5_000)

    # Competition-signal thresholds. [tunable] MVP heuristic — NOT a validated
    # saturation score. The reported ``signal`` is the stronger of a count-based
    # level and a density-based level (see metrics.py::_signal_for).
    signal_count_moderate: int = Field(default=3, ge=1)  # >= this many direct -> MODERATE
    signal_count_high: int = Field(default=6, ge=1)  # >= this many direct -> HIGH
    signal_density_moderate_per_km2: float = Field(default=0.05, ge=0.0)
    signal_density_high_per_km2: float = Field(default=0.15, ge=0.0)


# Process-wide default. Import this; do not re-instantiate with literals elsewhere.
DEFAULT_METRICS_CONFIG = CompetitionMetricsConfig()
