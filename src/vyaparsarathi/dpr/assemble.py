"""Assemble a `DprDocument` from a finished conversation session (CLAUDE.md
§23, §25 Phase 8). PURE — no clock (``generated_at`` is injected), no network,
no LLM, and no engine is re-run: every figure is lifted from a stored
artifact, a slot, `conversation/bundle.py`'s projection, or an explicit gap.
"""

from __future__ import annotations

from datetime import datetime
from importlib.metadata import PackageNotFoundError, version

from vyaparsarathi.conversation.bundle import EvidenceBundle, FactOrigin, build_bundle
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName
from vyaparsarathi.dpr.artifacts import ArtifactSet, load_artifacts
from vyaparsarathi.dpr.citations import build_citations
from vyaparsarathi.dpr.disclaimers import (
    CONFIDENCE_SEPARATION_NOTE,
    DISCLAIMER,
    GLOSSARY,
    NO_LLM_NOTE,
    REPORT_KIND,
)
from vyaparsarathi.dpr.fingerprint import (
    input_fingerprint,
    missing_artifacts,
    present_artifacts,
    report_id_for,
)
from vyaparsarathi.dpr.format import humanise_token
from vyaparsarathi.dpr.provenance import (
    GapReason,
    ProvenancedValue,
    ValueOrigin,
    is_gap,
    pv_calc,
    pv_missing,
)
from vyaparsarathi.dpr.report_models import (
    SCHEMA_VERSION,
    AnnexuresSection,
    AssumptionsSection,
    CalcProvenanceLine,
    CoverPage,
    DprDocument,
    ExecutiveSummary,
    FinancialAssessmentSection,
    GenerationMetadata,
    GlossaryEntry,
    LabeledItem,
    ReportSection,
    SchemeKnowledgeSection,
    SectionStatus,
    SlotHistoryLine,
)
from vyaparsarathi.dpr.sections import (
    build_financial_section,
    build_market_section,
    build_opportunity_section,
    build_profile_section,
    build_project_plan_section,
    build_risks_swot_section,
    build_scheme_knowledge_section,
)
from vyaparsarathi.dpr.slots import slot_value


def _renderer_tag() -> str:
    try:
        return f"reportlab-{version('reportlab')}"
    except PackageNotFoundError:  # pragma: no cover - reportlab is a hard dependency
        return "reportlab-unknown"


_RENDERER = _renderer_tag()


def assemble_report(session: ConversationSession, *, generated_at: datetime) -> DprDocument:
    """`ConversationSession` + an injected timestamp -> a fully structured
    `DprDocument`. Deterministic: two calls with the same session and
    different `generated_at` differ only on the cover date and
    `metadata.generated_at`; `report_id` and every other field are identical.
    """
    arts = load_artifacts(session)
    bundle = build_bundle(session)
    citations = build_citations(arts)
    fingerprint = input_fingerprint(session)
    report_id = report_id_for(fingerprint)

    profile = build_profile_section(session, arts)
    market = build_market_section(session, arts)
    opportunity = build_opportunity_section(session, arts)
    project_plan = build_project_plan_section(session, arts)
    financial = build_financial_section(session, arts)
    scheme_knowledge = build_scheme_knowledge_section(session, arts)
    risks_swot = build_risks_swot_section(session, arts)

    body_sections: tuple[ReportSection, ...] = (
        profile,
        market,
        opportunity,
        project_plan,
        financial,
        scheme_knowledge,
        risks_swot,
    )
    evidence_gaps = _collect_gaps(body_sections, financial, scheme_knowledge)

    cover = CoverPage(
        title="Cover page",
        project_title="VyaparSarathi — Detailed Project Report",
        proposed_business=slot_value(
            session,
            SlotName.PROPOSED_BUSINESS_TEXT,
            label="Proposed business",
            fmt=lambda v: str(v),
        ),
        location=slot_value(
            session, SlotName.LOCATION_TEXT, label="Location", fmt=lambda v: str(v)
        ),
        report_id=report_id,
        generated_on=generated_at.strftime("%d %B %Y, %H:%M UTC"),
        session_id=session.session_id,
        report_kind=REPORT_KIND,
        disclaimer=DISCLAIMER,
    )

    executive_summary = _build_executive_summary(arts, evidence_gaps, financial)
    assumptions = _build_assumptions_section(
        body_sections + (executive_summary,), arts, evidence_gaps
    )
    annexures = _build_annexures_section(session, bundle, citations)

    metadata = GenerationMetadata(
        schema_version=SCHEMA_VERSION,
        renderer=_RENDERER,
        report_id=report_id,
        input_fingerprint=fingerprint,
        generated_at=generated_at,
        session_id=session.session_id,
        session_turn_count=session.turn_index,
        assembled_from_artifacts=present_artifacts(session),
        missing_artifacts=missing_artifacts(session),
        session_warnings=tuple(session.session_warnings),
        llm_used_in_report=False,
    )

    return DprDocument(
        schema_version=SCHEMA_VERSION,
        report_id=report_id,
        input_fingerprint=fingerprint,
        generated_at=generated_at,
        session_id=session.session_id,
        disclaimer=DISCLAIMER,
        cover=cover,
        executive_summary=executive_summary,
        profile=profile,
        market=market,
        opportunity=opportunity,
        project_plan=project_plan,
        financial=financial,
        scheme_knowledge=scheme_knowledge,
        risks_swot=risks_swot,
        assumptions=assumptions,
        annexures=annexures,
        citations=citations,
        evidence_gaps=evidence_gaps,
        metadata=metadata,
    )


