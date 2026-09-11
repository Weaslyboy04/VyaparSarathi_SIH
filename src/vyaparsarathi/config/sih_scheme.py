"""The SIH26091 declared financing structure (CLAUDE.md §12, §14, §15, §18,
§30; Tier 1 "SIH 10%/90% Financial Structuring").

This is **declared problem-statement configuration**, not a retrieved scheme
rule (§18: "the scheme/credit routing engine consumes RAG output to obtain
scheme parameters; it does not guess them") and not a fact this repository
discovered on its own — it is the financing split, ceilings, and loan terms
the SIH26091 statement itself specifies for this hackathon problem. Every
`FinancialInput` `finance/structuring.py` / `finance/capacity.py` derives
from it is tagged `InputKind.ASSUMED`, `source="config:sih_scheme"`
(CLAUDE.md §15: "all rates, tenures, margins ... are inputs ... never
hard-coded constants pretending to be facts") — never `SOURCED`, which is
reserved for a real retrieved document (`knowledge/plan_binding.py`).

**Two declared bands, both `[tunable]`** — the SIH26091 problem statement's
own worked structure, a fixed 10% promoter / 90% funding split throughout,
selected by project-cost band (see `finance/scheme_router.py`):

* **Micro Finance** — project cost up to Rs 1.40 lakh; max loan Rs 1.25 lakh;
  6.5% p.a.; 3-year tenure; 3-month moratorium.
* **Term Loan** — project cost above Rs 1.40 lakh up to Rs 50 lakh; max loan
  Rs 45 lakh; 8% p.a.; 7-year tenure; 6-month moratorium.

`DEFAULT_SIH_SCHEME_TABLE` declares both, so `finance/structuring.py` and
`finance/capacity.py` are **configured out of the box** — unlike
`data/knowledge/`, which genuinely ships an empty corpus (CLAUDE.md §30: "do
not invent missing data — surface the gap"; there is a real, official
SIH26091 rule to declare here, not an absence to admit). Passing an empty
table (or `None`) still degrades honestly to `NOT_CONFIGURED` — that path is
kept and tested, for a deployment that deliberately does not want Tier 1
structuring active.
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


# The SIH26091 problem statement's own two declared bands — see the module
# docstring. A 10%/90% split throughout; project-cost band decides which one
# applies (`finance/scheme_router.py::route_scheme`).
MICRO_FINANCE = SihSchemeConfig(
    scheme_name="Micro Finance",
    promoter_contribution_pct=Decimal("0.10"),
    loan_pct=Decimal("0.90"),
    max_project_cost_inr=Decimal("140000"),
    max_loan_inr=Decimal("125000"),
    interest_rate_pct=Decimal("6.5"),
    tenure_months=36,
    moratorium_months=3,
    rationale=(
        "SIH26091 problem statement's declared micro-finance structure: project cost up "
        "to Rs 1.40 lakh, 90% funding up to Rs 1.25 lakh, 6.5% p.a., 3-year tenure, "
        "3-month moratorium."
    ),
)

TERM_LOAN = SihSchemeConfig(
    scheme_name="Term Loan",
    promoter_contribution_pct=Decimal("0.10"),
    loan_pct=Decimal("0.90"),
    min_project_cost_inr=Decimal("140000"),
    max_project_cost_inr=Decimal("5000000"),
    max_loan_inr=Decimal("4500000"),
    interest_rate_pct=Decimal("8"),
    tenure_months=84,
    moratorium_months=6,
    rationale=(
        "SIH26091 problem statement's declared term-loan structure: project cost above "
        "Rs 1.40 lakh up to Rs 50 lakh, 90% funding up to Rs 45 lakh, 8% p.a., 7-year "
        "tenure, 6-month moratorium."
    ),
)

# The shipped default: both bands declared, so Tier 1 structuring is
# configured out of the box (see the module docstring). An empty tuple (or
# `None`, still accepted at every call site for backward compatibility)
# degrades honestly to `NOT_CONFIGURED` — never a fabricated split.
DEFAULT_SIH_SCHEME_TABLE: tuple[SihSchemeConfig, ...] = (MICRO_FINANCE, TERM_LOAN)


__all__ = [
    "DEFAULT_SIH_SCHEME_TABLE",
    "MICRO_FINANCE",
    "SihSchemeConfig",
    "TERM_LOAN",
]
