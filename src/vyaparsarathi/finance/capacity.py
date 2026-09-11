"""SIH margin-capital -> project-capacity -> loan calculation (CLAUDE.md §12,
§14, §15, §18, §30; Tier 1 "SIH 10%/90% Financial Structuring", the SIH26091
headline calculation). PURE.

The forward direction — `finance/structuring.py`: cost -> margin split ->
loan — needs an already-computed project cost, which needs four financial
drivers (revenue, a margin, a project-cost line, fixed opex) before it can
say anything. This module is the INVERSE direction the SIH26091 problem
statement's own worked example asks for first: from the entrepreneur's
stated **Available Margin Capital alone**, band-route across the declared
scheme table (`config/sih_scheme.py::DEFAULT_SIH_SCHEME_TABLE`) and report
what project that capital supports — before any of those four drivers is
known.

Worked example (SIH26091's own):

    Available Margin Capital = Rs 1,00,000
    -> Term Loan band (Rs 1.40L-50L) is the best-fit band
    -> feasible project cost = Rs 10,00,000   (margin / 10%, capped by ceilings)
    -> indicated loan        = Rs  9,00,000   (90%)

This is a **capacity screen**, not a viability verdict — CLAUDE.md §12's
distinction ("maximum possible project capacity under the scheme" is never
confused with "actual viable business project cost"), generalised beyond
Phase 3's opportunity score to Tier 1's headline number. Once revenue, cost
of goods, an actual project cost and monthly operating expenses are known,
`finance/structuring.py` (cost -> split) and `finance/assessment.py`
(DSCR, cash flow, break-even, stress) answer the second, separate question:
whether the ACTUAL business survives. Never invents Available Margin Capital
(`None` in, `INSUFFICIENT_EVIDENCE` out); never treats a physical asset as
cash — the same two rules `finance/structuring.py` states, unchanged here.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.finance.money import q_money, rupees
from vyaparsarathi.finance.scheme_router import normalize_schemes, scheme_covers_project_cost
from vyaparsarathi.finance.structuring import build_scheme_loan_terms
from vyaparsarathi.models.finance import LoanTerms
from vyaparsarathi.models.taxonomy import BusinessCategory


class SchemeCapacityStatus(StrEnum):
    CALCULATED = "calculated"
    NOT_CONFIGURED = "not_configured"  # no scheme is declared for this deployment
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # Available Margin Capital not stated
    NO_ELIGIBLE_SCHEME = "no_eligible_scheme"  # the margin fits no declared band


class SchemeCapacityFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class SchemeCapacityResult(BaseModel):
    """Deterministic, JSON-serializable. Carries no verdict of its own — it
    says what a scheme's declared split lets this margin capital fund, never
    whether the resulting business will actually work (that is
    `finance/assessment.py`, once the four core drivers are known)."""

    model_config = ConfigDict(extra="forbid")

    status: SchemeCapacityStatus
    category: BusinessCategory
    margin_capital_inr: Decimal | None = None

    scheme_name: str = ""
    feasible_project_cost_inr: Decimal | None = None
    required_promoter_margin_inr: Decimal | None = None
    indicated_loan_inr: Decimal | None = None
    # Populated only when the selected scheme declares a full rate/tenure/
    # moratorium (mirrors `finance/structuring.py::SchemeStructureResult`).
    loan_terms: LoanTerms | None = None
    monthly_emi_inr: Decimal | None = None

    findings: list[SchemeCapacityFinding] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    config: SihSchemeConfig | None = None
    warnings: list[str] = Field(default_factory=list)


# Fixed, human-authored — mirrors finance/structuring.py's caveat convention.
# Never generated interpretation. [decision]
_CAPACITY_CAVEATS: tuple[str, ...] = (
    "This is the project a declared scheme's financing split says your stated Available "
    "Margin Capital could support — a capacity screen, not a viability verdict. It says "
    "nothing about whether this business, at this location, will actually earn enough to "
    "repay the loan; that is a separate, evidence-based check once revenue, cost of "
    "goods, an actual project cost and monthly expenses are known.",
    "The promoter-margin percentage and loan-to-cost ratio here come from the SIH26091 "
    "problem statement's declared financing structure, not from a retrieved scheme "
    "document — they are configuration this deployment states, not an external fact.",
    "Owning a physical asset never counts toward Available Margin Capital here — only "
    "stated liquid cash does (CLAUDE.md §13, §14).",
)


def compute_scheme_capacity(
    category: BusinessCategory,
    margin_capital_inr: Decimal | int | None,
    schemes: SihSchemeConfig | Sequence[SihSchemeConfig] | None,
) -> SchemeCapacityResult:
    """Available Margin Capital alone -> the best-fit declared scheme's
    feasible project cost, required promoter margin, indicated loan and
    (when that scheme states full loan terms) a monthly EMI. Needs none of
    `finance/assessment.py`'s four core drivers."""
    ordered = normalize_schemes(schemes)
    if not ordered:
        return SchemeCapacityResult(
            status=SchemeCapacityStatus.NOT_CONFIGURED,
            category=category,
            findings=[
                SchemeCapacityFinding(
                    code="scheme_not_configured",
                    message=(
                        "No SIH financing structure is declared for this deployment; a "
                        "project capacity could not be derived."
                    ),
                )
            ],
        )
    if margin_capital_inr is None:
        return SchemeCapacityResult(
            status=SchemeCapacityStatus.INSUFFICIENT_EVIDENCE,
            category=category,
            findings=[
                SchemeCapacityFinding(
                    code="margin_capital_unknown",
                    message=(
                        "Available Margin Capital has not been stated yet; a project "
                        "capacity cannot be derived without it."
                    ),
                )
            ],
        )
    margin = rupees(margin_capital_inr)

    candidates: list[tuple[SihSchemeConfig, Decimal]] = []
    for cfg in ordered:
        if cfg.promoter_contribution_pct <= 0:
            continue  # a 0% margin requirement cannot bound a feasible cost from margin alone
        caps = [margin / cfg.promoter_contribution_pct]
        if cfg.max_project_cost_inr is not None:
            caps.append(cfg.max_project_cost_inr)
        if cfg.max_loan_inr is not None and cfg.loan_pct > 0:
            caps.append(cfg.max_loan_inr / cfg.loan_pct)
        feasible_cost = q_money(min(caps))
        if scheme_covers_project_cost(cfg, feasible_cost):
            candidates.append((cfg, feasible_cost))

    if not candidates:
        return SchemeCapacityResult(
            status=SchemeCapacityStatus.NO_ELIGIBLE_SCHEME,
            category=category,
            margin_capital_inr=margin,
            findings=[
                SchemeCapacityFinding(
                    code="no_eligible_scheme",
                    message=(
                        f"Available Margin Capital (Rs {margin}) does not fit any declared "
                        "scheme's project-cost band."
                    ),
                )
            ],
        )

    # Tie-break: the entrepreneur's best outcome — the band letting this
    # margin support the LARGEST feasible project. Deterministic (ties
    # broken by table order via `max`'s first-max-wins semantics).
    cfg, feasible_cost = max(candidates, key=lambda pair: pair[1])

    required_margin = q_money(feasible_cost * cfg.promoter_contribution_pct)
    indicated_loan = q_money(feasible_cost * cfg.loan_pct)

    loan_terms = build_scheme_loan_terms(cfg, indicated_loan_inr=indicated_loan)
    monthly_emi: Decimal | None = None
    warnings: list[str] = []
    if loan_terms is None:
        warnings.append(
            f"{cfg.scheme_name} does not declare a full interest rate/tenure/moratorium; "
            "only the capacity/loan split was derived, not an EMI"
        )
    else:
        schedule = compute_debt_schedule(loan_terms)
        monthly_emi = schedule.emi_inr
        if monthly_emi is None:
            warnings.append(
                "the indicated loan would never amortise at this scheme's declared rate "
                "and tenure (the EMI would not exceed the first period's interest)"
            )

    return SchemeCapacityResult(
        status=SchemeCapacityStatus.CALCULATED,
        category=category,
        margin_capital_inr=margin,
        scheme_name=cfg.scheme_name,
        feasible_project_cost_inr=feasible_cost,
        required_promoter_margin_inr=required_margin,
        indicated_loan_inr=indicated_loan,
        loan_terms=loan_terms,
        monthly_emi_inr=monthly_emi,
        caveats=list(_CAPACITY_CAVEATS),
        config=cfg,
        warnings=warnings,
    )


__all__ = [
    "SchemeCapacityFinding",
    "SchemeCapacityResult",
    "SchemeCapacityStatus",
    "compute_scheme_capacity",
]