# --- gap roll-up -------------------------------------------------------


def _walk_pvs(model: object) -> list[ProvenancedValue]:
    """Every `ProvenancedValue` reachable inside a pydantic model tree."""
    from pydantic import BaseModel

    found: list[ProvenancedValue] = []

    def _visit(obj: object) -> None:
        if isinstance(obj, ProvenancedValue):
            found.append(obj)
            return
        if isinstance(obj, BaseModel):
            for value in obj.__dict__.values():
                _visit(value)
        elif isinstance(obj, dict):
            for value in obj.values():
                _visit(value)
        elif isinstance(obj, list | tuple | set | frozenset):
            for value in obj:
                _visit(value)

    _visit(model)
    return found


def _collect_gaps(
    body_sections: tuple[ReportSection, ...],
    financial: FinancialAssessmentSection,
    scheme_knowledge: SchemeKnowledgeSection,
) -> tuple[str, ...]:
    gaps: list[str] = []
    for section in body_sections:
        if section.status is SectionStatus.EVIDENCE_GAP and section.gap_note:
            gaps.append(f"{section.title}: {section.gap_note}")
        for pv in _walk_pvs(section):
            if not is_gap(pv):
                continue
            if pv.gap_reason in (GapReason.NOT_APPLICABLE, GapReason.DECLARED_ELSEWHERE):
                # An optional sub-field nobody asked for (e.g. the "lean
                # season" stress scenario with no seasonality supplied), or
                # a scheme parameter already answered elsewhere via a
                # declared configuration (e.g. the SIH interest rate used
                # to compute the EMI) — both still render in place, in
                # context, but neither is a core fact missing from this
                # report, so neither may crowd a real gap out of the
                # executive summary's short list.
                continue
            suffix = f" — {pv.note}" if pv.note else ""
            gaps.append(f"{section.title}: {pv.label} — {pv.display}{suffix}")
    for driver in financial.missing_core_drivers:
        gaps.append(f"Financial assessment: missing core driver — {driver}")
    for name in scheme_knowledge.no_evidence_parameters:
        gaps.append(f"Scheme evidence: no verified value for {name}")
    return tuple(dict.fromkeys(gaps))


# --- executive summary ----------------------------------------------


_NEXT_ACTION_BASE = (
    "Take this report to a bank business-correspondent agent or the block office to "
    "begin scheme appraisal — the bank's own credit assessment remains authoritative."
)

# Plain-language translations of the two headline verdicts, for the summary
# card at the top of the executive summary. Duplicated (rather than
# imported) from `market/opportunity.py`'s similar mapping deliberately —
# `market/` decides, `dpr/` only presents; the two must not depend on each
# other's private wording. [tunable — wording only]
_VERDICT_PLAIN: dict[str, str] = {
    "proceed": "This looks like a workable plan — the evidence and the numbers line up.",
    "proceed_with_caution": (
        "This can work, but pay close attention to the risks in this report before committing."
    ),
    "adjust": "This plan needs some changes before it's solid — see the risks below.",
    "pivot": "Consider a different business — the local evidence points to a stronger option.",
    "insufficient_evidence": (
        "We don't yet have enough evidence to make a clear call — see what's missing below."
    ),
}

_FINANCE_STATUS_PLAIN: dict[str, str] = {
    "feasible": "on the numbers you gave us, this plan can cover its loan and costs comfortably.",
    "feasible_with_stretch": (
        "this plan can cover its loan and costs, but with little room for a bad month."
    ),
    "financing_gap": (
        "the loan and margin this plan needs don't fully add up yet — see the financial section."
    ),
    "cash_flow_stress": "cash flow gets tight at some point in this plan — see the financial section.",
    "unserviceable": "on these numbers, this plan cannot reliably cover its loan repayments.",
    "insufficient_financial_evidence": (
        "we need a few more numbers from you before affordability can be judged."
    ),
}

