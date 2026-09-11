"""Per-section builders for the DPR (CLAUDE.md §25 Phase 8). PURE.

Each `build_*` takes the reconstructed `ArtifactSet` and the session's slots
and returns one section model. Nothing here runs an engine, reads a clock, or
touches the network — it only reshapes results that already exist, wrapping
every figure in a `ProvenancedValue` and turning every absent input into an
explicit gap. This module is intentionally wide (one cohesive builder per
report section); the orchestration and roll-ups live in `assemble.py`.
"""

from __future__ import annotations

from collections.abc import Callable

from vyaparsarathi.conversation.session_models import ConversationSession, SlotName, SlotState
from vyaparsarathi.dpr.artifacts import ArtifactSet
from vyaparsarathi.dpr.disclaimers import (
    DECLARED_CONFIG_NOTE,
    FINANCIAL_INCOMPLETE_NOTE,
    MARKET_COMPLETENESS_NOTE,
    PROFILE_UNVERIFIED_NOTE,
    PROJECT_PLAN_NOTE,
)
from vyaparsarathi.dpr.format import (
    format_confidence,
    format_distance_m,
    format_inr,
    format_inr_words,
    format_months,
    format_pct_value,
    format_ratio_pct,
    humanise_token,
)
from vyaparsarathi.dpr.provenance import (
    GapReason,
    ProvenancedValue,
    pv_assumed,
    pv_calc,
    pv_config,
    pv_missing,
    pv_sourced,
    pv_user,
)
from vyaparsarathi.dpr.report_models import (
    AlternativeOption,
    EntrepreneurProfileSection,
    FactorBreakdown,
    FinancialAssessmentSection,
    LabeledItem,
    MarketAssessmentSection,
    OpportunitySection,
    ParameterLine,
    PassageLine,
    ProjectPlanSection,
    RisksSwotSection,
    SchemeKnowledgeSection,
    SectionStatus,
    StressLine,
    SwotLine,
)
from vyaparsarathi.dpr.slots import as_decimal, slot_value
from vyaparsarathi.models.finance import FinancialInput, InputKind, LoanTerms
from vyaparsarathi.models.parameters import ParameterName, ResolutionStatus
from vyaparsarathi.models.results import DiscoveryStatus

_SCHEME_PARAMS = (
    ParameterName.INTEREST_RATE_PCT,
    ParameterName.LOAN_TENURE_MONTHS,
    ParameterName.MORATORIUM_MONTHS,
    ParameterName.PROMOTER_MARGIN_PCT,
    ParameterName.LOAN_CEILING_INR,
    ParameterName.SUBSIDY_PCT,
)
_STATUTORY_PARAMS = (ParameterName.LICENCE_FEE_INR, ParameterName.SECURITY_DEPOSIT_MONTHS)

# Scheme parameters that, even with no independently-cited source, are
# already answered elsewhere in this report via the SIH DECLARED_CONFIG
# financing structure (used to compute the EMI/DSCR in the Financial
# assessment section). An unresolved value for one of these must read as
# "not independently verified" rather than a flat "no evidence" — the
# report already states a number for it, just not a scheme-cited one.
_DECLARED_CONFIG_BACKED_PARAMS = frozenset(
    {
        ParameterName.INTEREST_RATE_PCT,
        ParameterName.LOAN_TENURE_MONTHS,
        ParameterName.MORATORIUM_MONTHS,
        ParameterName.PROMOTER_MARGIN_PCT,
    }
)


# --- shared helpers ------------------------------------------------------


def _kb_ids(arts: ArtifactSet) -> dict[str, str]:
    """`ParameterName.value -> "KB<n>"`, matching `citations.build_citations`."""
    ids: dict[str, str] = {}
    if arts.knowledge is None:
        return ids
    n = 0
    for res in arts.knowledge.resolutions:
        if res.status is ResolutionStatus.RESOLVED and res.chosen is not None:
            n += 1
            ids[res.name.value] = f"KB{n}"
    return ids


def _fin_input_pv(
    fi: FinancialInput | None, label: str, fmt: Callable[[object], str]
) -> ProvenancedValue:
    if fi is None:
        return pv_missing(label, reason=GapReason.INPUT_REQUIRED)
    display = fmt(fi.value)
    raw = str(fi.value)
    if fi.kind is InputKind.USER_PROVIDED:
        return pv_user(label, display, raw=raw)
    if fi.kind is InputKind.ASSUMED:
        maker = pv_config if (fi.source or "") == "config:sih_scheme" else pv_assumed
        return maker(label, display, rationale=fi.rationale, raw=raw)
    if fi.kind is InputKind.SOURCED:
        return pv_sourced(
            label, display, citation_id=fi.source_ref or fi.source or "source", raw=raw
        )
    return pv_calc(label, display, inputs=tuple(fi.calculated_from) or ("engine",), raw=raw)


# --- profile -----------------------------------------------------------


