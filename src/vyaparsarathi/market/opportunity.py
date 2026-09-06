"""Deterministic Phase 3 opportunity / pivot engine (CLAUDE.md §12).

Consumes an :class:`OpportunityEvidence` (one union discovery result + one demand
evidence + per-candidate coverage confidence, from
``discovery/opportunity_acquisition.py``) and an :class:`EntrepreneurProfile`.
Scores each candidate business by calling the Phase 2A -> 2B -> 2C -> 2D pipeline
once per candidate over the **same** evidence — Phase 2D was built to be called N
times (see ``docs/phase-2d.md``). Ranks them, and decides whether the proposed
business is the best of them or an alternative is materially better.

Pure: no network, disk, wall-clock, RNG or LLM (the ``market/`` no-I/O invariant,
``tests/test_market_purity.py``). It re-runs the pure engines; it never re-queries
OSM. It computes **no** finance — project cost, EMI, DSCR, cash flow, loan
structure, moratorium and stress tests are Phase 4.

Score composition (§12): ``opportunity_score`` decomposes into named
:class:`ScoreComponent` contributions —

* ``market_opportunity`` (weight 0.60) — the Phase 2D **label alone** (2D already
  fused competition + demand; re-deriving either would double-count).
* ``asset_fit`` (0.25) — owned assets vs a curated per-category relevance table.
* ``experience_fit`` (0.15) — trade experience vs the candidate, reusing
  ``relationship_for``.

Components with no data are dropped and the remaining weights are renormalised
(``opportunity_score = round(sum(value*w) / sum(w))`` over components that have a
value); the renormalisation is visible in every component's ``effective_weight``
and in ``components_missing`` / ``weight_coverage_pct``.

Ranking and the pivot recommendation are gated on the Phase 2D **label** (a
lattice), on ``evidence_sufficient``, on ``capability_incomplete`` and on
``capital_fit`` — never on the scalar alone. A higher score by itself is never a
recommendation.
"""

from __future__ import annotations

from vyaparsarathi.market.assessment import assess_market
from vyaparsarathi.market.assessment_models import MarketAssessmentLabel, MarketAssessmentStatus
from vyaparsarathi.market.classifier import analyze_competitors
from vyaparsarathi.market.demand import compute_demand_signals
from vyaparsarathi.market.metrics import compute_competition_metrics
from vyaparsarathi.market.metrics_models import CompetitionSignal
from vyaparsarathi.market.models import Relationship
from vyaparsarathi.market.opportunity_config import DEFAULT_OPPORTUNITY_CONFIG, OpportunityConfig
from vyaparsarathi.market.opportunity_models import (
    AssetRelevance,
    CapitalFit,
    FinancialFitInput,
    OpportunityAnalysisResult,
    OpportunityEvidenceRef,
    OpportunityStatus,
    ScoreComponent,
    ScoredCandidate,
    Stance,
)
from vyaparsarathi.market.proposed import proposed_from_category
from vyaparsarathi.market.relationships import relationship_for
from vyaparsarathi.models.opportunity import OpportunityEvidence
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_MARKET = "market_opportunity"
_ASSET = "asset_fit"
_EXPERIENCE = "experience_fit"


# -- per-candidate market pipeline -----------------------------------------


def _assess_candidate(
    evidence: OpportunityEvidence,
    category: BusinessCategory,
) -> tuple[
    MarketAssessmentLabel,
    str | None,
    bool,
    float,
    int,
    CompetitionSignal,
    float | None,
    list[str],
]:
    """Run 2A -> 2B -> 2C -> 2D for one candidate over the shared evidence.

    Returns ``(label, rung, evidence_sufficient, coverage_confidence,
    direct_count, competition_signal, persons_per_direct_competitor,
    warnings)``.
    """
    proposed = proposed_from_category(category)  # category granularity; no subtypes (uniform)
    conf = evidence.per_category_confidence.get(category, evidence.discovery.confidence)
    view = evidence.discovery.model_copy(update={"confidence": conf})

    analysis = analyze_competitors(view, proposed)
    metrics = compute_competition_metrics(analysis, view)
    demand = compute_demand_signals(evidence.demand, competition=metrics)
    assessment = assess_market(metrics, demand, analysis=analysis)

    rung = assessment.label_basis.get("rung")
    sufficient = (
        assessment.status is MarketAssessmentStatus.OK
        and assessment.label is not MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    )
    return (
        assessment.label,
        rung,
        sufficient,
        round(conf, 3),
        metrics.direct_count,
        metrics.signal,
        assessment.persons_per_direct_competitor,
        list(assessment.warnings),
    )


