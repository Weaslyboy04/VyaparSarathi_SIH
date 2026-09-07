"""SIH 10%/90% financial structuring (CLAUDE.md §12, §14, §15, §18, §22, §30;
Tier 1). PURE.

Turns a `FinancialPlanInput` + the declared `SihSchemeConfig` into a
`SchemeStructureResult`: the required promoter margin and the indicated loan,
derived from the declared cost split and the **existing**
`finance/costs.py`/`finance/operations.py` project-cost pipeline — this
module computes no EMI, DSCR, cash flow, break-even, or stress result of its
own; those stay exactly where they are (CLAUDE.md §15's engine, untouched).

Two hard rules this module exists to enforce (CLAUDE.md §13, §14, §30):

* **Never invents promoter contribution.** `FinancingInput.
  promoter_cash_contribution` is never read as a source of truth to compare
  against, and never written by `apply_structure`. The required margin is
  checked against `EntrepreneurProfile.liquid_cash_inr` only, and a shortfall
  is reported as a `StructureFinding` — never repaired by inflating a stated
  figure.
* **Never treats a physical asset as cash.** Only `liquid_cash_inr` feeds the
  margin check; `EntrepreneurProfile.assets` plays no part here (an owned
  asset can already reduce project cost via `AssetSpendOffset`, a separate
  and already-existing mechanism — `finance/costs.py::compute_project_cost`).

Every `FinancialInput` this module emits is `InputKind.ASSUMED`,
`source="config:sih_scheme"` — declared problem-statement configuration, not
a retrieved scheme rule. `FinancingInput._margin_not_assumed` forbids writing
an ASSUMED value into `declared_margin_requirement`, so the required margin
is carried only on `SchemeStructureResult`, never on the plan itself — an
assumed figure must never harden into a Phase 3 input via
`finance/fit.py::to_financial_fit` (CLAUDE.md §14: "scheme rules ... determine
eligibility", never an assumption pretending to be one).
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.finance.assessment import missing_core_drivers
from vyaparsarathi.finance.costs import compute_project_cost, compute_working_capital
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG, FinanceConfig
from vyaparsarathi.finance.money import q_money, rupees
from vyaparsarathi.finance.operations import compute_operating_costs, compute_revenue_schedule
from vyaparsarathi.finance.scheme_router import SchemeRoutingStatus, route_scheme
from vyaparsarathi.finance.structuring_models import (
    SchemeStructureResult,
    SchemeStructureStatus,
    StructureEvidenceRef,
    StructureFinding,
)
from vyaparsarathi.models.finance import (
    FinancialInput,
    FinancialPlanInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    Unit,
)

_ZERO = Decimal("0.00")

# Fixed, human-authored — mirrors finance/finance_config.py's caveat
# convention. Never generated interpretation. [decision]
_STRUCTURE_CAVEATS: tuple[str, ...] = (
    "The promoter-margin percentage and loan-to-cost ratio here come from the SIH26091 "
    "problem statement's declared financing structure, not from a retrieved scheme "
    "document — they are configuration this deployment states, not an external fact.",
    "This engine does not determine loan eligibility, scheme sanction, or credit "
    "approval. It structures a plan against a declared split; a bank's or scheme's own "
    "appraisal is a separate process this does not replace.",
    "Owning a physical asset never counts toward the required promoter margin here — "
    "only stated liquid cash does (CLAUDE.md §13, §14).",
)


def _build_loan_terms(cfg: SihSchemeConfig, *, indicated_loan_inr: Decimal) -> LoanTerms | None:
    """`None` when the declared scheme states only the cost split (no full
    rate/tenure/moratorium) — `LoanTerms` is all-or-nothing at construction,
    so this never returns a partially-sourced loan, mirroring
    `knowledge/plan_binding.py::build_loan_terms`'s own convention."""
    if cfg.interest_rate_pct is None or cfg.tenure_months is None or cfg.moratorium_months is None:
        return None
    treatment = MoratoriumTreatment.NONE if cfg.moratorium_months == 0 else cfg.moratorium_treatment

    def _assumed(label: str, value: Decimal | int, unit: Unit) -> FinancialInput:
        return FinancialInput(
            label=label,
            value=value,
            unit=unit,
            kind=InputKind.ASSUMED,
            source="config:sih_scheme",
            rationale=cfg.rationale,
        )

    return LoanTerms(
        principal_requested=_assumed("loan_principal_inr", indicated_loan_inr, Unit.INR),
        interest_rate_pct=_assumed(
            "loan_interest_rate_pct", cfg.interest_rate_pct, Unit.PERCENT_PER_ANNUM
        ),
        tenure_months=_assumed("loan_tenure_months", cfg.tenure_months, Unit.MONTHS),
        moratorium_months=_assumed("loan_moratorium_months", cfg.moratorium_months, Unit.MONTHS),
        moratorium_treatment=treatment,
    )