def build_profile_section(
    session: ConversationSession, arts: ArtifactSet
) -> EntrepreneurProfileSection:
    capital = slot_value(
        session,
        SlotName.LIQUID_CASH_INR,
        label="Available Margin Capital (liquid cash)",
        fmt=lambda v: format_inr_words(as_decimal(v)),
    )

    assets_slot = session.assets.current
    owned: list[LabeledItem] = []
    if assets_slot.state is SlotState.USER_PROVIDED:
        for kind in sorted(a.value for a in assets_slot.items):
            owned.append(LabeledItem(label=kind, detail="owned (stated)", origin="user_provided"))
    elif assets_slot.state is SlotState.DECLINED:
        owned.append(
            LabeledItem(label="Owned assets", detail="None declared", origin="user_provided")
        )

    exp = tuple(sorted(c.value for c in session.experience_categories.current.items))
    years = slot_value(
        session,
        SlotName.YEARS_EXPERIENCE,
        label="Years of relevant experience",
        fmt=lambda v: f"{int(as_decimal(v))} year(s)",
        missing_reason=GapReason.NO_EVIDENCE,
    )
    proposed = slot_value(
        session,
        SlotName.PROPOSED_BUSINESS_TEXT,
        label="Proposed enterprise (as stated)",
        fmt=lambda v: str(v),
    )
    if session.resolved_category is not None and session.resolved_category_resolved:
        resolved_cat = pv_calc(
            "Standardised category",
            session.resolved_category.value,
            inputs=("proposed_business_text", "market.proposed.resolve_proposed_business"),
        )
    else:
        resolved_cat = pv_missing(
            "Standardised category",
            reason=GapReason.NO_EVIDENCE,
            note="the stated business could not be mapped to one internal category",
        )

    status = SectionStatus.RENDERED
    if capital.origin.value == "not_available" and not exp and not owned:
        status = SectionStatus.PARTIAL

    return EntrepreneurProfileSection(
        title="Entrepreneur and project profile",
        status=status,
        available_margin_capital=capital,
        owned_assets=tuple(owned),
        experience_categories=exp,
        years_experience=years,
        proposed_enterprise=proposed,
        resolved_category=resolved_cat,
        constraints=(),
        unverified_note=PROFILE_UNVERIFIED_NOTE,
    )


# --- market ----------------------------------------------------------


_DISCOVERY_GAP = {
    DiscoveryStatus.NO_RESULTS: "The location resolved but no nearby-business data was returned.",
    DiscoveryStatus.LOCATION_AMBIGUOUS: "The location text matched more than one place; it was "
    "never disambiguated, so no market analysis could run.",
    DiscoveryStatus.LOCATION_NOT_FOUND: "The stated location could not be resolved to a place.",
    DiscoveryStatus.SOURCE_UNAVAILABLE: "Every business-data source and mirror was unreachable.",
}


