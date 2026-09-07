"""Result models for the SIH scheme financial structurer (CLAUDE.md §14, §15,
§22, §23; Tier 1 "SIH 10%/90% Financial Structuring"). PURE.

`SchemeStructureResult` is deliberately narrow: it derives the required
promoter margin and the indicated loan from the declared split and an
already-computed project cost, and stops there. It never computes a capital
gap of its own — `finance/costs.py::compute_capital_gap`, run inside
`finance/assessment.py::assess_financials` on the structured plan, already
owns "is there enough funding to cover the project cost", and duplicating
that concept here would let two capital-gap numbers disagree. A clipped or
short loan simply surfaces later as Phase 4's own `FINANCING_GAP` status —
the honest, non-duplicated outcome.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.models.finance import LoanTerms
from vyaparsarathi.models.taxonomy import BusinessCategory


class SchemeStructureStatus(StrEnum):
    STRUCTURED = "structured"  # a margin/loan split was derived from project cost
    NOT_CONFIGURED = "not_configured"  # no scheme config is declared for this deployment
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # project cost could not be derived yet


class StructureEvidenceRef(BaseModel):
    """A typed pointer at the field a `StructureFinding` rests on (the Phase
    2D / Phase 3 / Phase 4 auditability pattern, reused verbatim)."""

    model_config = ConfigDict(extra="forbid")

    source: Literal["cost", "profile", "config"]
    field: str
    value: float | int | str | bool | None
    compared_to: float | None = None


class StructureFinding(BaseModel):
    """Names the exact boundary condition a structuring outcome rests on
    (CLAUDE.md §16's "names the exact condition" idiom, applied to
    structuring rather than stress-testing)."""

    model_config = ConfigDict(extra="forbid")

    code: str  # stable, machine-readable, unique within a result
    message: str
    evidence: list[StructureEvidenceRef] = Field(default_factory=list)


class SchemeStructureResult(BaseModel):
    """Phase 4-adjacent output. Deterministic, JSON-serializable. Carries no
    verdict of its own — `finance/assessment.py::assess_financials`, run on
    the structured plan (`finance/structuring.py::apply_structure`), is what
    decides feasibility."""

    model_config = ConfigDict(extra="forbid")

    status: SchemeStructureStatus
    scheme_name: str = ""
    category: BusinessCategory

    project_cost_inr: Decimal | None = None
    required_promoter_margin_inr: Decimal | None = None
    # The figure the margin is checked against — the entrepreneur's stated
    # liquid cash (never `financing.promoter_cash_contribution`, and never
    # summed with physical assets: CLAUDE.md §13).
    stated_liquid_cash_inr: Decimal | None = None
    margin_shortfall_inr: Decimal | None = (
        None  # max(0, required - stated); None if either side unknown
    )

    indicated_loan_inr: Decimal | None = None
    loan_clipped_by_ceiling: bool = False
    # Populated only when the declared scheme states a full rate/tenure/
    # moratorium; `finance/structuring.py::apply_structure` writes this onto
    # `FinancialPlanInput.financing.loan` ONLY when that field is still None
    # — a user-stated or Phase-5-sourced loan is never overwritten.
    loan_terms: LoanTerms | None = None

    findings: list[StructureFinding] = Field(default_factory=list)
    missing_core_drivers: list[str] = Field(default_factory=list)

    caveats: list[str] = Field(default_factory=list)
    config: SihSchemeConfig | None = None
    warnings: list[str] = Field(default_factory=list)


__all__ = [
    "SchemeStructureResult",
    "SchemeStructureStatus",
    "StructureEvidenceRef",
    "StructureFinding",
]
