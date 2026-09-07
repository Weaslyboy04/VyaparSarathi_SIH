"""The Phase 6 recommendation output (CLAUDE.md §4's orphaned "Recommendation
engine" row — assigned to no phase in §25's table until now). PURE.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus
from vyaparsarathi.market.opportunity_models import Stance
from vyaparsarathi.models.taxonomy import BusinessCategory


class Verdict(StrEnum):
    """Deliberately avoids every word CLAUDE.md's guardrail lists ban
    (`scripts/phase3_demo.py:382`, `scripts/phase4_demo.py:250`,
    `tests/test_finance_guardrails.py:33-34`) — no "guaranteed", "will
    succeed", "safe", "approved"."""

    PROCEED = "proceed"
    PROCEED_WITH_CAUTION = "proceed_with_caution"
    ADJUST = "adjust"
    PIVOT = "pivot"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class RecommendationResult(BaseModel):
    """The combiner's output. Never a probability, never a single blended
    score — `stance` and `financial_status` are always echoed alongside
    `verdict` so a reader can see exactly which two inputs produced it
    (CLAUDE.md §22: market confidence, opportunity score and financial
    status are separate, never-multiplied measurements)."""

    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    reason: str
    stance: Stance
    financial_status: FinancialFeasibilityStatus
    recommended_pivot: BusinessCategory | None = None  # set ONLY for verdict == PIVOT
    caveats: list[str] = Field(default_factory=list)


__all__ = ["RecommendationResult", "Verdict"]