def build_market_section(
    session: ConversationSession, arts: ArtifactSet
) -> MarketAssessmentSection:
    disc = arts.discovery
    completeness = MARKET_COMPLETENESS_NOTE

    if disc is None:
        return MarketAssessmentSection(
            title="Local market assessment",
            status=SectionStatus.EVIDENCE_GAP,
            gap_note="Business discovery has not run for this session.",
            resolved_location=pv_missing("Resolved location", reason=GapReason.NO_EVIDENCE),
            data_confidence=pv_missing("Market-data confidence", reason=GapReason.NO_EVIDENCE),
            direct_competitors=pv_missing("Direct competitors", reason=GapReason.NO_EVIDENCE),
            adjacent_competitors=pv_missing("Adjacent competitors", reason=GapReason.NO_EVIDENCE),
            nearest_competitor=pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE),
            competition_signal=pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE),
            market_label=pv_missing("Market reading", reason=GapReason.NO_EVIDENCE),
            completeness_note=completeness,
        )

    if disc.status is not DiscoveryStatus.OK:
        note = _DISCOVERY_GAP.get(disc.status, "Discovery did not complete.")
        if disc.status is DiscoveryStatus.LOCATION_AMBIGUOUS and disc.candidates:
            note += " Candidates: " + "; ".join(c.display_name for c in disc.candidates[:6])
        return MarketAssessmentSection(
            title="Local market assessment",
            status=SectionStatus.EVIDENCE_GAP,
            gap_note=note,
            resolved_location=pv_missing("Resolved location", reason=GapReason.AMBIGUOUS)
            if disc.status is DiscoveryStatus.LOCATION_AMBIGUOUS
            else pv_missing("Resolved location", reason=GapReason.NO_EVIDENCE),
            data_confidence=pv_missing("Market-data confidence", reason=GapReason.NO_EVIDENCE),
            direct_competitors=pv_missing("Direct competitors", reason=GapReason.NO_EVIDENCE),
            adjacent_competitors=pv_missing("Adjacent competitors", reason=GapReason.NO_EVIDENCE),
            nearest_competitor=pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE),
            competition_signal=pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE),
            market_label=pv_missing("Market reading", reason=GapReason.NO_EVIDENCE),
            completeness_note=completeness,
        )

    place = disc.resolved_place
    loc_name = place.display_name if place is not None else disc.query_text
    resolved_location = pv_calc(
        "Resolved location",
        loc_name,
        inputs=("location_text", "geocoding.nominatim"),
        note="geocoded from the stated location text",
    )
    admin: list[LabeledItem] = []
    if place is not None:
        for key, val in (
            ("Village / town", place.village),
            ("Block / tehsil", place.block),
            ("District", place.district),
            ("State", place.state),
        ):
            if val:
                admin.append(LabeledItem(label=key, detail=val, origin="sourced"))

    coverage: list[LabeledItem] = []
    for ps in disc.coverage.per_source:
        detail = (
            f"{ps.raw_records} raw, {ps.normalized} normalised, "
            f"{ps.dropped_no_coordinates} dropped (no coords)"
        )
        if ps.mirror_fallback_used:
            detail += "; mirror fallback used"
        if ps.endpoint_used:
            detail += f"; endpoint {ps.endpoint_used}"
        coverage.append(LabeledItem(label=ps.source.value, detail=detail, origin="sourced"))
    coverage.append(
        LabeledItem(
            label="De-duplication",
            detail=(
                f"{disc.coverage.total_before_dedup} before, "
                f"{disc.coverage.total_after_dedup} after, "
                f"{disc.coverage.duplicates_merged} merged"
            ),
            origin="calculated",
        )
    )

    data_confidence = pv_calc(
        "Market-data confidence",
        format_confidence(disc.confidence),
        inputs=("discovery.coverage",),
        note="how completely the local market could be observed; NOT a viability measure",
    )

    metrics = arts.metrics
    analysis = arts.analysis
    # A skipped competitor analysis (category never resolved, so nothing
    # could be classified against it) must never render as a bare "0" —
    # that reads as "we checked and found none," not "we couldn't check."
    _not_evaluated_note = (
        "Competitor discovery was skipped because the business category "
        "could not be classified — this is not a finding of zero competitors."
    )
    if metrics is not None and metrics.status.value == "unknown_category":
        direct_pv = pv_missing(
            "Direct competitors", reason=GapReason.NO_EVIDENCE, note=_not_evaluated_note
        )
        adjacent_pv = pv_missing(
            "Adjacent competitors", reason=GapReason.NO_EVIDENCE, note=_not_evaluated_note
        )
        nearest_pv = pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE)
        signal_pv = pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE)
    elif metrics is not None:
        direct_pv = pv_calc(
            "Direct competitors", str(metrics.direct_count), inputs=("analyze", "discover")
        )
        adjacent_pv = pv_calc(
            "Adjacent competitors", str(metrics.adjacent_count), inputs=("analyze", "discover")
        )
        nearest_pv = (
            pv_calc(
                "Nearest competitor",
                format_distance_m(metrics.direct_distance.nearest_m),
                inputs=("metrics.direct_distance",),
            )
            if metrics.direct_distance.nearest_m is not None
            else pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE)
        )
        signal_pv = pv_calc(
            "Competition signal",
            humanise_token(metrics.signal.value),
            inputs=("metrics.signal_basis",),
            note=metrics.signal_reason,
        )
    elif analysis is not None and analysis.status.value == "unknown_category":
        direct_pv = pv_missing(
            "Direct competitors", reason=GapReason.NO_EVIDENCE, note=_not_evaluated_note
        )
        adjacent_pv = pv_missing(
            "Adjacent competitors", reason=GapReason.NO_EVIDENCE, note=_not_evaluated_note
        )
        nearest_pv = pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE)
        signal_pv = pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE)
    elif analysis is not None:
        direct_pv = pv_calc(
            "Direct competitors", str(len(analysis.direct_competitors)), inputs=("analyze",)
        )
        adjacent_pv = pv_calc(
            "Adjacent competitors", str(len(analysis.adjacent_competitors)), inputs=("analyze",)
        )
        nearest_pv = pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE)
        signal_pv = pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE)
    else:
        direct_pv = pv_missing("Direct competitors", reason=GapReason.NO_EVIDENCE)
        adjacent_pv = pv_missing("Adjacent competitors", reason=GapReason.NO_EVIDENCE)
        nearest_pv = pv_missing("Nearest competitor", reason=GapReason.NO_EVIDENCE)
        signal_pv = pv_missing("Competition signal", reason=GapReason.NO_EVIDENCE)

    demand = arts.demand
    demand_items: list[LabeledItem] = []
    if demand is not None:
        cp = demand.catchment
        if cp.persons is not None:
            floor = " (lower bound — partial coverage)" if cp.is_floor else ""
            demand_items.append(
                LabeledItem(
                    label="Catchment population",
                    detail=f"{cp.persons:,} residents{floor}, coverage "
                    f"{(cp.population_coverage or 0):.0%}",
                    origin="sourced",
                )
            )
        if cp.households is not None:
            demand_items.append(
                LabeledItem(label="Households", detail=f"{cp.households:,}", origin="sourced")
            )
            radius_slot = session.slot(SlotName.RADIUS_M)
            radius_km = (
                f"{float(as_decimal(radius_slot.value)) / 1000:.0f}"
                if radius_slot.value is not None
                else "a few"
            )
            demand_items.append(
                LabeledItem(
                    label="Market reach, in plain words",
                    detail=(
                        f"Roughly {cp.households:,} households live within {radius_km} km of "
                        "your location — the customers you can realistically reach on foot or "
                        "a short trip."
                    ),
                    origin="calculated",
                )
            )
        demand_items.append(
            LabeledItem(
                label="Settlements in catchment",
                detail=f"{cp.settlements_found} ({cp.settlements_with_population} with population)",
                origin="sourced",
            )
        )
        demand_items.append(
            LabeledItem(
                label="Public-service activity points",
                detail=f"{demand.activity.total_points} "
                f"across {len(demand.activity.counts_by_kind)} kind(s)",
                origin="sourced",
            )
        )
        demand_items.append(
            LabeledItem(
                label="Demand-data confidence",
                detail=format_confidence(demand.demand_data_confidence),
                origin="calculated",
            )
        )

    market = arts.market
    if market is not None:
        label_pv = pv_calc(
            "Market reading",
            humanise_token(market.label.value),
            inputs=("metrics", "demand_signals"),
            note="a market-state description, not an action",
        )
        label_reason = market.label_reason
        caveats = tuple(f.message for f in market.data_caveats) + tuple(market.assessment_caveats)
    else:
        label_pv = pv_missing("Market reading", reason=GapReason.NO_EVIDENCE)
        label_reason = ""
        caveats = ()

    status = SectionStatus.RENDERED
    if metrics is None and analysis is None:
        status = SectionStatus.PARTIAL

    return MarketAssessmentSection(
        title="Local market assessment",
        status=status,
        resolved_location=resolved_location,
        admin_hierarchy=tuple(admin),
        source_coverage=tuple(coverage),
        data_confidence=data_confidence,
        direct_competitors=direct_pv,
        adjacent_competitors=adjacent_pv,
        nearest_competitor=nearest_pv,
        competition_signal=signal_pv,
        demand_signals=tuple(demand_items),
        market_label=label_pv,
        label_reason=label_reason,
        caveats=caveats,
        completeness_note=completeness,
    )


# --- opportunity ---------------------------------------------------------


