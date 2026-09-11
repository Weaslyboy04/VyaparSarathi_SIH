"""The only module in this repository that executes a `StepId` (CLAUDE.md
§4, §25 Phase 6). Everything under `conversation/` only ever *names* a step;
this module is where that name becomes a real call into Phase 1-5 —
`market/`, `finance/`, `knowledge/`, `discovery/`, `sources/`, `geocoding/`,
`database/` — with every signature used **verbatim**, including the
`config=`/`cfg=` keyword inconsistency the approved plan says to preserve,
not unify (see `tests/test_conversation_workflow.py`'s `inspect.signature`
conformance check).

`RunContext` is the acquisition-seam convention already used throughout
Phase 1-5: impure handles (geocoder, Overpass client, census, corpus,
retriever, repository, clock) are explicit, caller-owned arguments — built
once by `app/runtime.py` and passed in, never constructed here.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import TypeVar

from pydantic import BaseModel

from vyaparsarathi.config import Settings
from vyaparsarathi.config.sih_scheme import DEFAULT_SIH_SCHEME_TABLE
from vyaparsarathi.conversation.artifacts import compute_fingerprints, record_artifact
from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
    ConversationMode,
)
from vyaparsarathi.conversation.deltas import (
    set_resolved_category,
    set_slot_ambiguous,
    set_slot_assumed,
)
from vyaparsarathi.conversation.plan_builder import (
    build_plan_input,
    build_profile,
    loan_principal_input,
)
from vyaparsarathi.conversation.recommendation import combine
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    SlotState,
    StepId,
)
from vyaparsarathi.conversation.step_models import STEP_RESULT_MODEL
from vyaparsarathi.conversation.swot import build_swot
from vyaparsarathi.conversation.swot_config import DEFAULT_SWOT_CONFIG
from vyaparsarathi.database.repository import BusinessRepository
from vyaparsarathi.discovery.demand_acquisition import acquire_demand_evidence
from vyaparsarathi.discovery.knowledge_acquisition import acquire_finance_knowledge
from vyaparsarathi.discovery.opportunity_acquisition import acquire_opportunity_evidence
from vyaparsarathi.discovery.service import DiscoveryService
from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.finance.capacity import compute_scheme_capacity
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG
from vyaparsarathi.finance.fit import to_financial_fit
from vyaparsarathi.finance.structuring import apply_structure, structure_financing
from vyaparsarathi.finance.structuring_models import SchemeStructureResult
from vyaparsarathi.knowledge.base import CorpusStore, Retriever
from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG
from vyaparsarathi.knowledge.plan_binding import bind_sourced_inputs, build_loan_terms
from vyaparsarathi.market.assessment import assess_market
from vyaparsarathi.market.assessment_config import DEFAULT_ASSESSMENT_CONFIG
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.classifier import analyze_competitors
from vyaparsarathi.market.demand import compute_demand_signals
from vyaparsarathi.market.demand_config import DEFAULT_DEMAND_CONFIG
from vyaparsarathi.market.demand_models import DemandSignalsResult
from vyaparsarathi.market.metrics import compute_competition_metrics
from vyaparsarathi.market.metrics_config import DEFAULT_METRICS_CONFIG
from vyaparsarathi.market.metrics_models import CompetitionMetricsResult
from vyaparsarathi.market.models import CompetitorAnalysisResult, ProposedBusiness
from vyaparsarathi.market.opportunity import score_opportunities
from vyaparsarathi.market.opportunity_config import DEFAULT_OPPORTUNITY_CONFIG
from vyaparsarathi.market.opportunity_models import FinancialFitInput, OpportunityAnalysisResult
from vyaparsarathi.market.proposed import resolve_proposed_business
from vyaparsarathi.models.demand import DemandEvidence
from vyaparsarathi.models.finance import FinancialPlanInput, MoratoriumTreatment
from vyaparsarathi.models.opportunity import OpportunityEvidence
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence, ParameterName, ParameterQuery
from vyaparsarathi.models.results import DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.sources.osm.client import OverpassClient
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)


@dataclass
class RunContext:
    """The impure handles every `StepRunner` may need. Built once per process
    (or per demo run) by `app/runtime.py::AdvisoryRuntime`; never constructed
    inside a runner."""

    settings: Settings
    discovery_service: DiscoveryService
    overpass_client: OverpassClient
    census: CensusVillageSource
    corpus: CorpusStore
    repository: BusinessRepository
    retriever: Retriever | None = None
    clock: Clock = field(default=utcnow)
    conv_cfg: ConversationConfig = field(default=DEFAULT_CONVERSATION_CONFIG)
    # DEVELOPER (default) keeps every pre-Phase-B test/demo's incremental
    # behaviour unchanged; a real channel sets NORMAL. Threaded to
    # `conversation/planner.py::decide` by `llm/orchestrator.py::run_turn`.
    mode: ConversationMode = ConversationMode.DEVELOPER


def _candidate_categories() -> list[BusinessCategory]:
    return [BusinessCategory(c) for c in DEFAULT_OPPORTUNITY_CONFIG.candidate_categories]


_M = TypeVar("_M", bound=BaseModel)


def _load(session: ConversationSession, step: StepId, model_cls: type[_M]) -> _M:
    artifact = session.artifacts.get(step)
    if artifact is None:
        raise KeyError(f"{step.value} has no artifact; the workflow DAG should have prevented this")
    return model_cls.model_validate(artifact.payload)


def _try_load(session: ConversationSession, step: StepId, model_cls: type[_M]) -> _M | None:
    artifact = session.artifacts.get(step)
    return model_cls.model_validate(artifact.payload) if artifact is not None else None


# --- Phase 1: resolve the proposed business + discover -------------------


def _run_resolve_proposed(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    text = session.slot(SlotName.PROPOSED_BUSINESS_TEXT).value
    raw_text = str(text) if text is not None else ""
    proposed = resolve_proposed_business(raw_text)
    session = set_resolved_category(
        session,
        category=proposed.category if proposed.resolved else None,
        resolved=proposed.resolved,
        subtypes=tuple(proposed.subtypes),
    )
    if not proposed.resolved and proposed.candidate_categories:
        # Weak-but-plausible category guesses exist — ask the entrepreneur
        # to pick one (mirrors `_run_discover`'s LOCATION_AMBIGUOUS handling
        # below) instead of silently falling through to a blank report.
        options = tuple(proposed.candidate_categories) + (
            "None of these — let me describe it differently",
        )
        session = set_slot_ambiguous(
            session,
            SlotName.PROPOSED_BUSINESS_TEXT,
            options,
            raw_text=raw_text,
            turn_index=session.turn_index,
        )
    return session, proposed


def _run_discover(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    location_text = str(session.slot(SlotName.LOCATION_TEXT).value)
    radius_slot = session.slot(SlotName.RADIUS_M)
    if radius_slot.state is SlotState.MISSING:
        session = set_slot_assumed(
            session,
            SlotName.RADIUS_M,
            ctx.conv_cfg.default_radius_m,
            rationale="No catchment radius was stated; using the MVP default.",
            config_source="config:default_radius_m",
            turn_index=session.turn_index,
        )
        radius_m = ctx.conv_cfg.default_radius_m
    else:
        assert isinstance(radius_slot.value, int | Decimal)
        radius_m = int(radius_slot.value)

    category = session.resolved_category or BusinessCategory.UNKNOWN
    also_fetch = [c for c in _candidate_categories() if c != category]
    result = ctx.discovery_service.discover(
        location_text,
        category,
        radius_m,
        candidate=session.selected_geocode_candidate,
        also_fetch=also_fetch,
    )
    if result.status is DiscoveryStatus.LOCATION_AMBIGUOUS:
        # Nominatim's `display_name` for an Indian place commonly already
        # ends with the state (e.g. "Bhagwanpur, Vaishali, Bihar, India") —
        # append it again only when it's genuinely missing, otherwise the
        # label reads "... Bihar, India (Bihar)" and, if a user re-types or
        # pastes that label back as their next location, it no longer
        # matches anything Nominatim recognises.
        options = tuple(
            f"{c.display_name}"
            + (f" ({c.state})" if c.state and c.state.lower() not in c.display_name.lower() else "")
            for c in result.candidates
        )
        session = set_slot_ambiguous(
            session,
            SlotName.LOCATION_TEXT,
            options,
            raw_text=location_text,
            turn_index=session.turn_index,
        )
    return session, result


def _run_analyze(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    discovery = _load(session, StepId.DISCOVER, DiscoveryResult)
    proposed = _load(session, StepId.RESOLVE_PROPOSED, ProposedBusiness)
    return session, analyze_competitors(discovery, proposed)


def _run_metrics(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    analysis = _load(session, StepId.ANALYZE, CompetitorAnalysisResult)
    discovery = _load(session, StepId.DISCOVER, DiscoveryResult)
    return session, compute_competition_metrics(analysis, discovery, config=DEFAULT_METRICS_CONFIG)


def _run_demand_evidence(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    discovery = _load(session, StepId.DISCOVER, DiscoveryResult)
    evidence = acquire_demand_evidence(
        discovery,
        client=ctx.overpass_client,
        census=ctx.census,
        settings=ctx.settings,
        clock=ctx.clock,
    )
    return session, evidence


def _run_demand_signals(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    evidence = _load(session, StepId.DEMAND_EVIDENCE, DemandEvidence)
    metrics = _try_load(session, StepId.METRICS, CompetitionMetricsResult)
    return session, compute_demand_signals(
        evidence, competition=metrics, config=DEFAULT_DEMAND_CONFIG
    )


def _run_assess_market(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    metrics = _load(session, StepId.METRICS, CompetitionMetricsResult)
    demand = _load(session, StepId.DEMAND_SIGNALS, DemandSignalsResult)
    analysis = _try_load(session, StepId.ANALYZE, CompetitorAnalysisResult)
    return session, assess_market(
        metrics, demand, analysis=analysis, config=DEFAULT_ASSESSMENT_CONFIG
    )


def _run_opportunity_evidence(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    discovery = _load(session, StepId.DISCOVER, DiscoveryResult)
    demand = _load(session, StepId.DEMAND_EVIDENCE, DemandEvidence)
    candidates = _candidate_categories()
    if session.resolved_category is not None and session.resolved_category not in candidates:
        candidates.append(session.resolved_category)
    evidence = acquire_opportunity_evidence(
        discovery,
        candidates,
        client=ctx.overpass_client,
        census=ctx.census,
        settings=ctx.settings,
        clock=ctx.clock,
        demand=demand,
    )
    return session, evidence


def _run_opportunity(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    evidence = _load(session, StepId.OPPORTUNITY_EVIDENCE, OpportunityEvidence)
    profile = build_profile(session)
    fit = _try_load(session, StepId.FINANCIAL_FIT, FinancialFitInput)
    financial_fit = {fit.category: fit} if fit is not None else None
    return session, score_opportunities(
        evidence, profile, config=DEFAULT_OPPORTUNITY_CONFIG, financial_fit=financial_fit
    )


def _run_finance_knowledge(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    discovery = _try_load(session, StepId.DISCOVER, DiscoveryResult)
    resolved_place = discovery.resolved_place if discovery is not None else None
    state = resolved_place.state if resolved_place is not None else None
    district = resolved_place.district if resolved_place is not None else None
    category = session.resolved_category
    principal_slot = session.slot(SlotName.LOAN_PRINCIPAL_INR)
    loan_amount: Decimal | None = None
    if principal_slot.state is SlotState.USER_PROVIDED and isinstance(
        principal_slot.value, int | Decimal
    ):
        loan_amount = Decimal(principal_slot.value)
    query = ParameterQuery(
        names=tuple(ParameterName),
        category=category,
        state=state,
        district=district,
        loan_amount_inr=loan_amount,
    )
    query_text = f"{category.value if category else ''} loan interest rate margin licence".strip()
    evidence = acquire_finance_knowledge(
        query,
        corpus=ctx.corpus,
        retriever=ctx.retriever,
        retrieval_query_text=query_text,
        retrieval_limit=ctx.settings.knowledge_max_passages if ctx.retriever is not None else 0,
        cfg=DEFAULT_KNOWLEDGE_CONFIG,
        clock=ctx.clock,
    )
    return session, evidence


def _run_build_plan(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    return session, build_plan_input(session, cfg=ctx.conv_cfg)


def _run_bind_plan(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    plan = _load(session, StepId.BUILD_PLAN, FinancialPlanInput)
    evidence = _load(session, StepId.FINANCE_KNOWLEDGE, FinanceKnowledgeEvidence)
    bound = bind_sourced_inputs(plan, evidence, cfg=DEFAULT_KNOWLEDGE_CONFIG)
    new_plan = bound.plan
    if new_plan.financing.loan is None:
        principal = loan_principal_input(session)
        if principal is not None:
            treatment = MoratoriumTreatment(ctx.conv_cfg.default_moratorium_treatment)
            loan, _unbound = build_loan_terms(
                evidence, principal=principal, treatment=treatment, cfg=DEFAULT_KNOWLEDGE_CONFIG
            )
            if loan is not None:
                new_plan = new_plan.model_copy(
                    update={"financing": new_plan.financing.model_copy(update={"loan": loan})}
                )
    return session, new_plan


def _run_scheme_capacity(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    category = session.resolved_category or BusinessCategory.UNKNOWN
    cash_slot = session.slot(SlotName.LIQUID_CASH_INR)
    margin_capital: Decimal | int | None = None
    if cash_slot.state is SlotState.USER_PROVIDED and isinstance(cash_slot.value, int | Decimal):
        margin_capital = cash_slot.value
    return session, compute_scheme_capacity(category, margin_capital, DEFAULT_SIH_SCHEME_TABLE)


def _run_structure_finance(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    plan = _load(session, StepId.BIND_PLAN, FinancialPlanInput)
    return session, structure_financing(
        plan, scheme_cfg=DEFAULT_SIH_SCHEME_TABLE, fin_cfg=DEFAULT_FINANCE_CONFIG
    )


def _run_assess_finance(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    plan = _load(session, StepId.BIND_PLAN, FinancialPlanInput)
    structure = _load(session, StepId.STRUCTURE_FINANCE, SchemeStructureResult)
    structured_plan = apply_structure(plan, structure)
    return session, assess_financials(structured_plan, cfg=DEFAULT_FINANCE_CONFIG)


def _run_financial_fit(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    result = _load(session, StepId.ASSESS_FINANCE, FinancialAssessmentResult)
    return session, to_financial_fit(result)


def _run_recommend(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    opportunity = _load(session, StepId.OPPORTUNITY, OpportunityAnalysisResult)
    market = _try_load(session, StepId.ASSESS_MARKET, MarketAssessmentResult)
    finance = _load(session, StepId.ASSESS_FINANCE, FinancialAssessmentResult)
    return session, combine(opportunity, market, finance, cfg=ctx.conv_cfg)


def _run_swot(
    session: ConversationSession, ctx: RunContext
) -> tuple[ConversationSession, BaseModel]:
    opportunity = _load(session, StepId.OPPORTUNITY, OpportunityAnalysisResult)
    finance = _load(session, StepId.ASSESS_FINANCE, FinancialAssessmentResult)
    structure = _load(session, StepId.STRUCTURE_FINANCE, SchemeStructureResult)
    market = _try_load(session, StepId.ASSESS_MARKET, MarketAssessmentResult)
    return session, build_swot(
        opportunity, finance, market=market, structure=structure, cfg=DEFAULT_SWOT_CONFIG
    )


StepRunner = Callable[[ConversationSession, RunContext], tuple[ConversationSession, BaseModel]]

STEP_RUNNERS: dict[StepId, StepRunner] = {
    StepId.RESOLVE_PROPOSED: _run_resolve_proposed,
    StepId.DISCOVER: _run_discover,
    StepId.ANALYZE: _run_analyze,
    StepId.METRICS: _run_metrics,
    StepId.DEMAND_EVIDENCE: _run_demand_evidence,
    StepId.DEMAND_SIGNALS: _run_demand_signals,
    StepId.ASSESS_MARKET: _run_assess_market,
    StepId.OPPORTUNITY_EVIDENCE: _run_opportunity_evidence,
    StepId.OPPORTUNITY: _run_opportunity,
    StepId.FINANCE_KNOWLEDGE: _run_finance_knowledge,
    StepId.BUILD_PLAN: _run_build_plan,
    StepId.BIND_PLAN: _run_bind_plan,
    StepId.SCHEME_CAPACITY: _run_scheme_capacity,
    StepId.STRUCTURE_FINANCE: _run_structure_finance,
    StepId.ASSESS_FINANCE: _run_assess_finance,
    StepId.FINANCIAL_FIT: _run_financial_fit,
    StepId.RECOMMEND: _run_recommend,
    StepId.SWOT: _run_swot,
}

# The exact config each pure engine call was made with, `model_dump(mode="json")`
# once at import time — `conversation/artifacts.py::compute_fingerprints` uses
# this SAME dict so a step's fingerprint changes if (and only if) a config
# value the engine actually reads changes. Steps not listed here take no
# config parameter at all (RESOLVE_PROPOSED, DISCOVER's own selectors are
# settings-driven not config-driven, ANALYZE, DEMAND_EVIDENCE,
# OPPORTUNITY_EVIDENCE, FINANCE_KNOWLEDGE, BUILD_PLAN, FINANCIAL_FIT).
CONFIG_BLOBS: dict[StepId, dict] = {
    StepId.METRICS: DEFAULT_METRICS_CONFIG.model_dump(mode="json"),
    StepId.DEMAND_SIGNALS: DEFAULT_DEMAND_CONFIG.model_dump(mode="json"),
    StepId.ASSESS_MARKET: DEFAULT_ASSESSMENT_CONFIG.model_dump(mode="json"),
    # Nested (not a single model's dump): structure_financing() takes two
    # distinct configs. Either one changing — a scheme figure or a finance
    # structural default — must bust the fingerprint.
    StepId.STRUCTURE_FINANCE: {
        "scheme": [cfg.model_dump(mode="json") for cfg in DEFAULT_SIH_SCHEME_TABLE],
        "finance": DEFAULT_FINANCE_CONFIG.model_dump(mode="json"),
    },
    StepId.SCHEME_CAPACITY: {
        "scheme": [cfg.model_dump(mode="json") for cfg in DEFAULT_SIH_SCHEME_TABLE],
    },
    StepId.OPPORTUNITY: DEFAULT_OPPORTUNITY_CONFIG.model_dump(mode="json"),
    StepId.BIND_PLAN: DEFAULT_KNOWLEDGE_CONFIG.model_dump(mode="json"),
    StepId.ASSESS_FINANCE: DEFAULT_FINANCE_CONFIG.model_dump(mode="json"),
    StepId.RECOMMEND: DEFAULT_CONVERSATION_CONFIG.model_dump(mode="json"),
    StepId.SWOT: DEFAULT_SWOT_CONFIG.model_dump(mode="json"),
}


def run_step(
    step: StepId, session: ConversationSession, ctx: RunContext, *, turn_index: int
) -> ConversationSession:
    """Execute `step`, record its artifact (fingerprinted against the SAME
    `CONFIG_BLOBS` `conversation/artifacts.py` uses for invalidation, so a
    freshly-run step's stored fingerprint always matches what `invalidate()`
    would recompute), and return the updated session. The only function
    `llm/orchestrator.py` calls to actually run a step."""
    runner = STEP_RUNNERS[step]
    new_session, result = runner(session, ctx)
    fresh = compute_fingerprints(new_session, CONFIG_BLOBS)
    payload_type = f"{type(result).__module__}.{type(result).__qualname__}"
    return record_artifact(
        new_session,
        step,
        payload=result.model_dump(mode="json"),
        payload_type=payload_type,
        fingerprint=fresh[step],
        turn_index=turn_index,
    )


__all__ = [
    "CONFIG_BLOBS",
    "STEP_RESULT_MODEL",
    "STEP_RUNNERS",
    "RunContext",
    "StepRunner",
    "run_step",
]
