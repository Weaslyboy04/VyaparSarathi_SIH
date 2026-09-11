"""The structured DPR domain model (CLAUDE.md §4 "DPR generator", §23, §25
Phase 8). PURE — data only, no assembly logic and no rendering.

`DprDocument` is what `dpr/assemble.py` produces and what `dpr/render_pdf.py`
and `dpr/render_json.py` consume. Every leaf figure is a
`provenance.ProvenancedValue`; every section carries a `SectionStatus` so a
missing section is an explicit, worded gap rather than a blank.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.dpr.provenance import ProvenancedValue

SCHEMA_VERSION: Literal["dpr/v1"] = "dpr/v1"


class SectionStatus(StrEnum):
    RENDERED = "rendered"  # the section has substantive content
    PARTIAL = "partial"  # some content, but a named part of it is missing
    EVIDENCE_GAP = "evidence_gap"  # nothing to show — carries a worded gap_note


class Citation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    text: str  # a self-contained one-line reference
    document_title: str = ""
    publisher: str = ""
    tier: str = ""  # SourceTier value, verbatim
    locator: str = ""  # section / page pointer
    reference_date: str = ""  # the date the rule itself describes
    retrieved_at: str = ""  # when the operator downloaded the document
    url: str = ""


class LabeledItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    detail: str
    origin: str = ""  # ValueOrigin value, when this item is a ProvenancedValue projection


class FactorBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    available: bool
    contribution: ProvenancedValue
    weight_pct: str
    reason: str


class AlternativeOption(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    business: str
    score: ProvenancedValue
    market_label: str
    rank: int | None = None
    reasons: tuple[str, ...] = ()
    capital_fit: str = ""


class StressLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    applied: bool
    skipped_reason: str = ""
    minimum_cash: ProvenancedValue
    negative_cash_months: str
    average_dscr: ProvenancedValue
    outcome: str = ""


class ParameterLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    value: ProvenancedValue  # SOURCED when resolved; NOT_AVAILABLE otherwise
    status: str  # ResolutionStatus value
    citation_id: str | None = None
    conditions: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


class PassageLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_id: str
    heading: str
    excerpt: str  # verbatim from the retrieved chunk; never paraphrased
    matched_terms: tuple[str, ...] = ()
    tier: str = ""


class SwotLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    quadrant: str
    text: str
    source_ref: str  # dotted path into the artifact it was drawn from


class CalcProvenanceLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    result: str
    display: ProvenancedValue
    inputs: tuple[str, ...]
    engine: str  # e.g. "finance.assessment", "market.opportunity"


class SlotHistoryLine(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: str
    state: str
    value: str
    raw_text: str = ""
    set_on_turn: int = 0
    superseded: bool = False


class GlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    term: str
    definition: str


# --- sections -------------------------------------------------------------


class ReportSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    status: SectionStatus = SectionStatus.RENDERED
    gap_note: str = ""


class CoverPage(ReportSection):
    project_title: str
    proposed_business: ProvenancedValue
    location: ProvenancedValue
    report_id: str
    generated_on: str
    session_id: str
    report_kind: str
    disclaimer: str


class ExecutiveSummary(ReportSection):
    recommendation: ProvenancedValue
    verdict_reason: str = ""
    market_data_confidence: ProvenancedValue
    financial_status: ProvenancedValue
    strengths: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    evidence_gaps: tuple[str, ...] = ()


class EntrepreneurProfileSection(ReportSection):
    available_margin_capital: ProvenancedValue
    owned_assets: tuple[LabeledItem, ...] = ()
    experience_categories: tuple[str, ...] = ()
    years_experience: ProvenancedValue
    proposed_enterprise: ProvenancedValue
    resolved_category: ProvenancedValue
    constraints: tuple[str, ...] = ()
    unverified_note: str


class MarketAssessmentSection(ReportSection):
    resolved_location: ProvenancedValue
    admin_hierarchy: tuple[LabeledItem, ...] = ()
    source_coverage: tuple[LabeledItem, ...] = ()
    data_confidence: ProvenancedValue
    direct_competitors: ProvenancedValue
    adjacent_competitors: ProvenancedValue
    nearest_competitor: ProvenancedValue
    competition_signal: ProvenancedValue
    demand_signals: tuple[LabeledItem, ...] = ()
    market_label: ProvenancedValue
    label_reason: str = ""
    caveats: tuple[str, ...] = ()
    completeness_note: str


class OpportunitySection(ReportSection):
    proposed_score: ProvenancedValue
    stance: ProvenancedValue
    stance_reason: str = ""
    factors: tuple[FactorBreakdown, ...] = ()
    alternatives: tuple[AlternativeOption, ...] = ()
    recommended_pivot: ProvenancedValue
    market_data_confidence: ProvenancedValue
    caveats: tuple[str, ...] = ()


class ProjectPlanSection(ReportSection):
    category: ProvenancedValue
    catchment_radius: ProvenancedValue
    stated_project_cost: ProvenancedValue
    stated_monthly_revenue: ProvenancedValue
    stated_cogs_pct: ProvenancedValue
    stated_fixed_opex: ProvenancedValue
    note: str


class FinancialAssessmentSection(ReportSection):
    feasibility_status: ProvenancedValue
    deciding_rung: str = ""
    missing_core_drivers: tuple[str, ...] = ()
    incomplete_note: str = ""

    project_cost: ProvenancedValue
    promoter_contribution: ProvenancedValue
    required_promoter_margin: ProvenancedValue
    indicated_loan: ProvenancedValue
    margin_shortfall: ProvenancedValue
    financing_scheme: ProvenancedValue

    loan_principal: ProvenancedValue
    interest_rate: ProvenancedValue
    tenure: ProvenancedValue
    moratorium: ProvenancedValue
    monthly_emi: ProvenancedValue

    average_annual_dscr: ProvenancedValue
    first_post_moratorium_dscr: ProvenancedValue
    minimum_cash_balance: ProvenancedValue
    cash_at_emi_start: ProvenancedValue
    operating_break_even_month: ProvenancedValue
    cash_break_even_month: ProvenancedValue
    breaking_point: str = ""
    stress_scenarios: tuple[StressLine, ...] = ()
    assumption_share: ProvenancedValue
    finance_findings: tuple[str, ...] = ()


class SchemeKnowledgeSection(ReportSection):
    corpus_present: bool
    corpus_note: str
    query_summary: str = ""
    resolved_parameters: tuple[ParameterLine, ...] = ()
    statutory_fees: tuple[ParameterLine, ...] = ()
    no_evidence_parameters: tuple[str, ...] = ()
    retrieved_passages: tuple[PassageLine, ...] = ()
    declared_config_note: str


class RisksSwotSection(ReportSection):
    swot_status: str
    strengths: tuple[SwotLine, ...] = ()
    weaknesses: tuple[SwotLine, ...] = ()
    opportunities: tuple[SwotLine, ...] = ()
    threats: tuple[SwotLine, ...] = ()
    quadrant_notes: tuple[str, ...] = ()
    structured_risks: tuple[str, ...] = ()
    mitigations: tuple[str, ...] = ()
    data_caveats: tuple[str, ...] = ()


class AssumptionsSection(ReportSection):
    user_inputs: tuple[LabeledItem, ...] = ()
    assumptions: tuple[LabeledItem, ...] = ()
    calculated_results: tuple[LabeledItem, ...] = ()
    source_facts: tuple[LabeledItem, ...] = ()
    declared_configuration: tuple[LabeledItem, ...] = ()
    unavailable_evidence: tuple[str, ...] = ()
    confidence_notes: tuple[str, ...] = ()


class AnnexuresSection(ReportSection):
    sources: tuple[Citation, ...] = ()
    calculation_provenance: tuple[CalcProvenanceLine, ...] = ()
    slot_history: tuple[SlotHistoryLine, ...] = ()
    glossary: tuple[GlossaryEntry, ...] = ()


class GenerationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["dpr/v1"] = SCHEMA_VERSION
    renderer: str
    report_id: str
    input_fingerprint: str
    generated_at: datetime
    session_id: str
    session_turn_count: int
    assembled_from_artifacts: tuple[str, ...] = ()
    missing_artifacts: tuple[str, ...] = ()
    session_warnings: tuple[str, ...] = ()
    llm_used_in_report: bool = False


class DprDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["dpr/v1"] = SCHEMA_VERSION
    report_id: str
    input_fingerprint: str
    generated_at: datetime
    session_id: str
    disclaimer: str

    cover: CoverPage
    executive_summary: ExecutiveSummary
    profile: EntrepreneurProfileSection
    market: MarketAssessmentSection
    opportunity: OpportunitySection
    project_plan: ProjectPlanSection
    financial: FinancialAssessmentSection
    scheme_knowledge: SchemeKnowledgeSection
    risks_swot: RisksSwotSection
    assumptions: AssumptionsSection
    annexures: AnnexuresSection

    citations: dict[str, Citation] = Field(default_factory=dict)
    evidence_gaps: tuple[str, ...] = ()
    metadata: GenerationMetadata

    def ordered_sections(self) -> tuple[ReportSection, ...]:
        return (
            self.executive_summary,
            self.profile,
            self.market,
            self.opportunity,
            self.project_plan,
            self.financial,
            self.scheme_knowledge,
            self.risks_swot,
            self.assumptions,
            self.annexures,
        )


__all__ = [
    "SCHEMA_VERSION",
    "AlternativeOption",
    "AnnexuresSection",
    "AssumptionsSection",
    "CalcProvenanceLine",
    "Citation",
    "CoverPage",
    "DprDocument",
    "EntrepreneurProfileSection",
    "ExecutiveSummary",
    "FactorBreakdown",
    "FinancialAssessmentSection",
    "GenerationMetadata",
    "GlossaryEntry",
    "LabeledItem",
    "MarketAssessmentSection",
    "OpportunitySection",
    "ParameterLine",
    "PassageLine",
    "ProjectPlanSection",
    "ReportSection",
    "RisksSwotSection",
    "SchemeKnowledgeSection",
    "SectionStatus",
    "SlotHistoryLine",
    "StressLine",
    "SwotLine",
]