def build_opportunity_section(
    session: ConversationSession, arts: ArtifactSet
) -> OpportunitySection:
    opp = arts.opportunity
    if opp is None or opp.status.value != "ok":
        note = (
            "The opportunity engine has not run."
            if opp is None
            else f"The opportunity engine returned '{opp.status.value}' — "
            "no candidate had a usable market reading."
        )
        return OpportunitySection(
            title="Opportunity and alternatives",
            status=SectionStatus.EVIDENCE_GAP,
            gap_note=note,
            proposed_score=pv_missing(
                "Proposed-business opportunity score", reason=GapReason.NO_EVIDENCE
            ),
            stance=pv_missing("Stance", reason=GapReason.NO_EVIDENCE),
            recommended_pivot=pv_missing("Recommended pivot", reason=GapReason.NO_EVIDENCE),
            market_data_confidence=pv_missing(
                "Market-data confidence", reason=GapReason.NO_EVIDENCE
            ),
        )

    proposed = next((c for c in opp.candidates if c.is_proposed), None)
    if proposed is not None and proposed.opportunity_score is not None:
        score_pv = pv_calc(
            "Proposed-business opportunity score",
            f"{proposed.opportunity_score}/100",
            inputs=("market_opportunity", "asset_fit", "experience_fit"),
            note=f"weight coverage {proposed.weight_coverage_pct}%",
        )
    else:
        score_pv = pv_missing(
            "Proposed-business opportunity score",
            reason=GapReason.NO_EVIDENCE,
            note="the proposed business had no usable market reading"
            if proposed is not None
            else "no specific business was proposed",
        )

    factors: list[FactorBreakdown] = []
    for comp in proposed.components if proposed is not None else []:
        contribution = (
            pv_calc(
                comp.name,
                f"{comp.contribution:.1f}" if comp.contribution is not None else "0.0",
                inputs=tuple(f"{e.source}.{e.field}" for e in comp.evidence) or ("engine",),
            )
            if comp.available
            else pv_missing(comp.name, reason=GapReason.NO_EVIDENCE, note=comp.reason)
        )
        factors.append(
            FactorBreakdown(
                name=comp.name,
                available=comp.available,
                contribution=contribution,
                weight_pct=f"{comp.nominal_weight:.0%}",
                reason=comp.reason,
                villager_reason=comp.villager_reason,
            )
        )

    alternatives: list[AlternativeOption] = []
    for cand in opp.candidates:
        if cand.is_proposed or cand.opportunity_score is None:
            continue
        alternatives.append(
            AlternativeOption(
                business=cand.category.value,
                score=pv_calc(
                    f"{cand.category.value} score",
                    f"{cand.opportunity_score}/100",
                    inputs=("market_opportunity", "asset_fit", "experience_fit"),
                ),
                market_label=humanise_token(cand.market_label.value),
                rank=cand.rank,
                reasons=tuple(cand.reasons[:3]),
                villager_reasons=tuple(cand.villager_reasons[:3]),
                capital_fit=humanise_token(cand.capital_fit.value),
            )
        )
    alternatives.sort(key=lambda a: (a.rank is None, a.rank or 0))

    pivot_pv = (
        pv_calc(
            "Recommended pivot",
            opp.recommended_pivot.value,
            inputs=("opportunity.ranking", "opportunity.stance"),
        )
        if opp.recommended_pivot is not None
        else pv_missing(
            "Recommended pivot",
            reason=GapReason.NO_EVIDENCE,
            note="no alternative scored materially higher than the proposed business",
        )
    )

    return OpportunitySection(
        title="Opportunity and alternatives",
        status=SectionStatus.RENDERED,
        proposed_score=score_pv,
        stance=pv_calc("Stance", humanise_token(opp.stance.value), inputs=("opportunity.ranking",)),
        stance_reason=opp.stance_reason,
        factors=tuple(factors),
        alternatives=tuple(alternatives[:5]),
        recommended_pivot=pivot_pv,
        market_data_confidence=pv_calc(
            "Market-data confidence",
            format_confidence(opp.market_data_confidence),
            inputs=("demand_signals.coverage",),
        ),
        caveats=tuple(opp.caveats),
    )


# --- project plan --------------------------------------------------------


def build_project_plan_section(
    session: ConversationSession, arts: ArtifactSet
) -> ProjectPlanSection:
    if session.resolved_category is not None and session.resolved_category_resolved:
        cat_pv = pv_calc(
            "Business category",
            session.resolved_category.value,
            inputs=("proposed_business_text",),
        )
    else:
        cat_pv = pv_missing("Business category", reason=GapReason.NO_EVIDENCE)

    radius = slot_value(
        session,
        SlotName.RADIUS_M,
        label="Catchment radius used",
        fmt=lambda v: f"{int(as_decimal(v)):,} m",
        missing_reason=GapReason.NO_EVIDENCE,
    )

    cogs_slot = session.slot(SlotName.COGS_PCT)
    margin_note = ""
    if cogs_slot.value is not None:
        cogs_pct = float(as_decimal(cogs_slot.value)) * 100
        margin_pct = 100 - cogs_pct
        margin_note = (
            f"In plain words: if {format_ratio_pct(as_decimal(cogs_slot.value))} of every rupee "
            f"of sales goes to buying stock, roughly {margin_pct:.0f}% is left as gross margin "
            "before your fixed costs and loan repayment — this is not a price recommendation, "
            "just what your own stated numbers imply."
        )

    return ProjectPlanSection(
        title="Project and operating plan",
        status=SectionStatus.RENDERED,
        category=cat_pv,
        catchment_radius=radius,
        stated_project_cost=slot_value(
            session,
            SlotName.PROJECT_COST_INR,
            label="One-time project / setup cost (stated)",
            fmt=lambda v: format_inr_words(as_decimal(v)),
        ),
        stated_monthly_revenue=slot_value(
            session,
            SlotName.MONTHLY_REVENUE_INR,
            label="Expected monthly revenue (stated)",
            fmt=lambda v: format_inr_words(as_decimal(v)),
        ),
        stated_cogs_pct=slot_value(
            session,
            SlotName.COGS_PCT,
            label="Cost of goods as a share of revenue (stated)",
            fmt=lambda v: format_ratio_pct(as_decimal(v)),
        ),
        stated_fixed_opex=slot_value(
            session,
            SlotName.FIXED_OPEX_INR,
            label="Monthly fixed operating cost (stated)",
            fmt=lambda v: format_inr_words(as_decimal(v)),
        ),
        note=PROJECT_PLAN_NOTE,
        margin_note=margin_note,
    )


