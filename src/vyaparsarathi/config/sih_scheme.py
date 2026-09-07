"""The SIH26091 declared financing structure (CLAUDE.md §12, §14, §15, §18,
§30; Tier 1 "SIH 10%/90% Financial Structuring").

This is **declared problem-statement configuration**, not a retrieved scheme
rule (§18: "the scheme/credit routing engine consumes RAG output to obtain
scheme parameters; it does not guess them") and not a fact this repository
discovered on its own — it is the financing split, ceilings, and (optionally)
loan terms the SIH26091 statement itself specifies for this hackathon
problem. Every `FinancialInput` `finance/structuring.py` derives from it is
tagged `InputKind.ASSUMED`, `source="config:sih_scheme"` (CLAUDE.md §15: "all
rates, tenures, margins ... are inputs ... never hard-coded constants
pretending to be facts") — never `SOURCED`, which is reserved for a real
retrieved document (`knowledge/plan_binding.py`).

**Ships unconfigured** (`DEFAULT_SIH_SCHEME_CONFIG = None`), exactly like
`data/knowledge/` ships an empty corpus (CLAUDE.md §30: "do not invent
missing data — surface the gap"). `finance/structuring.py` degrades to an
honest `NOT_CONFIGURED` status when no config is set — it never invents a
split. Set `DEFAULT_SIH_SCHEME_CONFIG` to a real `SihSchemeConfig` instance
here, built only from the SIH26091 problem-statement text, to activate
structuring. [tunable]
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vyaparsarathi.models.finance import MoratoriumTreatment


class SihSchemeConfig(BaseModel):
    """One declared financing structure. Frozen; echoed into every
    `SchemeStructureResult` for traceability (CLAUDE.md §23).

    ``promoter_contribution_pct`` and ``loan_pct`` must sum to exactly 1 — a
    financing split that does not fully account for the project cost is a
    configuration error, not a runtime condition. The four loan-term fields
    (``interest_rate_pct``/``tenure_months``/``moratorium_months``/
    ``moratorium_treatment``) are optional: a statement that declares only
    the cost split still lets the structurer derive the required promoter
    margin and the indicated loan amount, just not a full `LoanTerms`
    (`finance/structuring.py` reports this explicitly rather than
    half-building one).
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    scheme_name: str = Field(min_length=1)

    # The declared cost split. RATIO, e.g. Decimal("0.10") / Decimal("0.90").
    promoter_contribution_pct: Decimal = Field(ge=0, le=1)
    loan_pct: Decimal = Field(ge=0, le=1)

    # Boundary limits the structurer screens against; None = not declared.
    min_project_cost_inr: Decimal | None = Field(default=None, ge=0)
    max_project_cost_inr: Decimal | None = Field(default=None, ge=0)
    max_loan_inr: Decimal | None = Field(default=None, ge=0)

    # Optional declared loan terms. All three (plus a treatment) must be
    # present for `finance/structuring.py` to build a full `LoanTerms`.
    interest_rate_pct: Decimal | None = Field(default=None, ge=0)  # PERCENT_PER_ANNUM
    tenure_months: int | None = Field(default=None, ge=1)
    moratorium_months: int | None = Field(default=None, ge=0)
    # Used only when moratorium_months > 0; forced to NONE otherwise
    # (mirrors conversation/plan_builder.py's own convention).
    moratorium_treatment: MoratoriumTreatment = MoratoriumTreatment.INTEREST_SERVICED

    # Names the SIH26091 problem statement as the origin — required
    # non-empty by `FinancialInput._kind_shape` for every ASSUMED value this
    # config produces.
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def _split_sums_to_one(self) -> SihSchemeConfig:
        total = self.promoter_contribution_pct + self.loan_pct
        if total != Decimal("1"):
            raise ValueError(
                f"promoter_contribution_pct + loan_pct must equal 1; got {total} "
                f"({self.promoter_contribution_pct} + {self.loan_pct})"
            )
        if (
            self.min_project_cost_inr is not None
            and self.max_project_cost_inr is not None
            and self.min_project_cost_inr > self.max_project_cost_inr
        ):
            raise ValueError("min_project_cost_inr must not exceed max_project_cost_inr")
        return self


# Unconfigured by default — see the module docstring. Replace with a real
# `SihSchemeConfig(...)` built from the SIH26091 problem-statement text to
# activate deterministic financial structuring.
DEFAULT_SIH_SCHEME_CONFIG: SihSchemeConfig | None = None


__all__ = ["DEFAULT_SIH_SCHEME_CONFIG", "SihSchemeConfig"]
