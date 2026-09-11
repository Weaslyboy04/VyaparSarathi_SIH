"""`knowledge/plan_binding.py` — the Phase 4 integration seam (CLAUDE.md §14,
§15, §18, §23, §30). Pure & offline; builds resolutions by hand rather than
through the loader, to isolate the binder from corpus-loading concerns."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.knowledge.plan_binding import bind_sourced_inputs, build_loan_terms
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.knowledge import ChunkLocator, Jurisdiction, JurisdictionLevel, SourceTier
from vyaparsarathi.models.parameters import (
    Applicability,
    FinanceKnowledgeEvidence,
    ParameterName,
    ParameterQuery,
    ParameterResolution,
    ResolutionStatus,
    SourcedParameter,
    ValueNormalization,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_RETRIEVED_AT = datetime(2026, 1, 15, tzinfo=UTC)
_AS_OF = date(2026, 1, 1)


def _param(name: ParameterName, value, unit: Unit, *, value_token: str, normalization, quote=None):
    return SourcedParameter(
        parameter_id=f"doc-1:{name.value}:1",
        name=name,
        value=value,
        unit=unit,
        value_token=value_token,
        normalization=normalization,
        evidence_quote=quote or f"The value shall be {value_token}.",
        document_id="doc-1",
        chunk_id="doc-1#s1",
        locator=ChunkLocator(section="1"),
        tier=SourceTier.GOVT_PRIMARY,
        applicability=Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar")
        ),
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )


def _resolved(chosen: SourcedParameter, *, confidence: float = 0.8) -> ParameterResolution:
    return ParameterResolution(
        name=chosen.name,
        status=ResolutionStatus.RESOLVED,
        chosen=chosen,
        source=f"knowledge:{chosen.document_id}",
        source_ref=f"{chosen.document_id}#{chosen.locator.as_ref()}",
        retrieved_at=_RETRIEVED_AT,
        confidence=confidence,
    )


def _unresolved(name: ParameterName, status: ResolutionStatus) -> ParameterResolution:
    return ParameterResolution(name=name, status=status)


def _evidence(*resolutions: ParameterResolution) -> FinanceKnowledgeEvidence:
    return FinanceKnowledgeEvidence(
        query=ParameterQuery(names=tuple(r.name for r in resolutions), as_of=_AS_OF),
        resolutions=list(resolutions),
        acquired_at=_RETRIEVED_AT,
    )


def _assumed(value, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.ASSUMED, rationale="t", source="config:t"
    )


def _provided(value, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _plan(**overrides) -> FinancialPlanInput:
    fitout_line = CostLine(
        label="fit-out", kind=CostLineKind.CIVIL_WORK, amount=_assumed(200_000, Unit.INR)
    )
    base = {
        "category": C.GROCERY,
        "profile": EntrepreneurProfile(liquid_cash_inr=650_000, proposed_category=C.GROCERY),
        "project_cost": ProjectCostInput(lines=[fitout_line]),
        "working_capital": WorkingCapitalInput(),
        "revenue": RevenueInput(monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH)),
        "operating_costs": OperatingCostInput(
            fixed_lines=[OpexLine(label="rent", amount=_assumed(5_000, Unit.INR_PER_MONTH))]
        ),
        "financing": FinancingInput(),
        "horizon_months": 24,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


# ======================================================================
# licence fee -> a new CostLine
# ======================================================================


def test_licence_fee_becomes_a_new_cost_line() -> None:
    fee = _param(
        ParameterName.LICENCE_FEE_INR,
        Decimal("500"),
        Unit.INR,
        value_token="Rs. 500",
        normalization=ValueNormalization.AS_STATED,
    )
    evidence = _evidence(_resolved(fee))
    result = bind_sourced_inputs(_plan(), evidence)

    licence_lines = [
        line for line in result.plan.project_cost.lines if line.kind is CostLineKind.LICENCE
    ]
    assert len(licence_lines) == 1
    assert licence_lines[0].amount.value == Decimal("500")
    assert licence_lines[0].amount.kind is InputKind.SOURCED
    assert licence_lines[0].amount.source == "knowledge:doc-1"
    assert licence_lines[0].amount.source_ref == "doc-1#s1"
    assert licence_lines[0].amount.retrieved_at == _RETRIEVED_AT
    assert licence_lines[0].amount.confidence == 0.8
    assert any(b.name is ParameterName.LICENCE_FEE_INR for b in result.bound)


def test_licence_fee_does_not_disturb_existing_cost_lines() -> None:
    fee = _param(
        ParameterName.LICENCE_FEE_INR,
        Decimal("500"),
        Unit.INR,
        value_token="Rs. 500",
        normalization=ValueNormalization.AS_STATED,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(fee)))
    assert len(result.plan.project_cost.lines) == 2  # the original fit-out line + the new one


# ======================================================================
# the unit trap — promoter_margin_pct must never bind
# ======================================================================


def test_promoter_margin_pct_never_binds_regardless_of_status() -> None:
    margin = _param(
        ParameterName.PROMOTER_MARGIN_PCT,
        Decimal("0.1"),
        Unit.RATIO,
        value_token="10%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(margin)))
    assert result.plan.financing.declared_margin_requirement is None
    unbound = {u.name: u for u in result.unbound}
    assert ParameterName.PROMOTER_MARGIN_PCT in unbound
    assert "rupees" in unbound[ParameterName.PROMOTER_MARGIN_PCT].reason


def test_promoter_margin_not_resolved_is_not_reported_as_unbound() -> None:
    # Only a RESOLVED-but-unbindable margin is worth flagging; NO_EVIDENCE is
    # already visible via the resolution itself.
    unresolved = _unresolved(ParameterName.PROMOTER_MARGIN_PCT, ResolutionStatus.NO_EVIDENCE)
    result = bind_sourced_inputs(_plan(), _evidence(unresolved))
    assert not any(u.name is ParameterName.PROMOTER_MARGIN_PCT for u in result.unbound)


def test_subsidy_and_loan_ceiling_and_security_deposit_never_bind() -> None:
    subsidy = _param(
        ParameterName.SUBSIDY_PCT,
        Decimal("0.15"),
        Unit.RATIO,
        value_token="15%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    ceiling = _param(
        ParameterName.LOAN_CEILING_INR,
        Decimal("500000"),
        Unit.INR,
        value_token="Rs. 500000",
        normalization=ValueNormalization.AS_STATED,
    )
    deposit = _param(
        ParameterName.SECURITY_DEPOSIT_MONTHS,
        2,
        Unit.MONTHS,
        value_token="2",
        normalization=ValueNormalization.AS_STATED,
    )
    result = bind_sourced_inputs(
        _plan(), _evidence(_resolved(subsidy), _resolved(ceiling), _resolved(deposit))
    )
    assert result.plan == _plan()  # nothing at all changed
    unbound_names = {u.name for u in result.unbound}
    assert unbound_names == {
        ParameterName.SUBSIDY_PCT,
        ParameterName.LOAN_CEILING_INR,
        ParameterName.SECURITY_DEPOSIT_MONTHS,
    }


# ======================================================================
# never overwrite an existing value, whatever its kind
# ======================================================================


def test_user_provided_inventory_days_is_never_overwritten() -> None:
    inv = _param(
        ParameterName.INVENTORY_DAYS,
        45,
        Unit.DAYS,
        value_token="45 days",
        normalization=ValueNormalization.AS_STATED,
    )
    plan = _plan(working_capital=WorkingCapitalInput(inventory_days=_provided(20, Unit.DAYS)))
    result = bind_sourced_inputs(plan, _evidence(_resolved(inv)))
    assert result.plan.working_capital.inventory_days.value == 20
    assert result.plan.working_capital.inventory_days.kind is InputKind.USER_PROVIDED
    assert any(u.name is ParameterName.INVENTORY_DAYS for u in result.unbound)


def test_assumed_inventory_days_is_also_never_overwritten() -> None:
    inv = _param(
        ParameterName.INVENTORY_DAYS,
        45,
        Unit.DAYS,
        value_token="45 days",
        normalization=ValueNormalization.AS_STATED,
    )
    plan = _plan(working_capital=WorkingCapitalInput(inventory_days=_assumed(20, Unit.DAYS)))
    result = bind_sourced_inputs(plan, _evidence(_resolved(inv)))
    assert result.plan.working_capital.inventory_days.value == 20
    assert result.plan.working_capital.inventory_days.kind is InputKind.ASSUMED


def test_unset_inventory_days_does_bind() -> None:
    inv = _param(
        ParameterName.INVENTORY_DAYS,
        45,
        Unit.DAYS,
        value_token="45 days",
        normalization=ValueNormalization.AS_STATED,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(inv)))
    assert result.plan.working_capital.inventory_days.value == 45
    assert result.plan.working_capital.inventory_days.kind is InputKind.SOURCED


# ======================================================================
# margin-driver mutual exclusivity (cogs_pct XOR gross_margin_pct)
# ======================================================================


def test_gross_margin_pct_binds_when_no_margin_driver_is_set() -> None:
    margin = _param(
        ParameterName.GROSS_MARGIN_PCT,
        Decimal("0.12"),
        Unit.RATIO,
        value_token="12%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(margin)))
    assert result.plan.operating_costs.gross_margin_pct.value == Decimal("0.12")
    assert result.plan.operating_costs.cogs_pct is None


def test_gross_margin_pct_does_not_bind_when_cogs_pct_already_set() -> None:
    margin = _param(
        ParameterName.GROSS_MARGIN_PCT,
        Decimal("0.12"),
        Unit.RATIO,
        value_token="12%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    plan = _plan(
        operating_costs=OperatingCostInput(
            cogs_pct=_provided(Decimal("0.7"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(5_000, Unit.INR_PER_MONTH))],
        )
    )
    result = bind_sourced_inputs(plan, _evidence(_resolved(margin)))
    assert result.plan.operating_costs.gross_margin_pct is None
    assert result.plan.operating_costs.cogs_pct.value == Decimal("0.7")
    assert any(u.name is ParameterName.GROSS_MARGIN_PCT for u in result.unbound)


def test_cogs_pct_and_gross_margin_pct_both_resolved_binds_only_one() -> None:
    cogs = _param(
        ParameterName.COGS_PCT,
        Decimal("0.88"),
        Unit.RATIO,
        value_token="88%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    margin = _param(
        ParameterName.GROSS_MARGIN_PCT,
        Decimal("0.12"),
        Unit.RATIO,
        value_token="12%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(cogs), _resolved(margin)))
    # exactly one of the two mutually-exclusive fields ends up set
    set_fields = [
        f
        for f in ("cogs_pct", "gross_margin_pct")
        if getattr(result.plan.operating_costs, f) is not None
    ]
    assert len(set_fields) == 1
    assert len(result.bound) == 1
    assert len(result.unbound) == 1


# ======================================================================
# non-RESOLVED statuses leave everything untouched
# ======================================================================


@pytest.mark.parametrize(
    "status",
    [
        ResolutionStatus.NO_EVIDENCE,
        ResolutionStatus.CONFLICTING,
        ResolutionStatus.STALE_ONLY,
        ResolutionStatus.CONDITIONS_UNRESOLVED,
    ],
)
def test_non_resolved_status_leaves_the_plan_untouched(status: ResolutionStatus) -> None:
    plan = _plan()
    unresolved = _unresolved(ParameterName.LICENCE_FEE_INR, status)
    result = bind_sourced_inputs(plan, _evidence(unresolved))
    assert result.plan == plan
    assert result.bound == ()


# ======================================================================
# structural guards: unit mismatch, missing retrieved_at
# ======================================================================


def test_unit_mismatch_is_rejected_not_coerced() -> None:
    # A LICENCE_FEE_INR row that somehow carries the wrong unit (defence in
    # depth against a malformed registry row slipping past the loader).
    wrong_unit = _param(
        ParameterName.LICENCE_FEE_INR,
        Decimal("0.5"),
        Unit.RATIO,
        value_token="50%",
        normalization=ValueNormalization.PERCENT_TO_RATIO,
    )
    result = bind_sourced_inputs(_plan(), _evidence(_resolved(wrong_unit)))
    assert not any(line.kind is CostLineKind.LICENCE for line in result.plan.project_cost.lines)
    assert any(
        u.name is ParameterName.LICENCE_FEE_INR and "unit" in u.reason for u in result.unbound
    )


def test_missing_retrieved_at_is_rejected_not_defaulted() -> None:
    fee = _param(
        ParameterName.LICENCE_FEE_INR,
        Decimal("500"),
        Unit.INR,
        value_token="Rs. 500",
        normalization=ValueNormalization.AS_STATED,
    )
    resolution = ParameterResolution(
        name=ParameterName.LICENCE_FEE_INR,
        status=ResolutionStatus.RESOLVED,
        chosen=fee,
        source="knowledge:doc-1",
        source_ref="doc-1#s1",
        retrieved_at=None,  # e.g. no DocumentRecord was available to the resolver
        confidence=0.8,
    )
    result = bind_sourced_inputs(_plan(), _evidence(resolution))
    assert not any(line.kind is CostLineKind.LICENCE for line in result.plan.project_cost.lines)
    assert any(
        u.name is ParameterName.LICENCE_FEE_INR and "retrieved_at" in u.reason
        for u in result.unbound
    )


# ======================================================================
# build_loan_terms — all-or-nothing
# ======================================================================


def _rate() -> SourcedParameter:
    return _param(
        ParameterName.INTEREST_RATE_PCT,
        Decimal("11.0"),
        Unit.PERCENT_PER_ANNUM,
        value_token="11.0%",
        normalization=ValueNormalization.PERCENT_AS_ANNUAL_RATE,
    )


def _tenure() -> SourcedParameter:
    return _param(
        ParameterName.LOAN_TENURE_MONTHS,
        60,
        Unit.MONTHS,
        value_token="60",
        normalization=ValueNormalization.AS_STATED,
    )


def _moratorium() -> SourcedParameter:
    return _param(
        ParameterName.MORATORIUM_MONTHS,
        6,
        Unit.MONTHS,
        value_token="6",
        normalization=ValueNormalization.AS_STATED,
    )


def test_build_loan_terms_succeeds_when_all_three_resolve() -> None:
    evidence = _evidence(_resolved(_rate()), _resolved(_tenure()), _resolved(_moratorium()))
    loan, unbound = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    assert loan is not None
    assert loan.interest_rate_pct.value == Decimal("11.0")
    assert loan.interest_rate_pct.kind is InputKind.SOURCED
    assert loan.tenure_months.value == 60
    assert loan.moratorium_months.value == 6
    assert loan.principal_requested.kind is InputKind.USER_PROVIDED
    assert unbound == ()


@pytest.mark.parametrize("missing", ["rate", "tenure", "moratorium"])
def test_build_loan_terms_returns_none_when_any_one_is_missing(missing: str) -> None:
    parts = {
        "rate": _resolved(_rate()),
        "tenure": _resolved(_tenure()),
        "moratorium": _resolved(_moratorium()),
    }
    del parts[missing]
    evidence = _evidence(*parts.values())
    loan, unbound = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    assert loan is None
    assert len(unbound) == 1


def test_build_loan_terms_returns_none_when_nothing_resolves() -> None:
    evidence = _evidence(_unresolved(ParameterName.INTEREST_RATE_PCT, ResolutionStatus.NO_EVIDENCE))
    loan, unbound = build_loan_terms(
        evidence,
        principal=_provided(80_000, Unit.INR),
        treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    assert loan is None
    assert len(unbound) == 3


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