# -- the three components -------------------------------------------------


def _market_component(
    label: MarketAssessmentLabel, rung: str | None, cfg: OpportunityConfig
) -> ScoreComponent:
    points = cfg.label_points.get(label.value)
    value = float(points) if points is not None else None
    reason = (
        f"Phase 2D market label is '{label.value}'"
        + (f" (decided at ladder rung '{rung}')" if rung else "")
        + (f" -> {points}/100." if points is not None else " -> not scorable.")
    )
    return ScoreComponent(
        name=_MARKET,
        available=value is not None,
        value=value,
        nominal_weight=cfg.weight_market,
        effective_weight=0.0,
        contribution=None,
        reason=reason,
        unavailable_kind="" if value is not None else "no_config_table",
        evidence=[
            OpportunityEvidenceRef(source="assessment", field="label", value=label.value),
        ],
    )


def _asset_component(
    category: BusinessCategory, profile: EntrepreneurProfile, cfg: OpportunityConfig
) -> ScoreComponent:
    nominal = cfg.weight_asset
    if not profile.assets:
        return ScoreComponent(
            name=_ASSET,
            available=False,
            value=None,
            nominal_weight=nominal,
            effective_weight=0.0,
            contribution=None,
            reason="No assets recorded; asset fit was not scored.",
            unavailable_kind="no_profile_input",
            evidence=[OpportunityEvidenceRef(source="profile", field="assets", value=0)],
        )

    row = cfg.asset_relevance.get(category.value)
    if row is None:
        return ScoreComponent(
            name=_ASSET,
            available=False,
            value=None,
            nominal_weight=nominal,
            effective_weight=0.0,
            contribution=None,
            reason=(
                f"No asset-relevance profile is configured for '{category.value}', so asset "
                "fit could not be scored for this candidate."
            ),
            unavailable_kind="no_config_table",
            evidence=[
                OpportunityEvidenceRef(
                    source="config", field=f"asset_relevance.{category.value}", value=None
                )
            ],
        )

    essential = {AssetKind(k) for k, v in row.items() if v == AssetRelevance.ESSENTIAL.value}
    helpful = {AssetKind(k) for k, v in row.items() if v == AssetRelevance.HELPFUL.value}
    owned = set(profile.assets)
    ess_met = len(owned & essential) / len(essential) if essential else 1.0
    help_met = len(owned & helpful) / len(helpful) if helpful else 1.0
    value = 100.0 * (cfg.asset_essential_weight * ess_met + cfg.asset_helpful_weight * help_met)

    have_ess = sorted(a.value for a in owned & essential)
    have_help = sorted(a.value for a in owned & helpful)
    miss_ess = sorted(a.value for a in essential - owned)
    parts: list[str] = []
    if have_ess:
        parts.append(f"you own essential asset(s): {', '.join(have_ess)}")
    if miss_ess:
        parts.append(f"missing essential asset(s): {', '.join(miss_ess)}")
    if have_help:
        parts.append(f"you own helpful asset(s): {', '.join(have_help)}")
    if not parts:
        parts.append("none of the assets relevant to this business are owned")
    reason = f"Asset fit for '{category.value}': " + "; ".join(parts) + f" -> {value:.0f}/100."

    return ScoreComponent(
        name=_ASSET,
        available=True,
        value=value,
        nominal_weight=nominal,
        effective_weight=0.0,
        contribution=None,
        reason=reason,
        unavailable_kind="",
        evidence=[
            OpportunityEvidenceRef(
                source="profile",
                field="assets",
                value=", ".join(sorted(a.value for a in owned)) or "(none)",
            ),
            OpportunityEvidenceRef(
                source="config",
                field=f"asset_relevance.{category.value}",
                value=", ".join(f"{k}={v}" for k, v in sorted(row.items())),
            ),
        ],
    )


