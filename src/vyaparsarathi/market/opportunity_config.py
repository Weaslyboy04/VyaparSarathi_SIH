"""The single configuration layer for Phase 3 opportunity scoring (CLAUDE.md §12).

Mirrors ``assessment_config.py`` / ``metrics_config.py`` / ``demand_config.py``:
a frozen Pydantic model of tunables, echoed verbatim into every result. Kept
import-pure (plain strings, no model enums) so ``opportunity_models.py`` can
import :class:`OpportunityConfig` without a cycle.

Everything here is an **MVP heuristic, not validated**; every value is
``# [tunable]``. The curated lookup tables (candidate universe, asset relevance,
capital bands) are hand-authored *qualitative* claims in the same class as
``relationships.py`` — being wrong yields a wrong *reason*, not a wrong number,
and each entry carries a one-line justification.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# --- candidate universe ------------------------------------------------------
#
# An MVP *recommendation universe* — the businesses Phase 3 will compare a rural
# proposal against — NOT an exhaustive list of businesses a person could start.
# The proposed category is always added on top of this list. Order is config
# order (deterministic; never sorted by data).
_CANDIDATE_CATEGORIES: tuple[str, ...] = (
    "grocery",  # the archetypal rural retail proposal
    "general_store",  # village mixed-goods shop; broad staple demand
    "dairy",  # milk / curd / ghee; daily-need, cold-chain sensitive
    "agri_input",  # seed / fertiliser / pesticide; seasonal, higher ticket
    "livestock_services",  # cattle feed / veterinary; strong where livestock is kept
    "food_processing",  # dal / flour / oil milling; adds margin to staples
    "pharmacy",  # regulated, higher capital, thin rural coverage
    "food_stall",  # tea / snacks; lowest capital, footfall-driven
)

# --- label -> market_opportunity points -----------------------------------
#
# The `market_opportunity` component reads the Phase 2D label ALONE (2D already
# fused competition + demand; re-deriving either would double-count). [tunable]
_LABEL_POINTS: dict[str, int] = {
    "underserved": 85,
    "mixed": 60,
    "served": 55,
    "insufficient_evidence": 40,  # "we cannot tell" — mid, and never a pivot (see engine)
    "crowded": 25,
    "thin_market": 20,
}

# --- label lattice for "strictly better" ---------------------------------
#
# `underserved > mixed > served > {crowded, thin_market}`. `crowded` and
# `thin_market` are deliberately EQUAL-RANK (incomparable — one is "too many for
# this many people", the other "too few people"). `insufficient_evidence` is the
# floor and can never be "strictly better" than anything. Used only for the pivot
# recommendation gate, never for the numeric score. [tunable]
_LABEL_LATTICE_RANK: dict[str, int] = {
    "underserved": 4,
    "mixed": 3,
    "served": 2,
    "crowded": 1,
    "thin_market": 1,
    "insufficient_evidence": 0,
}

# --- asset relevance ------------------------------------------------------
#
# category -> { asset_kind -> "essential" | "helpful" }. A *qualitative
# structural* claim ("a dairy needs cold storage"), authored conservatively.
# Absent asset kinds are neither. A category with no row here makes `asset_fit`
# `component_unavailable` for that candidate (flagged, not renormalised away).
_ASSET_RELEVANCE: dict[str, dict[str, str]] = {
    "grocery": {
        "storefront": "essential",  # fixed retail point is the business
        "warehouse": "helpful",  # dry buffer stock smooths staple supply
    },
    "general_store": {
        "storefront": "essential",
        "warehouse": "helpful",
    },
    "dairy": {
        "storefront": "essential",
        "cold_storage": "essential",  # milk / curd spoil without a chiller
        "vehicle": "helpful",  # collection / delivery rounds
    },
    "agri_input": {
        "storefront": "essential",
        "warehouse": "helpful",  # bagged fertiliser / seed needs dry bulk storage
        "vehicle": "helpful",  # farm-gate delivery
    },
    "livestock_services": {
        "storefront": "helpful",  # much trade is at the farm, not a counter
        "warehouse": "helpful",  # feed sacks in bulk
        "vehicle": "helpful",  # feed delivery / mobile vet visits
        "livestock": "helpful",  # own herd signals credibility and a base demand
    },
    "food_processing": {
        "equipment": "essential",  # the mill / expeller IS the business
        "warehouse": "essential",  # grain in, processed stock out
        "storefront": "helpful",  # a counter for walk-in custom milling
    },
    "pharmacy": {
        "storefront": "essential",  # licensed fixed premises are mandatory in practice
    },
    "food_stall": {
        "equipment": "helpful",  # stove / griddle / cart
        "storefront": "helpful",  # a shaded pukka spot; many stalls run without one
    },
}

# --- capital bands ------------------------------------------------------
#
# category -> (indicative_minimum_inr, typical_inr). [assumption] — an ORDINAL
# screening estimate of typical capital intensity, NOT a project cost. It may
# flag or demote a candidate; it must never imply the business is impossible,
# ineligible or unaffordable. Phase 4 financial analysis replaces this screen
# with real project cost + scheme rules (§15, §18).
_CAPITAL_BANDS: dict[str, tuple[int, int]] = {
    "grocery": (150_000, 400_000),
    "general_store": (120_000, 350_000),
    "dairy": (200_000, 500_000),
    "agri_input": (300_000, 800_000),
    "livestock_services": (250_000, 700_000),
    "food_processing": (400_000, 1_200_000),
    "pharmacy": (500_000, 1_200_000),
    "food_stall": (50_000, 200_000),
}

# --- experience match -> points --------------------------------------
#
# Reuses Phase 2A's `relationship_for(candidate, experience_category)`; no new
# table. [tunable]
_EXPERIENCE_MATCH_POINTS: dict[str, int] = {
    "same": 100,
    "direct": 80,
    "adjacent": 60,
    "unrelated": 40,
}

# --- fixed caveats (copied verbatim into every result) --------------
#
# Human-authored; NOT generated interpretation (that is Phase 6). [decision]
_CAVEATS: tuple[str, ...] = (
    "The candidate list is an MVP recommendation universe for rural micro-retail, "
    "not an exhaustive list of businesses that could work here. A business you "
    "named is always scored even if it is outside this list.",
    "Assets you already own can reduce what a project needs to spend. They do NOT "
    "count as promoter margin — margin is set by scheme rules and is decided at a "
    "later (financing) stage.",
    "The capital check is an indicative screen based on typical capital intensity, "
    "not a financial assessment. It does not mean a business is unaffordable, "
    "ineligible or impossible — detailed capital needs and financing come from the "
    "financial analysis stage.",
    "This analysis ranks options against each other on partial local evidence. It "
    "does not predict whether any business will succeed, and it computes no "
    "revenue, loan, EMI or repayment figure.",
    "A higher score alone is never a recommendation: an alternative is only called "
    "materially better when its market evidence is stronger AND sufficient AND its "
    "capital fit is resolved.",
)


class OpportunityConfig(BaseModel):
    """Tunable parameters for
    :func:`vyaparsarathi.market.opportunity.score_opportunities`. Frozen; echoed
    into every result for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- component weights (nominal; renormalised over available components) --- [tunable]
    weight_market: float = Field(default=0.60, ge=0.0)
    weight_asset: float = Field(default=0.25, ge=0.0)
    weight_experience: float = Field(default=0.15, ge=0.0)

    # --- asset_fit sub-weights --- [tunable]
    asset_essential_weight: float = Field(default=0.70, ge=0.0, le=1.0)
    asset_helpful_weight: float = Field(default=0.30, ge=0.0, le=1.0)

    # --- pivot recommendation gate --- [tunable]
    # Minimum score gap before a strictly-better-labelled alternative is called
    # "materially better". Belt-and-braces with the label-lattice check.
    material_margin: int = Field(default=8, ge=0)

    # --- data ---
    candidate_categories: tuple[str, ...] = _CANDIDATE_CATEGORIES
    label_points: dict[str, int] = Field(default_factory=lambda: dict(_LABEL_POINTS))
    label_lattice_rank: dict[str, int] = Field(default_factory=lambda: dict(_LABEL_LATTICE_RANK))
    asset_relevance: dict[str, dict[str, str]] = Field(
        default_factory=lambda: {k: dict(v) for k, v in _ASSET_RELEVANCE.items()}
    )
    capital_bands: dict[str, tuple[int, int]] = Field(default_factory=lambda: dict(_CAPITAL_BANDS))
    experience_match_points: dict[str, int] = Field(
        default_factory=lambda: dict(_EXPERIENCE_MATCH_POINTS)
    )
    caveats: tuple[str, ...] = _CAVEATS


DEFAULT_OPPORTUNITY_CONFIG = OpportunityConfig()
