"""The single configuration layer for Phase 2C demand signals (CLAUDE.md §11, §22).

Every tunable in the demand engine lives here — thresholds, confidence weights,
and the fixed interpretation caveats — mirroring ``metrics_config.py`` for
Phase 2B. All values are **MVP heuristics, not validated**.

``reference_year`` is the deliberate substitute for ``datetime.now().year``: the
engine must never read a wall clock (CLAUDE.md §28), so data staleness is a
*stated assumption* echoed into every result.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_DEFAULT_GUIDANCE: tuple[str, ...] = (
    "Catchment population is a count of residents on paper, not a measure of "
    "purchasing power, footfall, or willingness to buy.",
    "Activity anchors (schools, markets, banks, transport) indicate settled "
    "population and daily movement; they are proxies, not demand measurements.",
    "Where population coverage is below 1.0 the catchment figure is a lower "
    "bound; the true resident population is higher.",
    "Census 2011 predates any newer village-level enumeration; local population "
    "may have grown or shrunk since.",
)


class DemandConfig(BaseModel):
    """Tunable parameters for
    :func:`vyaparsarathi.market.demand.compute_demand_signals`. Frozen so the
    shared default cannot be mutated; echoed into the result for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # The "as of" year for freshness maths. [tunable] a stated assumption, NOT a
    # clock read.
    reference_year: int = 2026
    census_reference_year: int = 2011

    # Freshness decay for a stale but authoritative source. [tunable]
    # freshness = max(freshness_floor, 1 - decay_per_year * (reference_year - data_year))
    freshness_floor: float = Field(default=0.45, ge=0.0, le=1.0)
    freshness_decay_per_year: float = Field(default=0.03, ge=0.0, le=1.0)

    # Source tiers (CLAUDE.md §19). [tunable]
    census_tier: float = Field(default=0.85, ge=0.0, le=1.0)
    osm_single_source_ceiling: float = Field(default=0.75, ge=0.0, le=1.0)

    # A settlement whose centre is at or beyond this fraction of the radius is
    # flagged boundary-proximate for the sensitivity list. [tunable]
    boundary_fraction: float = Field(default=0.8, ge=0.0, le=1.0)

    # Weights for the overall demand_data_confidence (renormalised over the
    # signals that are actually available). [tunable]
    weight_population: float = Field(default=0.6, ge=0.0)
    weight_settlement: float = Field(default=0.2, ge=0.0)
    weight_activity: float = Field(default=0.2, ge=0.0)

    # Fixed, human-authored caveats copied verbatim into every result. These are
    # NOT generated interpretation (that is Phase 6 / the LLM layer, CLAUDE.md §5).
    interpretation_guidance: tuple[str, ...] = _DEFAULT_GUIDANCE


DEFAULT_DEMAND_CONFIG = DemandConfig()