def _experience_component(
    category: BusinessCategory, profile: EntrepreneurProfile, cfg: OpportunityConfig
) -> ScoreComponent:
    nominal = cfg.weight_experience
    if not profile.experience_categories:
        return ScoreComponent(
            name=_EXPERIENCE,
            available=False,
            value=None,
            nominal_weight=nominal,
            effective_weight=0.0,
            contribution=None,
            reason="No trade experience recorded; experience fit was not scored.",
            unavailable_kind="no_profile_input",
            evidence=[
                OpportunityEvidenceRef(source="profile", field="experience_categories", value=0)
            ],
        )

    def _kind(exp: BusinessCategory) -> str:
        if exp is category:
            return "same"
        rel = relationship_for(category, exp)
        if rel is Relationship.DIRECT:
            return "direct"
        if rel is Relationship.ADJACENT:
            return "adjacent"
        return "unrelated"

    scored = {exp: cfg.experience_match_points[_kind(exp)] for exp in profile.experience_categories}
    best_exp, best_points = max(scored.items(), key=lambda kv: (kv[1], kv[0].value))
    value = float(best_points)
    reason = (
        f"Experience fit for '{category.value}': closest match is your '{best_exp.value}' "
        f"experience ({_kind(best_exp)}) -> {best_points}/100."
    )
    return ScoreComponent(
        name=_EXPERIENCE,
        available=True,
        value=value,
        nominal_weight=nominal,
        effective_weight=0.0,
        contribution=None,
        reason=reason,
        unavailable_kind="",
        evidence=[
            OpportunityEvidenceRef(
                source="profile",
                field="experience_categories",
                value=", ".join(sorted(c.value for c in profile.experience_categories)),
            ),
        ],
    )


def _combine(components: list[ScoreComponent]) -> tuple[int | None, int]:
    """``(opportunity_score, weight_coverage_pct)`` from the components in place.

    Renormalises over components that carry a value; mutates each component's
    ``effective_weight`` / ``contribution`` so the breakdown adds up.
    ``weight_coverage_pct`` is the share of the *total nominal* weight that had
    data (100 = every component scored).
    """
    valued = [c for c in components if c.value is not None]
    total_nominal = sum(c.nominal_weight for c in components) or 1.0
    den = sum(c.nominal_weight for c in valued)
    if den <= 0.0:
        return None, 0
    num = 0.0
    for c in components:
        if c.value is None:
            c.effective_weight = 0.0
            c.contribution = None
            continue
        c.effective_weight = round(c.nominal_weight / den, 4)
        c.contribution = round(c.value * c.nominal_weight / den, 2)
        num += c.value * c.nominal_weight / den
    return round(num), round(100.0 * den / total_nominal)


# -- capital screen (indicative only, never a verdict) -----------------


def _capital_fit(
    category: BusinessCategory, profile: EntrepreneurProfile, cfg: OpportunityConfig
) -> tuple[CapitalFit, str]:
    cash = profile.liquid_cash_inr
    band = cfg.capital_bands.get(category.value)
    if cash is None:
        return CapitalFit.UNKNOWN, "Capital not stated; this candidate was not screened on capital."
    if band is None:
        return (
            CapitalFit.UNKNOWN,
            f"No indicative capital band is configured for '{category.value}'; not screened.",
        )
    lo, hi = band
    if cash >= hi:
        return (
            CapitalFit.AFFORDABLE,
            f"Stated cash Rs {cash:,} is at or above the typical figure (Rs {hi:,}) for a "
            f"'{category.value}' business on this indicative screen.",
        )
    if cash >= lo:
        return (
            CapitalFit.STRETCH,
            f"Stated cash Rs {cash:,} is between the indicative minimum (Rs {lo:,}) and the "
            f"typical figure (Rs {hi:,}) for a '{category.value}' business — workable but tight "
            "on this rough screen.",
        )
    return (
        CapitalFit.OUT_OF_REACH,
        f"Stated cash Rs {cash:,} is below the indicative minimum (Rs {lo:,}) for a "
        f"'{category.value}' business on this screening estimate. This is not a financial "
        "assessment; detailed capital needs and financing are decided at a later stage.",
    )