# --- financial ---------------------------------------------------------


def _loan_terms_pvs(
    loan: LoanTerms | None, *, declared: bool
) -> tuple[ProvenancedValue, ProvenancedValue, ProvenancedValue, ProvenancedValue]:
    if loan is None:
        return (
            pv_missing("Loan principal", reason=GapReason.INPUT_REQUIRED),
            pv_missing("Interest rate", reason=GapReason.INPUT_REQUIRED),
            pv_missing("Loan tenure", reason=GapReason.INPUT_REQUIRED),
            pv_missing("Moratorium", reason=GapReason.INPUT_REQUIRED),
        )
    if declared:
        rationale = DECLARED_CONFIG_NOTE
        return (
            pv_config(
                "Loan principal", format_inr(loan.principal_requested.value), rationale=rationale
            ),
            pv_config(
                "Interest rate", format_pct_value(loan.interest_rate_pct.value), rationale=rationale
            ),
            pv_config(
                "Loan tenure", format_months(int(loan.tenure_months.value)), rationale=rationale
            ),
            pv_config(
                "Moratorium", format_months(int(loan.moratorium_months.value)), rationale=rationale
            ),
        )
    return (
        _fin_input_pv(loan.principal_requested, "Loan principal", format_inr),
        _fin_input_pv(loan.interest_rate_pct, "Interest rate", format_pct_value),
        _fin_input_pv(loan.tenure_months, "Loan tenure", format_months),
        _fin_input_pv(loan.moratorium_months, "Moratorium", format_months),
    )


