"""The Phase 4 integration seam (CLAUDE.md §14, §15, §18, §23, §30).

Binds `RESOLVED` :class:`~vyaparsarathi.models.parameters.ParameterResolution`\\ s
from a :class:`~vyaparsarathi.models.parameters.FinanceKnowledgeEvidence` into
a `FinancialPlanInput`, producing `FinancialInput(kind=SOURCED, ...)` values —
the exact seam `docs/phase-4.md` names: *"Phase 5 injects
`FinancialInput(kind=SOURCED, source_ref=…)` values into `FinancingInput` /
`OperatingCostInput` — no engine module changes required."*

Lives in `knowledge/`, never `finance/`: `tests/test_finance_purity.py`
``rglob``\\ s ``finance/**/*.py`` and would sweep in any file placed there.
This module is the **only** place in the Phase 5 package that constructs a
`FinancialInput` or imports `vyaparsarathi.models.finance`'s plan models — the
mirror of `finance/fit.py` being the only module in `finance/` allowed to
import `vyaparsarathi.market`.

## Binding rules

* Only a `RESOLVED` resolution binds; `NO_EVIDENCE`, `CONFLICTING`,
  `STALE_ONLY` and `CONDITIONS_UNRESOLVED` leave the target field alone.
* A resolved value's `unit` must equal `PARAMETER_SPEC[name].unit`. A
  mismatch is a hard reject (`UnboundParameter`), never a silent coercion.
* A field that already carries a value — of **any** kind
  (`user_provided`/`assumed`/an earlier `sourced` value) — is never
  overwritten. The entrepreneur's own statement, or a caller's earlier
  assumption, always wins; a difference is only noted.
* `FinancialInput.retrieved_at` comes from the chosen row's source
  `DocumentRecord.retrieved_at` (via `ParameterResolution.retrieved_at`),
  never the acquisition run's own clock — the whole binding path stays
  clock-free, so a bound plan's assessment is byte-identical across repeat
  runs.

## What actually binds, and what structurally cannot (yet)

Auditing every `ParameterName` against Phase 4's real field shapes
(`models/finance.py`) — not every resolved fact has somewhere to go:

* `interest_rate_pct`, `loan_tenure_months`, `moratorium_months` -> the
  three `LoanTerms` fields, via `build_loan_terms` (a bundle — see below),
  never bound individually.
* `licence_fee_inr` -> a new `CostLine(kind=LICENCE)`. A statutory fee is
  genuinely an absolute rupee cost line.
* `gross_margin_pct` -> `OperatingCostInput.gross_margin_pct`, and
  `cogs_pct` -> `OperatingCostInput.cogs_pct`: a direct unit match, bound
  only when the other of the pair isn't already set (they are mutually
  exclusive on the plan itself).
* `inventory_days` -> `WorkingCapitalInput.inventory_days`: a direct match.
* `promoter_margin_pct` -> **nowhere**. `FinancingInput.
  declared_margin_requirement` is consumed as absolute rupees by
  `finance/fit.py`; converting a stated percentage needs
  `project_cost x pct`, which is Phase 4 arithmetic this phase must not
  perform.
* `subsidy_pct` -> **nowhere**, the identical percentage-needs-project-cost
  problem; no Phase 4 field takes a capital-subsidy percentage directly.
* `loan_ceiling_inr` -> **nowhere**. A scheme's maximum is a *validation*
  fact ("is the ask within the ceiling?"), not a plan driver —
  `LoanTerms.principal_requested` is what the entrepreneur is asking for,
  never a ceiling.
* `security_deposit_months` -> **nowhere**. A `CostLine.amount` must carry
  `Unit.INR`; converting months of rent/EMI into rupees needs figures
  (rent, EMI) this phase does not itself supply.

The four "nowhere" parameters are not a bug: `docs/phase-4.md` deliberately refused
to let this engine invent project-cost-scaled figures, and Phase 5 inherits
that refusal rather than working around it with an ad hoc conversion. Each
resolved-but-unbindable parameter is still fully visible on
`FinanceKnowledgeEvidence` (for Phase 6/8 narrative and DPR use) and is
reported as an `UnboundParameter` naming exactly why. See
`docs/phase-5.md`'s unsupported-parameter register.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.parameter_spec import PARAMETER_SPEC
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
)
from vyaparsarathi.models.parameters import (
    FinanceKnowledgeEvidence,
    ParameterName,
    ParameterResolution,
    ResolutionStatus,
)

# Names bound individually by bind_sourced_inputs(); loan terms are bound only
# as a bundle by build_loan_terms(), and never touched here.
_LOAN_BUNDLE_NAMES = frozenset(
    {
        ParameterName.INTEREST_RATE_PCT,
        ParameterName.LOAN_TENURE_MONTHS,
        ParameterName.MORATORIUM_MONTHS,
    }
)

_MARGIN_PCT_REASON = (
    "target field FinancingInput.declared_margin_requirement is consumed as absolute "
    "rupees (finance/fit.py); converting a stated percentage requires project cost, "
    "which is a Phase 4 calculation this phase does not perform. The resolved value "
    "is carried on FinanceKnowledgeEvidence for narrative/DPR use, never bound here."
)
_SUBSIDY_PCT_REASON = (
    "no FinancialPlanInput field accepts a capital-subsidy percentage directly; "
    "applying it needs project cost, which is a Phase 4 calculation this phase does "
    "not perform. The resolved value is carried on FinanceKnowledgeEvidence only."
)
_LOAN_CEILING_REASON = (
    "a scheme's loan ceiling is a validation fact (is the requested amount within "
    "it?), not a plan driver — LoanTerms.principal_requested is what the entrepreneur "
    "is asking for, never a ceiling. The resolved value is carried on "
    "FinanceKnowledgeEvidence only."
)
_SECURITY_DEPOSIT_REASON = (
    "a CostLine.amount must carry Unit.INR; converting a deposit stated in months of "
    "rent/EMI into rupees needs figures this phase does not itself supply. The "
    "resolved value is carried on FinanceKnowledgeEvidence only."
)
_UNBINDABLE_REASONS: dict[ParameterName, str] = {
    ParameterName.PROMOTER_MARGIN_PCT: _MARGIN_PCT_REASON,
    ParameterName.SUBSIDY_PCT: _SUBSIDY_PCT_REASON,
    ParameterName.LOAN_CEILING_INR: _LOAN_CEILING_REASON,
    ParameterName.SECURITY_DEPOSIT_MONTHS: _SECURITY_DEPOSIT_REASON,
}


class BoundParameter(BaseModel):
    """One `FinancialInput` this run actually wrote into the plan."""

    model_config = ConfigDict(extra="forbid")

    name: ParameterName
    target_field: str
    financial_input: FinancialInput
    confidence: float | None = None


class UnboundParameter(BaseModel):
    """One resolved-or-not parameter this run did **not** write into the
    plan, and exactly why — a missing binding is reported, never silently
    dropped."""

    model_config = ConfigDict(extra="forbid")

    name: ParameterName
    status: ResolutionStatus
    reason: str


class BoundPlan(BaseModel):
    """The result of `bind_sourced_inputs`: the (possibly updated) plan, plus
    a full account of what did and did not bind."""

    model_config = ConfigDict(extra="forbid")

    plan: FinancialPlanInput
    bound: tuple[BoundParameter, ...] = ()
    unbound: tuple[UnboundParameter, ...] = ()
    warnings: list[str] = Field(default_factory=list)


def _build_financial_input(resolution: ParameterResolution) -> tuple[FinancialInput | None, str]:
    """`(financial_input, "")` on success, or `(None, reason)` when a
    structural check fails — a unit mismatch against `PARAMETER_SPEC`, or a
    missing `retrieved_at` (no source `DocumentRecord` was available to the
    resolver, so `InputKind.SOURCED`'s own required field cannot be filled)."""
    chosen = resolution.chosen
    assert chosen is not None  # guarded by the RESOLVED check at every call site
    spec = PARAMETER_SPEC.get(resolution.name)
    if spec is not None and chosen.unit != spec.unit:
        return None, (
            f"resolved value carries unit {chosen.unit.value}, but this parameter's "
            f"target expects {spec.unit.value} — rejected rather than coerced"
        )
    if resolution.retrieved_at is None:
        return None, "no DocumentRecord.retrieved_at was available for the resolved source"
    fi = FinancialInput(
        label=resolution.name.value,
        value=chosen.value,
        unit=chosen.unit,
        kind=InputKind.SOURCED,
        source=resolution.source or f"knowledge:{chosen.document_id}",
        source_ref=resolution.source_ref or f"{chosen.document_id}#{chosen.locator.as_ref()}",
        retrieved_at=resolution.retrieved_at,
        confidence=resolution.confidence,
    )
    return fi, ""


def bind_sourced_inputs(
    plan: FinancialPlanInput,
    evidence: FinanceKnowledgeEvidence,
    *,
    cfg: KnowledgeConfig = DEFAULT_KNOWLEDGE_CONFIG,
) -> BoundPlan:
    """Bind every bindable `RESOLVED` parameter in `evidence` into a copy of
    `plan`. Never mutates `plan` — every change is a `model_copy(update=...)`,
    following `finance/stress.py`'s transform style. `cfg` is accepted for a
    consistent call shape with the rest of Phase 5 (this function currently
    reads no `KnowledgeConfig` field directly; unit/overwrite rules are
    structural, not tunable)."""
    del cfg  # accepted for interface consistency; nothing here is tunable yet

    project_cost = plan.project_cost
    working_capital = plan.working_capital
    operating_costs = plan.operating_costs

    bound: list[BoundParameter] = []
    unbound: list[UnboundParameter] = []
    margin_driver_claimed = operating_costs.gross_margin_pct is not None or (
        operating_costs.cogs_pct is not None
    )

    for resolution in evidence.resolutions:
        name = resolution.name

        if name in _LOAN_BUNDLE_NAMES:
            continue  # handled only by build_loan_terms(), as a bundle

        if name in _UNBINDABLE_REASONS:
            if resolution.status is ResolutionStatus.RESOLVED:
                unbound.append(
                    UnboundParameter(
                        name=name, status=resolution.status, reason=_UNBINDABLE_REASONS[name]
                    )
                )
            continue

        if resolution.status is not ResolutionStatus.RESOLVED:
            continue  # nothing to bind; not itself worth an UnboundParameter

        if name is ParameterName.LICENCE_FEE_INR:
            fi, reason = _build_financial_input(resolution)
            if fi is None:
                unbound.append(UnboundParameter(name=name, status=resolution.status, reason=reason))
                continue
            new_line = CostLine(
                label=f"licence fee ({resolution.chosen.document_id})",  # type: ignore[union-attr]
                kind=CostLineKind.LICENCE,
                amount=fi,
            )
            project_cost = project_cost.model_copy(
                update={"lines": [*project_cost.lines, new_line]}
            )
            bound.append(
                BoundParameter(
                    name=name,
                    target_field="project_cost.lines[]",
                    financial_input=fi,
                    confidence=resolution.confidence,
                )
            )
            continue

        if name is ParameterName.INVENTORY_DAYS:
            if working_capital.inventory_days is not None:
                unbound.append(
                    UnboundParameter(
                        name=name,
                        status=resolution.status,
                        reason="working_capital.inventory_days is already set; not overwritten",
                    )
                )
                continue
            fi, reason = _build_financial_input(resolution)
            if fi is None:
                unbound.append(UnboundParameter(name=name, status=resolution.status, reason=reason))
                continue
            working_capital = working_capital.model_copy(update={"inventory_days": fi})
            bound.append(
                BoundParameter(
                    name=name,
                    target_field="working_capital.inventory_days",
                    financial_input=fi,
                    confidence=resolution.confidence,
                )
            )
            continue

        if name in (ParameterName.GROSS_MARGIN_PCT, ParameterName.COGS_PCT):
            target_field = (
                "operating_costs.gross_margin_pct"
                if name is ParameterName.GROSS_MARGIN_PCT
                else "operating_costs.cogs_pct"
            )
            if margin_driver_claimed:
                unbound.append(
                    UnboundParameter(
                        name=name,
                        status=resolution.status,
                        reason=(
                            "operating_costs already carries a margin driver "
                            "(cogs_pct/gross_margin_pct accept only one); not overwritten"
                        ),
                    )
                )
                continue
            fi, reason = _build_financial_input(resolution)
            if fi is None:
                unbound.append(UnboundParameter(name=name, status=resolution.status, reason=reason))
                continue
            field_name = (
                "gross_margin_pct" if name is ParameterName.GROSS_MARGIN_PCT else "cogs_pct"
            )
            operating_costs = operating_costs.model_copy(update={field_name: fi})
            margin_driver_claimed = True
            bound.append(
                BoundParameter(
                    name=name,
                    target_field=target_field,
                    financial_input=fi,
                    confidence=resolution.confidence,
                )
            )
            continue

        # A ParameterName this module has not been taught to bind (should not
        # happen while PARAMETER_SPEC and this dispatch stay in sync — a
        # future new name lands here as a visible gap, not a silent no-op).
        unbound.append(
            UnboundParameter(
                name=name,
                status=resolution.status,
                reason="knowledge/plan_binding.py has no binding rule for this parameter name",
            )
        )

    new_plan = plan.model_copy(
        update={
            "project_cost": project_cost,
            "working_capital": working_capital,
            "operating_costs": operating_costs,
        }
    )
    return BoundPlan(plan=new_plan, bound=tuple(bound), unbound=tuple(unbound))


def build_loan_terms(
    evidence: FinanceKnowledgeEvidence,
    *,
    principal: FinancialInput,
    treatment: MoratoriumTreatment,
    cfg: KnowledgeConfig = DEFAULT_KNOWLEDGE_CONFIG,
) -> tuple[LoanTerms | None, tuple[UnboundParameter, ...]]:
    """Build a `LoanTerms` from resolved rate/tenure/moratorium, or `None`
    with the reasons why not. `LoanTerms` is all-or-nothing at construction
    (`models/finance.py`), so this never returns a partially-sourced loan —
    a caller falls back to its own `LoanTerms` (or reports
    `INSUFFICIENT_FINANCIAL_EVIDENCE`) exactly as it would without Phase 5.

    `principal` (the amount actually requested) and `treatment` (how the
    sanction handles moratorium interest) are supplied by the caller, never
    retrieved — the amount asked for is the entrepreneur's, and the
    treatment is a fact about a specific sanction, not a generic scheme rule
    this registry models."""
    del cfg  # accepted for interface consistency; nothing here is tunable yet

    unbound: list[UnboundParameter] = []
    resolved_by_name: dict[ParameterName, ParameterResolution] = {
        r.name: r for r in evidence.resolutions if r.name in _LOAN_BUNDLE_NAMES
    }

    inputs_by_name: dict[ParameterName, FinancialInput] = {}
    for name in (
        ParameterName.INTEREST_RATE_PCT,
        ParameterName.LOAN_TENURE_MONTHS,
        ParameterName.MORATORIUM_MONTHS,
    ):
        resolution = resolved_by_name.get(name)
        if resolution is None or resolution.status is not ResolutionStatus.RESOLVED:
            status = resolution.status if resolution is not None else ResolutionStatus.NO_EVIDENCE
            unbound.append(
                UnboundParameter(
                    name=name, status=status, reason="not resolved; LoanTerms cannot be built"
                )
            )
            continue
        fi, reason = _build_financial_input(resolution)
        if fi is None:
            unbound.append(UnboundParameter(name=name, status=resolution.status, reason=reason))
            continue
        inputs_by_name[name] = fi

    if len(inputs_by_name) < 3:
        return None, tuple(unbound)

    loan = LoanTerms(
        principal_requested=principal,
        interest_rate_pct=inputs_by_name[ParameterName.INTEREST_RATE_PCT],
        tenure_months=inputs_by_name[ParameterName.LOAN_TENURE_MONTHS],
        moratorium_months=inputs_by_name[ParameterName.MORATORIUM_MONTHS],
        moratorium_treatment=treatment,
    )
    return loan, tuple(unbound)


__all__ = [
    "BoundParameter",
    "BoundPlan",
    "UnboundParameter",
    "bind_sourced_inputs",
    "build_loan_terms",
]
