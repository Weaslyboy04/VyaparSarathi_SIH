"""The single configuration layer for Phase 2D overall market assessment.

Mirrors ``metrics_config.py`` / ``demand_config.py``: a frozen Pydantic model of
tunables, echoed verbatim into every result. Kept import-pure (no model enums)
so ``assessment_models.py`` can import :class:`AssessmentConfig` without a cycle
— the same one-way arrangement 2B uses.

The label matrix and the finding message templates are **data** and live here,
not in the engine, following the ``relationships.py`` precedent. All values are
**MVP heuristics, not validated**; every one is ``# [tunable]``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# --- label matrix -------------------------------------------------------
#
# LABEL_MATRIX[catchment_scale][competition_signal] -> market label.
# Keys are the StrEnum *values* (plain strings) so this module imports no model.
# Rows: small / moderate / large.  Columns: none / low / moderate / high.
# `none` competition that survives the precedence ladder (rung "absence_not_
# evidence") is treated as the `low` column, plus a mandatory coverage caveat.
#
#   scale \ competition | none        | low         | moderate | high
#   --------------------|-------------|-------------|----------|--------
#   small               | thin_market | thin_market | crowded  | crowded
#   moderate            | underserved | mixed       | mixed    | crowded
#   large               | underserved | underserved | served   | served
#
_DEFAULT_LABEL_MATRIX: dict[str, dict[str, str]] = {
    "small": {
        "none": "thin_market",
        "low": "thin_market",
        "moderate": "crowded",
        "high": "crowded",
    },
    "moderate": {
        "none": "underserved",
        "low": "mixed",
        "moderate": "mixed",
        "high": "crowded",
    },
    "large": {
        "none": "underserved",
        "low": "underserved",
        "moderate": "served",
        "high": "served",
    },
}

# --- finding message templates ---------------------------------------
#
# code -> str.format(**evidence_values) template. Human-authored strings only;
# no runtime natural-language generation (that is Phase 6 / §3.3).
_DEFAULT_FINDING_MESSAGES: dict[str, str] = {
    # positive
    "no_direct_competitors_confident": (
        "No direct competitors were found within {radius_km:.0f} km, and market "
        "data coverage here ({data_confidence:.2f}) is good enough to take that "
        "at face value."
    ),
    "nearest_competitor_distant": (
        "The nearest direct competitor is {nearest_km:.1f} km away — beyond the "
        "{near_km:.1f} km 'very close' threshold."
    ),
    "large_catchment_population": (
        "The catchment holds about {persons:,} residents (Census 2011), above "
        "the {threshold:,} 'large catchment' mark."
    ),
    "multiple_settlements_in_catchment": (
        "{settlements_found} settlements fall inside the catchment — this is a "
        "cluster, not an isolated village."
    ),
    "marketplace_anchor_present": (
        "A marketplace / mandi anchor is present within the catchment "
        "({count} found), indicating recurring trade footfall."
    ),
    "transport_access_present": (
        "A bus or rail stop is present within the catchment ({count} found), "
        "widening the practical catchment beyond residents alone."
    ),
    # concerns
    "small_catchment": (
        "The catchment is small ({scale}); resident demand may not sustain "
        "additional supply of this kind."
    ),
    "high_competition": (
        "Competition is {signal} — {direct_count} direct competitor(s) within {radius_km:.0f} km."
    ),
    "competitor_very_close": (
        "A direct competitor sits {nearest_km:.2f} km away, within the "
        "{near_km:.1f} km 'very close' threshold."
    ),
    "adjacent_substitutes_present": (
        "{adjacent_count} adjacent substitute business(es) were found vs "
        "{direct_count} direct — the direct-only competition signal understates "
        "the real overlap."
    ),
    # data caveats
    "population_not_geolocated": (
        "Census 2011 population exists for this area but no village could be "
        "placed on the map (village-boundary coordinates in this build cover "
        "Bihar only). Catchment scale was derived from activity proxies, not a "
        "population count."
    ),
    "population_is_floor": (
        "The catchment population ({persons:,}) is a lower bound "
        "({with_pop} of {found} settlements matched a Census record); the true "
        "figure is higher, so the scale — and any 'thin market' reading — may "
        "be conservative."
    ),
    "population_unknown": (
        "No Census 2011 population figure is available for this catchment; the "
        "scale, where given, rests on settlement and activity counts only."
    ),
    "stale_population_data": (
        "Population figures are from Census 2011 ({age} years old); local "
        "population may have grown or shrunk since."
    ),
    "low_market_data_confidence": (
        "Observation quality is low ({confidence:.2f}). The market label is "
        "the best reading of thin evidence, not a firm conclusion."
    ),
    "competitor_absence_low_coverage": (
        "Zero competitors were found, but market-data coverage ({confidence:.2f}) "
        "is below {threshold:.2f} — absence from the data is not proof of an "
        "empty market."
    ),
    "distances_unavailable": (
        "{missing} direct competitor(s) have no usable distance; distance-based "
        "signals were skipped."
    ),
}

_DEFAULT_CAVEATS: tuple[str, ...] = (
    "This is a market-state reading, not a recommendation. It does not say "
    "whether to open the business, and it does not compare alternatives.",
    "Catchment scale is a count of residents / settlements, not a measure of "
    "purchasing power, footfall, or willingness to buy.",
    "Confidence here measures how well the local market is observed, not "
    "whether the business will succeed. Low confidence never worsens the label.",
    "Competition reflects only businesses the configured sources listed; "
    "absence from the data is not proof of absence on the ground.",
)


class AssessmentConfig(BaseModel):
    """Tunable parameters for
    :func:`vyaparsarathi.market.assessment.assess_market`. Frozen; echoed into
    every result for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- catchment-scale thresholds, population tier (persons) --- [tunable]
    population_large: int = Field(default=20_000, ge=1)
    population_moderate: int = Field(default=5_000, ge=1)

    # Below this Census coverage the population figure does not describe the
    # catchment; fall back to the activity proxy tier. [tunable]
    min_population_coverage_for_scale: float = Field(default=0.34, ge=0.0, le=1.0)

    # --- catchment-scale thresholds, activity-proxy tier --- [tunable]
    proxy_settlements_moderate: int = Field(default=15, ge=1)
    proxy_settlements_small: int = Field(default=3, ge=1)
    proxy_kinds_moderate: int = Field(default=3, ge=1)
    # The proxy tier can never exceed this scale. [tunable] — structural, not a
    # side effect of the numbers above.
    proxy_max_scale: str = "moderate"

    # --- precedence ladder --- [tunable]
    # A `none` competition signal is only believed when 2B data confidence is at
    # least this; otherwise the assessment refuses (rung "absence_not_evidence").
    min_confidence_for_absence_claim: float = Field(default=0.5, ge=0.0, le=1.0)
    # Two inputs are "the same catchment" only if their query points agree to
    # within this many metres (0.0 = exact). [tunable]
    max_point_divergence_m: float = Field(default=0.0, ge=0.0)

    # --- confidence --- [tunable]
    proxy_tier_penalty: float = Field(default=0.7, ge=0.0, le=1.0)

    # --- finding thresholds --- [tunable]
    near_competitor_km: float = Field(default=1.0, ge=0.0)
    low_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    adjacent_dominance_ratio: float = Field(default=1.5, ge=1.0)
    census_reference_year: int = 2011
    reference_year: int = 2026  # [tunable] stated assumption, not a clock read

    # --- data ---
    label_matrix: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {k: dict(v) for k, v in _DEFAULT_LABEL_MATRIX.items()}
    )
    finding_messages: dict[str, str] = Field(
        default_factory=lambda: dict(_DEFAULT_FINDING_MESSAGES)
    )
    assessment_caveats: tuple[str, ...] = _DEFAULT_CAVEATS


DEFAULT_ASSESSMENT_CONFIG = AssessmentConfig()