def build_financial_section(
    session: ConversationSession, arts: ArtifactSet
) -> FinancialAssessmentSection:
    fin = arts.finance
    structure = arts.structure
    capacity = arts.scheme_capacity
    plan = arts.plan

    missing_drivers: tuple[str, ...] = tuple(fin.missing_core_drivers) if fin is not None else ()
    incomplete = fin is None or fin.status.value == "insufficient_financial_evidence"

    if fin is not None:
        status_pv = pv_calc(
            "Financial feasibility status",
            humanise_token(fin.status.value),
            inputs=(f"finance.rung={fin.rung.value}",),
        )
        rung = fin.rung.value
    else:
        status_pv = pv_missing("Financial feasibility status", reason=GapReason.NO_EVIDENCE)
        rung = ""

    # project cost — prefer the real Phase 4 computation, then the declared-config screen
    if fin is not None and fin.project_cost is not None:
        project_cost_pv = pv_calc(
            "Project cost",
            format_inr(fin.project_cost.project_cost_inr),
            inputs=("cost lines", "contingency", "net working capital"),
        )
    elif structure is not None and structure.project_cost_inr is not None:
        project_cost_pv = pv_calc(
            "Project cost",
            format_inr(structure.project_cost_inr),
            inputs=("build_plan cost lines",),
        )
    elif capacity is not None and capacity.feasible_project_cost_inr is not None:
        project_cost_pv = pv_config(
            "Feasible project cost (capacity screen)",
            format_inr(capacity.feasible_project_cost_inr),
            rationale="Derived from Available Margin Capital and the declared SIH 10%/90% "
            "split — a capacity screen, not the actual business's costed project.",
        )
    else:
        project_cost_pv = pv_missing("Project cost", reason=GapReason.INPUT_REQUIRED)

    # This is the entrepreneur's STATED available liquid cash — a ceiling on
    # what they could put in, never a record of what this particular
    # business's (possibly much smaller) project cost actually draws on. The
    # label says so explicitly; "capital_remaining_after_margin" below says
    # how much of it is left over once the real requirement is known.
    available_cash_input = plan.financing.promoter_cash_contribution if plan is not None else None
    promoter_contribution_pv = _fin_input_pv(
        available_cash_input,
        "Available promoter cash (stated)",
        lambda v: format_inr(v),
    )

    req_margin = None
    indicated_loan = None
    margin_shortfall = None
    scheme_name = ""
    if structure is not None and structure.status.value == "structured":
        req_margin = structure.required_promoter_margin_inr
        indicated_loan = structure.indicated_loan_inr
        margin_shortfall = structure.margin_shortfall_inr
        scheme_name = structure.scheme_name
    elif capacity is not None and capacity.status.value == "calculated":
        req_margin = capacity.required_promoter_margin_inr
        indicated_loan = capacity.indicated_loan_inr
        scheme_name = capacity.scheme_name

    req_margin_pv = (
        pv_config(
            "Required promoter margin",
            format_inr(req_margin),
            rationale=DECLARED_CONFIG_NOTE,
        )
        if req_margin is not None
        else pv_missing("Required promoter margin", reason=GapReason.NO_EVIDENCE)
    )
    indicated_loan_pv = (
        pv_config("Indicated loan", format_inr(indicated_loan), rationale=DECLARED_CONFIG_NOTE)
        if indicated_loan is not None
        else pv_missing("Indicated loan", reason=GapReason.NO_EVIDENCE)
    )
    margin_shortfall_pv = (
        pv_calc(
            "Margin shortfall vs stated cash",
            format_inr(margin_shortfall),
            inputs=("required promoter margin", "stated liquid cash"),
        )
        if margin_shortfall is not None
        else pv_missing("Margin shortfall vs stated cash", reason=GapReason.NO_EVIDENCE)
    )
    scheme_pv = (
        pv_config("Financing structure", scheme_name, rationale=DECLARED_CONFIG_NOTE)
        if scheme_name
        else pv_missing("Financing structure", reason=GapReason.NO_EVIDENCE)
    )

    capital_remaining_pv = (
        pv_calc(
            "Capital remaining after margin",
            format_inr(available_cash_input.value - req_margin),
            inputs=("available promoter cash (stated)", "required promoter margin"),
        )
        if available_cash_input is not None and req_margin is not None
        else pv_missing("Capital remaining after margin", reason=GapReason.NO_EVIDENCE)
    )

    # Scheme capacity screen — what the declared 10%/90% split says the
    # stated Available Margin Capital could support on its own, independent
    # of whether the actual business-based structuring above succeeded.
    # Rendered whenever `capacity` calculated something, never only as a
    # fallback for a missing `structure` (CLAUDE.md §12's capacity-screen vs
    # viability-verdict distinction; see finance/capacity.py).
    calculated_capacity = (
        capacity if capacity is not None and capacity.status.value == "calculated" else None
    )
    capacity_project_cost_pv = (
        pv_config(
            "Feasible project cost (capacity screen)",
            format_inr(calculated_capacity.feasible_project_cost_inr),
            rationale=DECLARED_CONFIG_NOTE,
        )
        if calculated_capacity is not None
        else pv_missing("Feasible project cost (capacity screen)", reason=GapReason.NO_EVIDENCE)
    )
    capacity_required_margin_pv = (
        pv_config(
            "Required promoter margin (capacity screen)",
            format_inr(calculated_capacity.required_promoter_margin_inr),
            rationale=DECLARED_CONFIG_NOTE,
        )
        if calculated_capacity is not None
        else pv_missing("Required promoter margin (capacity screen)", reason=GapReason.NO_EVIDENCE)
    )
    capacity_indicated_loan_pv = (
        pv_config(
            "Indicated loan (capacity screen)",
            format_inr(calculated_capacity.indicated_loan_inr),
            rationale=DECLARED_CONFIG_NOTE,
        )
        if calculated_capacity is not None
        else pv_missing("Indicated loan (capacity screen)", reason=GapReason.NO_EVIDENCE)
    )
    capacity_note = (
        f"This is what {calculated_capacity.scheme_name}'s declared financing split says your "
        "stated Available Margin Capital could support — a capacity screen, not a "
        "recommendation or a viability verdict. Your business's actual requirement is shown "
        "above."
        if calculated_capacity is not None
        else ""
    )

    # loan terms
    plan_loan = plan.financing.loan if plan is not None else None
    declared_loan = None
    if structure is not None and structure.loan_terms is not None:
        declared_loan = structure.loan_terms
    elif capacity is not None and capacity.loan_terms is not None:
        declared_loan = capacity.loan_terms
    if plan_loan is not None:
        principal_pv, rate_pv, tenure_pv, moratorium_pv = _loan_terms_pvs(plan_loan, declared=False)
    else:
        principal_pv, rate_pv, tenure_pv, moratorium_pv = _loan_terms_pvs(
            declared_loan, declared=True
        )

    # EMI
    if fin is not None and fin.debt is not None and fin.debt.emi_inr is not None:
        emi_pv = pv_calc(
            "Monthly EMI",
            format_inr(fin.debt.emi_inr),
            inputs=("loan principal", "interest rate", "tenure"),
        )
    elif capacity is not None and capacity.monthly_emi_inr is not None:
        emi_pv = pv_calc(
            "Monthly EMI (on the indicated loan)",
            format_inr(capacity.monthly_emi_inr),
            inputs=("indicated loan", "declared rate", "declared tenure"),
        )
    else:
        emi_pv = pv_missing("Monthly EMI", reason=GapReason.INPUT_REQUIRED)

    # DSCR / cash flow / break-even
    dscr = fin.dscr if fin is not None else None
    avg_dscr_pv = (
        pv_calc(
            "Average annual DSCR",
            str(dscr.average_annual_dscr),
            inputs=("cash flow", "debt schedule"),
        )
        if dscr is not None and dscr.average_annual_dscr is not None
        else pv_missing("Average annual DSCR", reason=GapReason.INPUT_REQUIRED)
    )
    first_dscr_pv = (
        pv_calc(
            "First post-moratorium year DSCR",
            str(dscr.first_post_moratorium_year_dscr),
            inputs=("cash flow", "debt schedule"),
        )
        if dscr is not None and dscr.first_post_moratorium_year_dscr is not None
        else pv_missing("First post-moratorium year DSCR", reason=GapReason.INPUT_REQUIRED)
    )
    cash = fin.cash_flow if fin is not None else None
    min_cash_pv = (
        pv_calc(
            "Minimum cash balance",
            format_inr(cash.minimum_cash_balance_inr),
            inputs=("monthly cash flow",),
            note=f"in month {cash.minimum_cash_month}",
        )
        if cash is not None
        else pv_missing("Minimum cash balance", reason=GapReason.INPUT_REQUIRED)
    )
    cash_emi_pv = (
        pv_calc(
            "Cash on hand at EMI start",
            format_inr(cash.cash_at_emi_start_inr),
            inputs=("monthly cash flow",),
            note=f"EMI starts in month {cash.emi_start_month}",
        )
        if cash is not None and cash.cash_at_emi_start_inr is not None
        else pv_missing("Cash on hand at EMI start", reason=GapReason.INPUT_REQUIRED)
    )
    be = fin.break_even if fin is not None else None
    op_be_pv = (
        pv_calc(
            "Operating break-even month",
            str(be.operating_break_even_month),
            inputs=("revenue", "operating costs"),
        )
        if be is not None and be.operating_break_even_month is not None
        else pv_missing(
            "Operating break-even month",
            reason=GapReason.INPUT_REQUIRED,
            note=(be.undefined_reason if be is not None else ""),
        )
    )
    cash_be_pv = (
        pv_calc(
            "Cash break-even month", str(be.cash_break_even_month), inputs=("monthly cash flow",)
        )
        if be is not None and be.cash_break_even_month is not None
        else pv_missing("Cash break-even month", reason=GapReason.INPUT_REQUIRED)
    )

    stress: list[StressLine] = []
    for sr in fin.stress_results if fin is not None else []:
        # An optional scenario nobody supplied data for (e.g. "lean season"
        # with no seasonality given) is NOT the same kind of gap as a core
        # figure that's genuinely missing — use NOT_APPLICABLE so it never
        # pollutes the executive summary's evidence-gap list.
        missing_reason = GapReason.NO_EVIDENCE if sr.applied else GapReason.NOT_APPLICABLE
        # The NOT_APPLICABLE gap text already says "wasn't run for this
        # plan" — repeating `skipped_reason` after it would just restate
        # the same thing twice; it's worth showing only for a genuinely
        # missing (NO_EVIDENCE) figure.
        gap_note = sr.skipped_reason if missing_reason is GapReason.NO_EVIDENCE else ""
        stress.append(
            StressLine(
                name=humanise_token(sr.name),
                description=sr.description,
                applied=sr.applied,
                skipped_reason=sr.skipped_reason,
                minimum_cash=(
                    pv_calc(
                        "Minimum cash",
                        format_inr(sr.minimum_cash_balance_inr),
                        inputs=("stress cash flow",),
                    )
                    if sr.minimum_cash_balance_inr is not None
                    else pv_missing("Minimum cash", reason=missing_reason, note=gap_note)
                ),
                negative_cash_months=(
                    ", ".join(str(m) for m in sr.negative_cash_months)
                    if sr.negative_cash_months
                    else "none"
                ),
                average_dscr=(
                    pv_calc(
                        "Average DSCR",
                        str(sr.average_annual_dscr),
                        inputs=("stress cash flow", "debt schedule"),
                    )
                    if sr.average_annual_dscr is not None
                    else pv_missing("Average DSCR", reason=missing_reason, note=gap_note)
                ),
                outcome=humanise_token(sr.status.value) if sr.status is not None else "",
            )
        )

    assumption_share_pv = (
        pv_calc(
            "Share of plan resting on assumptions",
            format_ratio_pct(fin.assumptions.assumption_share),
            inputs=("finance assumption register",),
        )
        if fin is not None
        else pv_missing("Share of plan resting on assumptions", reason=GapReason.NO_EVIDENCE)
    )

    section_status = SectionStatus.RENDERED
    if incomplete:
        section_status = (
            SectionStatus.PARTIAL
            if (structure is not None or capacity is not None)
            else SectionStatus.EVIDENCE_GAP
        )

    return FinancialAssessmentSection(
        title="Financial assessment",
        status=section_status,
        gap_note=(
            "Financial assessment incomplete — see the missing inputs below." if incomplete else ""
        ),
        feasibility_status=status_pv,
        deciding_rung=rung,
        missing_core_drivers=missing_drivers,
        incomplete_note=FINANCIAL_INCOMPLETE_NOTE if incomplete else "",
        project_cost=project_cost_pv,
        promoter_contribution=promoter_contribution_pv,
        capital_remaining_after_margin=capital_remaining_pv,
        required_promoter_margin=req_margin_pv,
        indicated_loan=indicated_loan_pv,
        margin_shortfall=margin_shortfall_pv,
        financing_scheme=scheme_pv,
        capacity_feasible_project_cost=capacity_project_cost_pv,
        capacity_required_margin=capacity_required_margin_pv,
        capacity_indicated_loan=capacity_indicated_loan_pv,
        capacity_note=capacity_note,
        loan_principal=principal_pv,
        interest_rate=rate_pv,
        tenure=tenure_pv,
        moratorium=moratorium_pv,
        monthly_emi=emi_pv,
        average_annual_dscr=avg_dscr_pv,
        first_post_moratorium_dscr=first_dscr_pv,
        minimum_cash_balance=min_cash_pv,
        cash_at_emi_start=cash_emi_pv,
        operating_break_even_month=op_be_pv,
        cash_break_even_month=cash_be_pv,
        breaking_point=fin.breaking_point if fin is not None else "",
        stress_scenarios=tuple(stress),
        assumption_share=assumption_share_pv,
        finance_findings=tuple(f.message for f in fin.findings) if fin is not None else (),
    )


