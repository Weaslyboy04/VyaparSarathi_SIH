"""A compact, deterministic evidence bundle (CLAUDE.md §22, §23, §25 Phase
6). PURE.

Feeding a raw engine result to an LLM is both impossible and unsafe: one
`FinancialAssessmentResult` serialises to ~10k tokens, `OpportunityAnalysisResult`
~6k — a haystack of numbers to pick from (the measured constraint the
approved plan's architecture rests on). `build_bundle` instead extracts a
short, named list of `Fact` cards from whatever step artifacts already
exist, each carrying `origin` — literally CLAUDE.md §23's four-way
distinction (`source_fact` / `retrieved_rule` / `calculation` / `profile`) —
and a `render` string that is the ONLY form a number may take in generated
prose (`conversation/grounding.py` checks this). The bundle IS the
grounding allowlist.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.conversation.recommendation_models import RecommendationResult
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.market.assessment_models import MarketAssessmentResult
from vyaparsarathi.market.opportunity_models import OpportunityAnalysisResult
from vyaparsarathi.models.parameters import FinanceKnowledgeEvidence, ResolutionStatus


class FactOrigin(StrEnum):
    """CLAUDE.md §23's four-way distinction, restated as a type."""

    SOURCE_FACT = "source_fact"  # a discovered/measured business, place or population figure
    RETRIEVED_RULE = "retrieved_rule"  # a resolved scheme parameter, with a citation
    CALCULATION = "calculation"  # produced by a deterministic engine
    PROFILE = "profile"  # the entrepreneur's own unverified statement


class Fact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str
    label: str
    render: str  # the ONLY string form this fact may appear as in prose
    origin: FactOrigin
    citation_id: str | None = None


class EvidenceBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: tuple[Fact, ...] = Field(default_factory=tuple)
    citations: dict[str, str] = Field(default_factory=dict)  # citation_id -> human-readable ref

    def get(self, key: str) -> Fact | None:
        return next((f for f in self.facts if f.key == key), None)

    def renders(self) -> tuple[str, ...]:
        """Every allowed numeric/textual rendering — the grounding allowlist."""
        return tuple(f.render for f in self.facts)


_M = TypeVar("_M", bound=BaseModel)


def _artifact(session: ConversationSession, step: StepId, model_cls: type[_M]) -> _M | None:
    art = session.artifacts.get(step)
    return model_cls.model_validate(art.payload) if art is not None else None


def build_bundle(session: ConversationSession) -> EvidenceBundle:
    facts: list[Fact] = []
    citations: dict[str, str] = {}

    opportunity = _artifact(session, StepId.OPPORTUNITY, OpportunityAnalysisResult)
    if opportunity is not None:
        facts.append(
            Fact(
                key="opportunity.stance",
                label="Market stance",
                render=opportunity.stance.value.replace("_", " "),
                origin=FactOrigin.CALCULATION,
            )
        )
        facts.append(
            Fact(
                key="opportunity.market_data_confidence",
                label="Market-data confidence",
                render=f"{opportunity.market_data_confidence:.0%}",
                origin=FactOrigin.CALCULATION,
            )
        )
        for candidate in opportunity.candidates:
            if candidate.is_proposed and candidate.opportunity_score is not None:
                facts.append(
                    Fact(
                        key="opportunity.proposed_score",
                        label="Proposed business opportunity score",
                        render=f"{candidate.opportunity_score}/100",
                        origin=FactOrigin.CALCULATION,
                    )
                )
        if opportunity.recommended_pivot is not None:
            facts.append(
                Fact(
                    key="opportunity.recommended_pivot",
                    label="Recommended pivot",
                    render=opportunity.recommended_pivot.value.replace("_", " "),
                    origin=FactOrigin.CALCULATION,
                )
            )

    market = _artifact(session, StepId.ASSESS_MARKET, MarketAssessmentResult)
    if market is not None:
        facts.append(
            Fact(
                key="market.label",
                label="Market reading",
                render=market.label.value.replace("_", " "),
                origin=FactOrigin.CALCULATION,
            )
        )

    finance = _artifact(session, StepId.ASSESS_FINANCE, FinancialAssessmentResult)
    if finance is not None:
        facts.append(
            Fact(
                key="finance.status",
                label="Financial status",
                render=finance.status.value.replace("_", " "),
                origin=FactOrigin.CALCULATION,
            )
        )
        dscr = finance.dscr
        if dscr is not None and dscr.average_annual_dscr is not None:
            facts.append(
                Fact(
                    key="finance.average_annual_dscr",
                    label="Average annual DSCR",
                    render=str(dscr.average_annual_dscr),
                    origin=FactOrigin.CALCULATION,
                )
            )
        break_even = finance.break_even
        if break_even is not None and break_even.cash_break_even_month is not None:
            facts.append(
                Fact(
                    key="finance.cash_break_even_month",
                    label="Cash break-even month",
                    render=str(break_even.cash_break_even_month),
                    origin=FactOrigin.CALCULATION,
                )
            )
        if finance.breaking_point:
            facts.append(
                Fact(
                    key="finance.breaking_point",
                    label="Named breaking point",
                    render=finance.breaking_point,
                    origin=FactOrigin.CALCULATION,
                )
            )
        for i, driver in enumerate(finance.missing_core_drivers):
            facts.append(
                Fact(
                    key=f"finance.missing_core_driver.{i}",
                    label="Missing financial driver",
                    render=driver,
                    origin=FactOrigin.CALCULATION,
                )
            )

    knowledge = _artifact(session, StepId.FINANCE_KNOWLEDGE, FinanceKnowledgeEvidence)
    if knowledge is not None:
        for resolution in knowledge.resolutions:
            if resolution.status is ResolutionStatus.RESOLVED and resolution.chosen is not None:
                cid = f"knowledge.{resolution.name.value}"
                facts.append(
                    Fact(
                        key=cid,
                        label=resolution.name.value.replace("_", " "),
                        render=str(resolution.chosen.value_token),
                        origin=FactOrigin.RETRIEVED_RULE,
                        citation_id=cid,
                    )
                )
                citations[cid] = resolution.citation or resolution.source

    recommend = _artifact(session, StepId.RECOMMEND, RecommendationResult)
    if recommend is not None:
        facts.append(
            Fact(
                key="recommend.verdict",
                label="Verdict",
                render=recommend.verdict.value.replace("_", " "),
                origin=FactOrigin.CALCULATION,
            )
        )
        facts.append(
            Fact(
                key="recommend.reason",
                label="Reason",
                render=recommend.reason,
                origin=FactOrigin.CALCULATION,
            )
        )

    return EvidenceBundle(facts=tuple(facts), citations=citations)


__all__ = ["EvidenceBundle", "Fact", "FactOrigin", "build_bundle"]