_MARKET_LABEL_PLAIN_SUMMARY: dict[str, str] = {
    "underserved": "few or no similar businesses currently serve this area.",
    "mixed": "there is some demand, but it's shared with existing businesses.",
    "served": "this kind of business already serves the area reasonably well.",
    "crowded": "there are already many similar businesses here for the market's size.",
    "thin_market": "there may not be enough people here to reliably support it.",
    "insufficient_evidence": "there isn't enough local data yet to judge demand here.",
}


def _build_plain_summary(arts: ArtifactSet) -> tuple[str, ...]:
    lines: list[str] = []
    rec = arts.recommendation
    if rec is not None:
        lines.append(_VERDICT_PLAIN.get(rec.verdict.value, rec.reason))

    fin = arts.finance
    if fin is not None:
        detail = _FINANCE_STATUS_PLAIN.get(fin.status.value)
        if detail is not None:
            lines.append(f"Can you afford it? {detail.capitalize()}")

    market = arts.market
    if market is not None:
        detail = _MARKET_LABEL_PLAIN_SUMMARY.get(market.label.value)
        if detail is not None:
            lines.append(f"Is the market good? {detail.capitalize()}")

    lines.append(f"What next? {_NEXT_ACTION_BASE}")
    return tuple(lines)


def _build_executive_summary(
    arts: ArtifactSet,
    evidence_gaps: tuple[str, ...],
    financial: FinancialAssessmentSection,
) -> ExecutiveSummary:
    rec = arts.recommendation
    if rec is not None:
        recommendation = pv_calc(
            "Recommendation",
            humanise_token(rec.verdict.value),
            inputs=("opportunity.stance", "finance.status"),
        )
        verdict_reason = rec.reason
    else:
        recommendation = pv_missing("Recommendation", reason=GapReason.NO_EVIDENCE)
        verdict_reason = "The recommendation combiner has not run for this session."

    opp = arts.opportunity
    market_conf = (
        pv_calc(
            "Market-data confidence",
            f"{opp.market_data_confidence:.0%}",
            inputs=("demand_signals.coverage",),
        )
        if opp is not None
        else pv_missing("Market-data confidence", reason=GapReason.NO_EVIDENCE)
    )
    fin = arts.finance
    fin_status = (
        pv_calc(
            "Financial status",
            humanise_token(fin.status.value),
            inputs=(f"finance.rung={fin.rung.value}",),
        )
        if fin is not None
        else pv_missing("Financial status", reason=GapReason.NO_EVIDENCE)
    )

    strengths: list[str] = []
    risks: list[str] = []
    if arts.swot is not None:
        for item in arts.swot.items:
            if item.quadrant.value == "strength":
                strengths.append(item.text)
            elif item.quadrant.value in ("weakness", "threat"):
                risks.append(item.text)
    if not strengths and arts.market is not None:
        strengths = [f.message for f in arts.market.positive_signals]
    if arts.recommendation is not None:
        risks.extend(arts.recommendation.caveats)

    next_actions: list[str] = []
    md = financial.missing_core_drivers
    if md:
        next_actions.append(
            "Provide the missing financial inputs so a full DSCR / cash-flow / stress "
            "verdict can be produced: " + ", ".join(md) + "."
        )
    if rec is not None and rec.verdict.value == "pivot" and rec.recommended_pivot is not None:
        next_actions.append(
            f"Seriously consider the alternative that scored materially higher: "
            f"{rec.recommended_pivot.value.replace('_', ' ')}."
        )
    if rec is not None and rec.verdict.value == "adjust":
        next_actions.append(
            "Adjust the plan (capital, scale, or location) before applying — see the risks section."
        )
    next_actions.append(_NEXT_ACTION_BASE)

    status = SectionStatus.RENDERED
    if rec is None:
        status = SectionStatus.PARTIAL

    return ExecutiveSummary(
        title="Executive summary",
        status=status,
        recommendation=recommendation,
        verdict_reason=verdict_reason,
        market_data_confidence=market_conf,
        financial_status=fin_status,
        strengths=tuple(dict.fromkeys(strengths))[:6],
        risks=tuple(dict.fromkeys(risks))[:6],
        next_actions=tuple(dict.fromkeys(next_actions)),
        evidence_gaps=evidence_gaps[:12],
        plain_summary=_build_plain_summary(arts),
    )


# --- assumptions / limitations -------------------------------------


_ORIGIN_BUCKET = {
    ValueOrigin.USER_PROVIDED: "user_inputs",
    ValueOrigin.ASSUMED: "assumptions",
    ValueOrigin.CALCULATED: "calculated_results",
    ValueOrigin.SOURCED: "source_facts",
    ValueOrigin.DECLARED_CONFIG: "declared_configuration",
}