# -- ranking + stance -------------------------------------------------


def _rank_key(c: ScoredCandidate) -> tuple[int, int, str]:
    score = c.opportunity_score if c.opportunity_score is not None else -1
    return (1 if c.capital_fit is CapitalFit.OUT_OF_REACH else 0, -score, c.category.value)


def _decide_stance(
    candidates: list[ScoredCandidate],
    proposed_category: BusinessCategory | None,
    cfg: OpportunityConfig,
) -> tuple[Stance, str, BusinessCategory | None]:
    if proposed_category is None:
        return (
            Stance.NO_PROPOSAL_TO_COMPARE,
            "No specific business was proposed; the shortlist is ranked for consideration only.",
            None,
        )
    if not any(c.evidence_sufficient for c in candidates):
        return (
            Stance.NO_RECOMMENDATION,
            "No candidate had sufficient local market evidence to support a recommendation; "
            "more local data is needed (a field visit, or additional data sources).",
            None,
        )

    proposed_c = next((c for c in candidates if c.category is proposed_category), None)
    if proposed_c is None:  # defensive; the proposed category is always in the list
        return (
            Stance.NO_PROPOSAL_TO_COMPARE,
            "The proposed business was not among the scored candidates.",
            None,
        )

    p_rank = cfg.label_lattice_rank.get(proposed_c.market_label.value, 0)
    for cand in candidates:  # candidates is already in rank order
        if cand.category is proposed_category:
            continue
        if not cand.evidence_sufficient or cand.capability_incomplete:
            continue
        if cand.capital_fit not in (CapitalFit.AFFORDABLE, CapitalFit.STRETCH):
            continue
        if cfg.label_lattice_rank.get(cand.market_label.value, 0) <= p_rank:
            continue
        gap = (cand.opportunity_score or 0) - (proposed_c.opportunity_score or 0)
        if gap < cfg.material_margin:
            continue
        return (
            Stance.ALTERNATIVE_MATERIALLY_BETTER,
            f"'{cand.category.value}' has a strictly stronger market label "
            f"('{cand.market_label.value}' vs '{proposed_c.market_label.value}'), sufficient "
            f"evidence, a resolved capital fit, and a score {gap} point(s) higher than the "
            f"proposed '{proposed_category.value}'.",
            cand.category,
        )

    best_score = max(
        (c.opportunity_score for c in candidates if c.opportunity_score is not None),
        default=None,
    )
    proposed_at_top = (
        proposed_c.evidence_sufficient
        and proposed_c.opportunity_score is not None
        and best_score is not None
        and proposed_c.opportunity_score >= best_score
    )
    if proposed_at_top:
        return (
            Stance.PROPOSED_IS_BEST,
            f"The proposed '{proposed_category.value}' scores at least as high as every "
            "alternative on the available evidence, and no alternative clears the bar for a "
            "materially-better call.",
            None,
        )
    return (
        Stance.ALTERNATIVES_COMPARABLE,
        "One or more candidates rank alongside or above the proposed business, but none "
        "clears every gate for a materially-better call (a strictly stronger AND "
        "evidence-sufficient label, no capability gap, and a resolved capital fit).",
        None,
    )


# -- public --------------------------------------------------------