def structure_financing(
    plan: FinancialPlanInput,
    *,
    scheme_cfg: SihSchemeConfig | None,
    fin_cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG,
) -> SchemeStructureResult:
    """Derive a `SchemeStructureResult` for `plan` under the declared
    `scheme_cfg`. `scheme_cfg=None` (the shipped default) returns
    `NOT_CONFIGURED` without touching the plan — never a fabricated split."""
    routing = route_scheme(plan.category, scheme_cfg)
    if routing.status is SchemeRoutingStatus.NOT_CONFIGURED or routing.selected is None:
        return SchemeStructureResult(
            status=SchemeStructureStatus.NOT_CONFIGURED,
            category=plan.category,
            findings=[
                StructureFinding(
                    code="scheme_not_configured",
                    message=(
                        "No SIH financing structure is declared for this deployment; "
                        "loan terms and a promoter-margin requirement were not derived."
                    ),
                )
            ],
            warnings=list(routing.reasons),
        )
    cfg = routing.selected

    missing = missing_core_drivers(plan)
    if missing:
        return SchemeStructureResult(
            status=SchemeStructureStatus.INSUFFICIENT_EVIDENCE,
            scheme_name=cfg.scheme_name,
            category=plan.category,
            missing_core_drivers=missing,
            config=cfg,
            warnings=[
                f"{len(missing)} core financial driver(s) missing; project cost could "
                "not be derived, so no financing split was structured"
            ],
        )

    # The exact project-cost pipeline finance/pipeline.py::run_core_pipeline
    # uses, up to (not including) financing — reused, never reimplemented.
    revenue_schedule = compute_revenue_schedule(plan.revenue, plan.horizon_months)
    operating = compute_operating_costs(revenue_schedule, plan.operating_costs)
    base_cogs = q_money(revenue_schedule.base_monthly_revenue_inr * operating.cogs_pct)
    working_capital_result = compute_working_capital(
        plan.working_capital,
        monthly_revenue_inr=revenue_schedule.base_monthly_revenue_inr,
        monthly_cogs_inr=base_cogs,
        monthly_fixed_opex_inr=operating.fixed_opex_monthly_inr,
        cfg=fin_cfg,
    )
    project_cost_result = compute_project_cost(
        plan.project_cost, working_capital_result, cfg=fin_cfg
    )
    project_cost_inr = project_cost_result.project_cost_inr

    findings: list[StructureFinding] = []

    if cfg.min_project_cost_inr is not None and project_cost_inr < cfg.min_project_cost_inr:
        findings.append(
            StructureFinding(
                code="project_cost_below_floor",
                message=(
                    f"Project cost (Rs {project_cost_inr}) is below {cfg.scheme_name}'s "
                    f"declared project-cost floor (Rs {cfg.min_project_cost_inr})."
                ),
                evidence=[
                    StructureEvidenceRef(
                        source="cost",
                        field="project_cost.project_cost_inr",
                        value=float(project_cost_inr),
                        compared_to=float(cfg.min_project_cost_inr),
                    )
                ],
            )
        )
    if cfg.max_project_cost_inr is not None and project_cost_inr > cfg.max_project_cost_inr:
        findings.append(
            StructureFinding(
                code="project_cost_above_ceiling",
                message=(
                    f"Project cost (Rs {project_cost_inr}) is above {cfg.scheme_name}'s "
                    f"declared project-cost ceiling (Rs {cfg.max_project_cost_inr})."
                ),
                evidence=[
                    StructureEvidenceRef(
                        source="cost",
                        field="project_cost.project_cost_inr",
                        value=float(project_cost_inr),
                        compared_to=float(cfg.max_project_cost_inr),
                    )
                ],
            )
        )

    required_margin = q_money(project_cost_inr * cfg.promoter_contribution_pct)

    stated_cash: Decimal | None = None
    if plan.profile.liquid_cash_inr is not None:
        stated_cash = rupees(plan.profile.liquid_cash_inr)

    margin_shortfall: Decimal | None = None
    if stated_cash is None:
        findings.append(
            StructureFinding(
                code="liquid_cash_unknown_for_margin_check",
                message=(
                    f"The required promoter margin (Rs {required_margin}) could not be "
                    "checked against liquid cash — no liquid cash figure has been stated."
                ),
                evidence=[
                    StructureEvidenceRef(
                        source="config",
                        field="promoter_contribution_pct",
                        value=float(cfg.promoter_contribution_pct),
                    )
                ],
            )
        )
    else:
        margin_shortfall = max(_ZERO, q_money(required_margin - stated_cash))
        if margin_shortfall > 0:
            findings.append(
                StructureFinding(
                    code="margin_shortfall_against_liquid_cash",
                    message=(
                        f"The required promoter margin (Rs {required_margin}) exceeds "
                        f"stated liquid cash (Rs {stated_cash}) by Rs {margin_shortfall}."
                    ),
                    evidence=[
                        StructureEvidenceRef(
                            source="profile",
                            field="liquid_cash_inr",
                            value=float(stated_cash),
                            compared_to=float(required_margin),
                        )
                    ],
                )
            )

    indicated_loan = q_money(project_cost_inr * cfg.loan_pct)
    loan_clipped = False
    if cfg.max_loan_inr is not None and indicated_loan > cfg.max_loan_inr:
        loan_clipped = True
        findings.append(
            StructureFinding(
                code="loan_clipped_by_ceiling",
                message=(
                    f"The indicated loan (Rs {indicated_loan}) exceeds {cfg.scheme_name}'s "
                    f"declared maximum loan (Rs {cfg.max_loan_inr}); capped there. Any "
                    "remaining gap between project cost and committed funding will appear "
                    "as this plan's financial-assessment capital gap."
                ),
                evidence=[
                    StructureEvidenceRef(
                        source="cost",
                        field="indicated_loan_inr",
                        value=float(indicated_loan),
                        compared_to=float(cfg.max_loan_inr),
                    )
                ],
            )
        )
        indicated_loan = cfg.max_loan_inr

    loan_terms = _build_loan_terms(cfg, indicated_loan_inr=indicated_loan)
    warnings: list[str] = []
    if loan_terms is None:
        warnings.append(
            f"{cfg.scheme_name} does not declare a full interest rate/tenure/moratorium; "
            "only the margin/loan split was derived, not a usable LoanTerms"
        )

    return SchemeStructureResult(
        status=SchemeStructureStatus.STRUCTURED,
        scheme_name=cfg.scheme_name,
        category=plan.category,
        project_cost_inr=project_cost_inr,
        required_promoter_margin_inr=required_margin,
        stated_liquid_cash_inr=stated_cash,
        margin_shortfall_inr=margin_shortfall,
        indicated_loan_inr=indicated_loan,
        loan_clipped_by_ceiling=loan_clipped,
        loan_terms=loan_terms,
        findings=findings,
        caveats=list(_STRUCTURE_CAVEATS),
        config=cfg,
        warnings=warnings,
    )


def apply_structure(
    plan: FinancialPlanInput, structure: SchemeStructureResult
) -> FinancialPlanInput:
    """Write `structure.loan_terms` onto `plan.financing.loan` — but ONLY
    when the plan does not already carry a loan. A user-stated or
    Phase-5-sourced `LoanTerms` is never overwritten (the same "existing
    value of any kind always wins" rule `knowledge/plan_binding.py` follows).
    Pure `model_copy`; never mutates `plan`."""
    if structure.loan_terms is None or plan.financing.loan is not None:
        return plan
    new_financing = plan.financing.model_copy(update={"loan": structure.loan_terms})
    return plan.model_copy(update={"financing": new_financing})


__all__ = ["apply_structure", "structure_financing"]