def _build_assumptions_section(
    sections: tuple[ReportSection, ...],
    arts: ArtifactSet,
    evidence_gaps: tuple[str, ...],
) -> AssumptionsSection:
    buckets: dict[str, list[LabeledItem]] = {
        "user_inputs": [],
        "assumptions": [],
        "calculated_results": [],
        "source_facts": [],
        "declared_configuration": [],
    }
    seen: set[tuple[str, str]] = set()
    for section in sections:
        for pv in _walk_pvs(section):
            if is_gap(pv):
                continue
            bucket = _ORIGIN_BUCKET.get(pv.origin)
            if bucket is None:
                continue
            key = (pv.label, pv.display)
            if key in seen:
                continue
            seen.add(key)
            detail = pv.display
            if pv.origin is ValueOrigin.ASSUMED and pv.rationale:
                detail += f" — {pv.rationale}"
            if pv.origin is ValueOrigin.CALCULATED and pv.inputs:
                detail += f" (from {', '.join(pv.inputs)})"
            if pv.origin is ValueOrigin.SOURCED and pv.citation_id:
                detail += f" [{pv.citation_id}]"
            buckets[bucket].append(
                LabeledItem(label=pv.label, detail=detail, origin=pv.origin.value)
            )

    if arts.finance is not None:
        for fi in arts.finance.assumptions.inputs:
            bucket = _ORIGIN_BUCKET.get(ValueOrigin(fi.kind.value))
            if bucket is None:
                continue
            key = (f"finance:{fi.label}", str(fi.value))
            if key in seen:
                continue
            seen.add(key)
            detail = f"{fi.value} {fi.unit.value}"
            if fi.kind.value == "assumed" and fi.rationale:
                detail += f" — {fi.rationale}"
            buckets[bucket].append(
                LabeledItem(label=f"finance: {fi.label}", detail=detail, origin=fi.kind.value)
            )

    confidence_notes = [NO_LLM_NOTE, CONFIDENCE_SEPARATION_NOTE]
    if arts.opportunity is not None:
        confidence_notes.append(arts.opportunity.market_data_confidence_note)
    if arts.finance is not None:
        confidence_notes.append(arts.finance.assumptions.note)

    return AssumptionsSection(
        title="Assumptions, limitations, and evidence confidence",
        status=SectionStatus.RENDERED,
        user_inputs=tuple(buckets["user_inputs"]),
        assumptions=tuple(buckets["assumptions"]),
        calculated_results=tuple(buckets["calculated_results"]),
        source_facts=tuple(buckets["source_facts"]),
        declared_configuration=tuple(buckets["declared_configuration"]),
        unavailable_evidence=evidence_gaps,
        confidence_notes=tuple(dict.fromkeys(confidence_notes)),
    )


# --- annexures ------------------------------------------------------


def _build_annexures_section(
    session: ConversationSession,
    bundle: EvidenceBundle,
    citations: dict,
) -> AnnexuresSection:
    sources = tuple(sorted(citations.values(), key=lambda c: c.citation_id))

    calc_lines: list[CalcProvenanceLine] = []
    for fact in bundle.facts:
        if fact.origin is FactOrigin.CALCULATION:
            engine = fact.key.split(".", 1)[0]
            engine_name = {
                "opportunity": "market.opportunity",
                "market": "market.assessment",
                "finance": "finance.assessment",
                "recommend": "conversation.recommendation",
                "swot": "conversation.swot",
                "structure": "finance.structuring",
            }.get(engine, engine)
            calc_lines.append(
                CalcProvenanceLine(
                    result=fact.label,
                    display=pv_calc(fact.label, fact.render, inputs=(fact.key,)),
                    inputs=(fact.key,),
                    engine=engine_name,
                )
            )

    slot_history: list[SlotHistoryLine] = []
    for name in sorted(SlotName, key=lambda n: n.value):
        slot = session.slots.get(name)
        if slot is None or slot.state.value == "missing":
            continue
        entries = [*slot.history, slot.current]
        for i, sv in enumerate(entries):
            slot_history.append(
                SlotHistoryLine(
                    slot=name.value,
                    state=sv.state.value,
                    value="" if sv.value is None else str(sv.value),
                    raw_text=sv.raw_text or "",
                    set_on_turn=sv.set_on_turn,
                    superseded=i < len(entries) - 1,
                )
            )

    glossary = tuple(GlossaryEntry(term=t, definition=d) for t, d in GLOSSARY)

    return AnnexuresSection(
        title="Annexures",
        status=SectionStatus.RENDERED,
        sources=sources,
        calculation_provenance=tuple(calc_lines),
        slot_history=tuple(slot_history),
        glossary=glossary,
    )


__all__ = ["assemble_report"]