def score_opportunities(
    evidence: OpportunityEvidence,
    profile: EntrepreneurProfile,
    *,
    config: OpportunityConfig | None = None,
    financial_fit: dict[BusinessCategory, FinancialFitInput] | None = None,
) -> OpportunityAnalysisResult:
    """Score and rank the candidate businesses for this location + profile.

    ``financial_fit`` (Phase 4 seam) is accepted but does **not** influence the
    score in the MVP; any ``notes`` on a matching entry are copied onto that
    candidate's ``warnings``.
    """
    cfg = config or DEFAULT_OPPORTUNITY_CONFIG

    proposed_category: BusinessCategory | None = profile.proposed_category
    if proposed_category is BusinessCategory.UNKNOWN:
        proposed_category = None

    # Candidate list: exactly what acquisition resolved, plus the proposed
    # category if it somehow is not already present.
    categories: list[BusinessCategory] = list(evidence.candidate_categories)
    if proposed_category is not None and proposed_category not in categories:
        categories.append(proposed_category)

    location_text = evidence.discovery.query_text
    radius_m = (
        evidence.discovery.query.radius_m
        if evidence.discovery.query is not None
        else evidence.discovery.requested_radius_m
    )

    profile_has_assets = bool(profile.assets)
    profile_has_experience = bool(profile.experience_categories)

    scored: list[ScoredCandidate] = []
    market_data_confidence = 0.0
    for category in categories:
        label, rung, sufficient, conf, direct, signal, ppc, warns = _assess_candidate(
            evidence, category
        )
        # demand-data confidence is candidate-invariant; capture it once.
        if market_data_confidence == 0.0:
            demand_only = compute_demand_signals(evidence.demand)
            market_data_confidence = round(demand_only.demand_data_confidence, 3)

        components = [
            _market_component(label, rung, cfg),
            _asset_component(category, profile, cfg),
            _experience_component(category, profile, cfg),
        ]
        score, weight_coverage_pct = _combine(components)
        missing = [c.name for c in components if not c.available]
        capability_incomplete = any(c.unavailable_kind == "no_config_table" for c in components)
        capital_fit, capital_reason = _capital_fit(category, profile, cfg)

        reasons = [c.reason for c in components]
        reasons.append(capital_reason)
        if score is not None:
            reasons.append(f"Overall opportunity score {score}/100.")

        scored.append(
            ScoredCandidate(
                category=category,
                is_proposed=category is proposed_category,
                in_candidate_universe=category.value in cfg.candidate_categories,
                opportunity_score=score,
                weight_coverage_pct=weight_coverage_pct,
                market_label=label,
                market_label_rung=rung,
                evidence_sufficient=sufficient,
                capability_incomplete=capability_incomplete,
                components=components,
                components_missing=missing,
                capital_fit=capital_fit,
                capital_fit_reason=capital_reason,
                coverage_confidence=conf,
                competition_signal=signal,
                direct_competitors=direct,
                persons_per_direct_competitor=ppc,
                reasons=reasons,
                warnings=warns,
            )
        )

    # financial_fit (Phase 4 seam) — notes only, no scoring effect.
    result_warnings = list(evidence.warnings)
    if financial_fit:
        for cand in scored:
            fi = financial_fit.get(cand.category)
            if fi is not None and fi.notes:
                cand.warnings.extend(f"[financial-fit input] {n}" for n in fi.notes)
        result_warnings.append(
            "financial_fit inputs were supplied; they are recorded on candidates but do NOT "
            "affect Phase 3 scoring (that is Phase 4)."
        )

    scored.sort(key=_rank_key)
    for i, cand in enumerate(scored, 1):
        cand.rank = i

    stance, stance_reason, pivot = _decide_stance(scored, proposed_category, cfg)

    if not scored:
        status = OpportunityStatus.NO_CANDIDATES
    elif not any(c.evidence_sufficient for c in scored):
        status = OpportunityStatus.NO_EVIDENCE
    else:
        status = OpportunityStatus.OK

    profile_completeness = round(
        sum([profile.liquid_cash_inr is not None, profile_has_assets, profile_has_experience])
        / 3.0,
        3,
    )

    logger.info(
        "opportunity scan (%s, r=%dm): %d candidate(s), stance=%s, pivot=%s",
        location_text,
        radius_m,
        len(scored),
        stance.value,
        pivot.value if pivot is not None else "-",
    )

    return OpportunityAnalysisResult(
        status=status,
        location_text=location_text,
        analysis_radius_m=radius_m,
        proposed_category=proposed_category,
        stance=stance,
        stance_reason=stance_reason,
        recommended_pivot=pivot,
        candidates=scored,
        ranked_order=[c.category for c in scored],
        market_data_confidence=market_data_confidence,
        profile_completeness=profile_completeness,
        nominal_weights={
            _MARKET: cfg.weight_market,
            _ASSET: cfg.weight_asset,
            _EXPERIENCE: cfg.weight_experience,
        },
        caveats=list(cfg.caveats),
        config=cfg,
        warnings=result_warnings,
    )