# --- scheme / knowledge -----------------------------------------------


def build_scheme_knowledge_section(
    session: ConversationSession, arts: ArtifactSet
) -> SchemeKnowledgeSection:
    kb = arts.knowledge
    kb_ids = _kb_ids(arts)

    if kb is None:
        return SchemeKnowledgeSection(
            title="Scheme, compliance, and knowledge evidence",
            status=SectionStatus.EVIDENCE_GAP,
            gap_note="The knowledge-retrieval step has not run for this session.",
            corpus_present=False,
            corpus_note="No knowledge evidence was gathered.",
            declared_config_note=DECLARED_CONFIG_NOTE,
        )

    acq = kb.acquisition
    if acq.corpus_present:
        corpus_note = (
            f"Corpus manifest {acq.corpus_manifest_version or 'n/a'} — "
            f"{acq.documents_loaded} document(s), {acq.chunks_loaded} chunk(s), "
            f"{acq.parameters_loaded} approved parameter row(s). This is a limited, "
            "incomplete corpus."
        )
    else:
        corpus_note = (
            "No knowledge corpus is loaded. Every scheme parameter below is "
            "'no evidence' — nothing has been assumed in its place."
        )

    q = kb.query
    query_summary = (
        f"Requested: {', '.join(n.value for n in q.names)}"
        + (f"; category {q.category.value}" if q.category else "")
        + (f"; state {q.state}" if q.state else "")
    )

    def _param_line(name: ParameterName) -> ParameterLine:
        declared_elsewhere = name in _DECLARED_CONFIG_BACKED_PARAMS
        gap_reason = GapReason.DECLARED_ELSEWHERE if declared_elsewhere else GapReason.NO_EVIDENCE
        res = kb.resolution_for(name)
        if res is None:
            return ParameterLine(
                name=name.value,
                value=pv_missing(name.value, reason=gap_reason),
                status="not_queried",
            )
        if res.status is ResolutionStatus.RESOLVED and res.chosen is not None:
            cid = kb_ids.get(name.value, "source")
            scheme = res.chosen.applicability.scheme
            # A resolved value can come from a scheme OTHER than the one this
            # deployment's Financial assessment section is actually built on
            # (`config/sih_scheme.py`) — e.g. a PMMY loan ceiling retrieved
            # only because no SIH-specific document exists in the corpus. Name
            # that scheme so the two are never mistaken for one figure.
            scheme_note = (
                f"This figure is specific to {scheme}, not the SIH-declared financing "
                "structure this report's own EMI/DSCR figures are calculated from."
                if scheme
                else ""
            )
            return ParameterLine(
                name=name.value,
                value=pv_sourced(
                    name.value,
                    str(res.chosen.value_token),
                    citation_id=cid,
                    raw=str(res.chosen.value),
                ),
                status=res.status.value,
                citation_id=cid,
                conditions=tuple(res.chosen.applicability.conditions),
                notes=(*res.notes, scheme_note) if scheme_note else tuple(res.notes),
            )
        return ParameterLine(
            name=name.value,
            value=pv_missing(
                name.value,
                reason=gap_reason,
                note="" if declared_elsewhere else f"resolution status: {res.status.value}",
            ),
            status=res.status.value,
            notes=tuple(res.notes),
        )

    resolved = tuple(_param_line(n) for n in _SCHEME_PARAMS)
    statutory = tuple(_param_line(n) for n in _STATUTORY_PARAMS)
    no_evidence = tuple(
        line.name
        for line in (*resolved, *statutory)
        if line.status != ResolutionStatus.RESOLVED.value
        and line.name not in {p.value for p in _DECLARED_CONFIG_BACKED_PARAMS}
    )

    passages: list[PassageLine] = []
    for i, p in enumerate(kb.passages, start=1):
        chunk = p.chunk
        text = chunk.text.strip().replace("\n", " ")
        passages.append(
            PassageLine(
                citation_id=f"P{i}",
                heading=" › ".join(chunk.heading_path) or chunk.document_id,
                excerpt=(text[:500] + "…") if len(text) > 500 else text,
                matched_terms=tuple(p.matched_terms),
                tier=p.tier.value,
            )
        )

    any_resolved = any(
        line.status == ResolutionStatus.RESOLVED.value for line in (*resolved, *statutory)
    )
    status = SectionStatus.RENDERED if (any_resolved or passages) else SectionStatus.PARTIAL

    return SchemeKnowledgeSection(
        title="Scheme, compliance, and knowledge evidence",
        status=status,
        gap_note=(
            ""
            if any_resolved or passages
            else "No verified scheme parameter or passage could be retrieved for this business "
            "and location."
        ),
        corpus_present=acq.corpus_present,
        corpus_note=corpus_note,
        query_summary=query_summary,
        resolved_parameters=resolved,
        statutory_fees=statutory,
        no_evidence_parameters=no_evidence,
        retrieved_passages=tuple(passages),
        declared_config_note=DECLARED_CONFIG_NOTE,
    )


