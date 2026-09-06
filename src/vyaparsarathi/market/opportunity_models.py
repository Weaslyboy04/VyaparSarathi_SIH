"""Phase 3 opportunity / pivot-engine models (CLAUDE.md §12).

Phase 3 is the first phase that (a) evaluates more than one business, (b) takes a
person as input, and (c) emits a number. All three are sanctioned by §12 (a 0-100
comparison across candidate businesses, each score decomposing into named factor
contributions).

Design points carried in the type shapes:

* The number is a **display projection of an ordinal comparison**. Ranking and
  the pivot recommendation are gated on the Phase 2D *label* and on capability /
  capital flags, never on the scalar alone (§12; the three approved
  clarifications).
* ``market_opportunity`` is the only evidence-backed component and it reads the
  Phase 2D label **alone** — 2D already fused competition + demand, so
  re-deriving either would double-count.
* Confidence is reported once, unmultiplied, and can never change a score, a
  rank, or the stance.
* No field is named or implies a probability of success, profit, EMI or DSCR —
  those are Phase 4.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.market.assessment_models import MarketAssessmentLabel
from vyaparsarathi.market.metrics_models import CompetitionSignal
from vyaparsarathi.market.opportunity_config import OpportunityConfig
from vyaparsarathi.models.taxonomy import BusinessCategory


class OpportunityStatus(StrEnum):
    OK = "ok"
    NO_CANDIDATES = "no_candidates"  # nothing to score
    NO_EVIDENCE = "no_evidence"  # no candidate had a usable market reading


class AssetRelevance(StrEnum):
    ESSENTIAL = "essential"
    HELPFUL = "helpful"


class CapitalFit(StrEnum):
    """An indicative screen, never a verdict. Named so it cannot read as
    'impossible' / 'ineligible'."""

    UNKNOWN = "unknown"  # capital not stated, or no band for this business
    AFFORDABLE = "affordable"  # stated cash at or above the typical figure
    STRETCH = "stretch"  # between the indicative minimum and the typical figure
    OUT_OF_REACH = "out_of_reach"  # below the indicative minimum (screening estimate only)


class Stance(StrEnum):
    """Top-ranked is NOT the same as recommended (§12, approved clarification 3)."""

    PROPOSED_IS_BEST = "proposed_is_best"
    ALTERNATIVE_MATERIALLY_BETTER = "alternative_materially_better"
    ALTERNATIVES_COMPARABLE = "alternatives_comparable"
    NO_PROPOSAL_TO_COMPARE = "no_proposal_to_compare"  # nothing specific was proposed
    NO_RECOMMENDATION = "no_recommendation"  # no candidate had sufficient market evidence


class OpportunityEvidenceRef(BaseModel):
    """A typed pointer at the upstream field a component rests on, so a test can
    resolve the path and confirm the value (the Phase 2D auditability pattern,
    widened with ``profile`` / ``config`` sources)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["assessment", "competition", "demand", "profile", "config"]
    field: str
    value: float | int | str | bool | None
    compared_to: float | None = None


class ScoreComponent(BaseModel):
    """One named factor contribution (§12: 'every score decomposes into named
    factor contributions with the data behind each')."""

    model_config = ConfigDict(extra="forbid")

    name: str  # "market_opportunity" | "asset_fit" | "experience_fit"
    available: bool
    value: float | None = None  # 0..100 when available
    nominal_weight: float
    effective_weight: float  # nominal renormalised over available components; 0 when unavailable
    contribution: float | None = None  # value * effective_weight
    reason: str  # rendered from parts, never free-form LLM prose
    # "" when available; otherwise WHY it is missing — this drives renormalisation
    # vs. a hard capability gap.
    unavailable_kind: Literal["", "no_profile_input", "no_config_table"] = ""
    evidence: list[OpportunityEvidenceRef] = Field(default_factory=list)


class ScoredCandidate(BaseModel):
    """One business scored for this location + profile."""

    model_config = ConfigDict(extra="forbid")

    category: BusinessCategory
    is_proposed: bool = False
    in_candidate_universe: bool = True  # False => added only because it was proposed

    opportunity_score: int | None = None  # 0..100; None only if the market could not be read
    # Share of the total nominal scoring weight that had data (100 = every
    # component scored). The score itself is renormalised over just those
    # components, so a thin profile still yields a 0..100 number — this field is
    # how visible that thinness is.
    weight_coverage_pct: int = 100
    rank: int | None = None  # 1-based position in the ranked list

    market_label: MarketAssessmentLabel = MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    market_label_rung: str | None = None
    # True when the label is a real market state (not `insufficient_evidence`). A
    # candidate that is not evidence-sufficient can never become a pivot
    # recommendation (approved clarification 1).
    evidence_sufficient: bool = False
    # True when a component was unavailable for lack of a config table (not for
    # lack of profile input) — excluded from any 'materially better' claim.
    capability_incomplete: bool = False

    components: list[ScoreComponent] = Field(default_factory=list)
    components_missing: list[str] = Field(default_factory=list)

    capital_fit: CapitalFit = CapitalFit.UNKNOWN
    capital_fit_reason: str = ""

    # Per-candidate data-coverage confidence (competition half). Separate from the
    # score; never changes it.
    coverage_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    competition_signal: CompetitionSignal = CompetitionSignal.NONE
    direct_competitors: int = 0
    persons_per_direct_competitor: float | None = None

    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class FinancialFitInput(BaseModel):
    """Phase 4 seam (§12, §15). Accepted by ``score_opportunities`` but does
    **not** influence the score in the MVP — only its ``notes`` are surfaced onto
    the matching candidate. When Phase 4 lands it replaces the §5 capital screen
    and may add a fourth :class:`ScoreComponent`; the component list and weights
    are already config-owned, so that is a config + one-module change."""

    model_config = ConfigDict(extra="forbid")

    category: BusinessCategory
    feasible: bool | None = None
    capital_gap_inr: int | None = None
    required_promoter_margin_inr: int | None = None
    notes: list[str] = Field(default_factory=list)


class OpportunityAnalysisResult(BaseModel):
    """Phase 3 output. Deterministic, JSON-serializable."""

    model_config = ConfigDict(extra="forbid")

    status: OpportunityStatus

    location_text: str | None = None
    analysis_radius_m: int = 0
    proposed_category: BusinessCategory | None = None

    stance: Stance
    stance_reason: str = ""
    recommended_pivot: BusinessCategory | None = None  # set ONLY for ALTERNATIVE_MATERIALLY_BETTER

    candidates: list[ScoredCandidate] = Field(default_factory=list)  # ranked order, best first
    ranked_order: list[BusinessCategory] = Field(default_factory=list)

    # --- confidence: reported once, unmultiplied, changes nothing ---
    market_data_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    market_data_confidence_note: str = (
        "How well the local market is OBSERVED here (the Phase 2C demand-data "
        "coverage; each candidate also carries its own competition-side coverage "
        "confidence). NOT a viability or success measure, and it never changes a "
        "score or the ranking (CLAUDE.md §22)."
    )
    profile_completeness: float = Field(default=0.0, ge=0.0, le=1.0)

    nominal_weights: dict[str, float] = Field(default_factory=dict)
    caveats: list[str] = Field(default_factory=list)  # fixed, config-authored
    config: OpportunityConfig = Field(default_factory=OpportunityConfig)
    warnings: list[str] = Field(default_factory=list)