# --- risks & SWOT ------------------------------------------------------


def build_risks_swot_section(session: ConversationSession, arts: ArtifactSet) -> RisksSwotSection:
    swot = arts.swot
    quads: dict[str, list[SwotLine]] = {
        "strength": [],
        "weakness": [],
        "opportunity": [],
        "threat": [],
    }
    quadrant_notes: list[str] = []
    data_caveats: list[str] = []
    swot_status = "no_evidence" if swot is None else swot.status.value

    if swot is not None:
        for item in swot.items:
            quads.setdefault(item.quadrant.value, []).append(
                SwotLine(
                    quadrant=item.quadrant.value,
                    text=item.text,
                    source_ref=item.source_ref,
                )
            )
        for q, note in swot.quadrant_notes.items():
            quadrant_notes.append(f"{q.value.capitalize()}: {note}")
        data_caveats.extend(swot.data_caveats)

    risks: list[str] = []
    mitigations: list[str] = []
    if arts.recommendation is not None:
        risks.extend(arts.recommendation.caveats)
    if arts.finance is not None:
        risks.extend(f.message for f in arts.finance.findings)
        if arts.finance.breaking_point:
            mitigations.append(
                f"Address the named breaking point before drawdown: {arts.finance.breaking_point}"
            )
    if arts.market is not None:
        risks.extend(c.message for c in arts.market.concerns)
        data_caveats.extend(c.message for c in arts.market.data_caveats)
    if arts.finance is not None and arts.finance.missing_core_drivers:
        mitigations.append(
            "Supply the missing financial drivers so a real DSCR / cash-flow verdict can be run: "
            + ", ".join(arts.finance.missing_core_drivers)
        )

    has_content = any(quads.values()) or risks
    return RisksSwotSection(
        title="Risks and SWOT",
        status=SectionStatus.RENDERED if has_content else SectionStatus.EVIDENCE_GAP,
        gap_note="" if has_content else "No structured risks or SWOT items are available yet.",
        swot_status=swot_status,
        strengths=tuple(quads["strength"]),
        weaknesses=tuple(quads["weakness"]),
        opportunities=tuple(quads["opportunity"]),
        threats=tuple(quads["threat"]),
        quadrant_notes=tuple(quadrant_notes),
        structured_risks=tuple(dict.fromkeys(risks)),
        mitigations=tuple(dict.fromkeys(mitigations)),
        data_caveats=tuple(dict.fromkeys(data_caveats)),
    )


__all__ = [
    "build_financial_section",
    "build_market_section",
    "build_opportunity_section",
    "build_profile_section",
    "build_project_plan_section",
    "build_risks_swot_section",
    "build_scheme_knowledge_section",
]
