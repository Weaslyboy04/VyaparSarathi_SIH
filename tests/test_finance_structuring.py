"""`finance/structuring.py` (CLAUDE.md §12, §13, §14, §15, §30; Tier 1 "SIH
10%/90% Financial Structuring"). Pure & offline.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus
from vyaparsarathi.finance.structuring import apply_structure, structure_financing
from vyaparsarathi.finance.structuring_models import SchemeStructureStatus
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _provided(value: object, unit: Unit, *, label: str = "x") -> FinancialInput:
    return FinancialInput(
        label=label, value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _cfg(**overrides: object) -> SihSchemeConfig:
    base: dict[str, object] = {
        "scheme_name": "Test Declared Structure",
        "promoter_contribution_pct": Decimal("0.10"),
        "loan_pct": Decimal("0.90"),
        "rationale": "SIH26091 problem statement — test fixture value.",
    }
    base.update(overrides)
    return SihSchemeConfig(**base)  # type: ignore[arg-type]


def _plan(*, liquid_cash_inr: int | None = 650_000, **overrides: object) -> FinancialPlanInput:
    base: dict[str, object] = {
        "category": C.GROCERY,
        "profile": EntrepreneurProfile(liquid_cash_inr=liquid_cash_inr),
        "project_cost": ProjectCostInput(
            lines=[
                CostLine(
                    label="startup cost",
                    kind=CostLineKind.EQUIPMENT,
                    amount=_provided(300_000, Unit.INR),
                )
            ]
        ),
        "working_capital": WorkingCapitalInput(),
        "revenue": RevenueInput(monthly_revenue=_provided(40_000, Unit.INR_PER_MONTH)),
        "operating_costs": OperatingCostInput(
            cogs_pct=_provided(Decimal("0.70"), Unit.RATIO),
            fixed_lines=[
                OpexLine(label="other fixed costs", amount=_provided(4_000, Unit.INR_PER_MONTH))
            ],
        ),
        "financing": FinancingInput(),
        "horizon_months": 36,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


def _empty_plan() -> FinancialPlanInput:
    return FinancialPlanInput(
        category=C.GROCERY,
        profile=EntrepreneurProfile(),
        project_cost=ProjectCostInput(),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(),
        operating_costs=OperatingCostInput(),
        financing=FinancingInput(),
        horizon_months=36,
    )


# --- status gates -----------------------------------------------------------


def test_no_scheme_config_is_not_configured_and_derives_nothing() -> None:
    result = structure_financing(_plan(), scheme_cfg=None)
    assert result.status is SchemeStructureStatus.NOT_CONFIGURED
    assert result.project_cost_inr is None
    assert result.required_promoter_margin_inr is None
    assert result.loan_terms is None
    assert any(f.code == "scheme_not_configured" for f in result.findings)


def test_missing_core_drivers_yields_insufficient_evidence() -> None:
    result = structure_financing(_empty_plan(), scheme_cfg=_cfg())
    assert result.status is SchemeStructureStatus.INSUFFICIENT_EVIDENCE
    assert result.missing_core_drivers
    assert result.project_cost_inr is None


# --- the split, to the number ------------------------------------------------


def test_structured_split_is_exact() -> None:
    plan = _plan()
    cfg = _cfg()
    result = structure_financing(plan, scheme_cfg=cfg)
    assert result.status is SchemeStructureStatus.STRUCTURED

    # project cost = capex (300000) + 5% contingency (15000) + net working
    # capital (opex cushion only here: 2 months x 4000 = 8000; no
    # inventory/receivable/payable stated) = 323000
    assert result.project_cost_inr == Decimal("323000.00")
    assert result.required_promoter_margin_inr == Decimal("32300.00")  # 10%
    assert result.indicated_loan_inr == Decimal("290700.00")  # 90%
    assert (
        result.required_promoter_margin_inr + result.indicated_loan_inr == result.project_cost_inr
    )
    assert result.stated_liquid_cash_inr == Decimal("650000")
    assert result.margin_shortfall_inr == Decimal("0.00")
    assert result.loan_clipped_by_ceiling is False


def test_two_runs_are_byte_identical() -> None:
    plan = _plan()
    cfg = _cfg()
    a = structure_financing(plan, scheme_cfg=cfg)
    b = structure_financing(plan, scheme_cfg=cfg)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


# --- boundary findings --------------------------------------------------


def test_project_cost_below_floor_fires() -> None:
    cfg = _cfg(min_project_cost_inr=Decimal("1000000"))
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert any(f.code == "project_cost_below_floor" for f in result.findings)


def test_project_cost_above_ceiling_fires() -> None:
    cfg = _cfg(max_project_cost_inr=Decimal("100000"))
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert any(f.code == "project_cost_above_ceiling" for f in result.findings)


def test_margin_shortfall_against_liquid_cash_fires_and_reports_only() -> None:
    cfg = _cfg()
    plan = _plan(liquid_cash_inr=5_000)  # far below the ~32,300 required margin
    result = structure_financing(plan, scheme_cfg=cfg)
    assert result.status is SchemeStructureStatus.STRUCTURED
    assert result.margin_shortfall_inr is not None
    assert result.margin_shortfall_inr > 0
    finding = next(f for f in result.findings if f.code == "margin_shortfall_against_liquid_cash")
    assert (
        "5000" in finding.message
        or "5,000" in finding.message
        or str(plan.profile.liquid_cash_inr) in finding.message
    )
    # never invented or mutated promoter_cash_contribution
    assert plan.financing.promoter_cash_contribution is None


def test_no_liquid_cash_stated_yields_unknown_margin_check() -> None:
    result = structure_financing(_plan(liquid_cash_inr=None), scheme_cfg=_cfg())
    assert result.status is SchemeStructureStatus.STRUCTURED
    assert result.margin_shortfall_inr is None
    assert result.stated_liquid_cash_inr is None
    assert any(f.code == "liquid_cash_unknown_for_margin_check" for f in result.findings)


def test_loan_clipped_by_ceiling_fires() -> None:
    cfg = _cfg(max_loan_inr=Decimal("50000"))
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert result.loan_clipped_by_ceiling is True
    assert result.indicated_loan_inr == Decimal("50000")
    assert any(f.code == "loan_clipped_by_ceiling" for f in result.findings)


def test_physical_assets_never_count_toward_margin_check() -> None:
    cfg = _cfg()
    plan_no_assets = _plan(liquid_cash_inr=5_000)
    plan_with_assets = _plan(
        liquid_cash_inr=5_000,
        profile=EntrepreneurProfile(
            liquid_cash_inr=5_000, assets={AssetKind.STOREFRONT, AssetKind.EQUIPMENT}
        ),
    )
    a = structure_financing(plan_no_assets, scheme_cfg=cfg)
    b = structure_financing(plan_with_assets, scheme_cfg=cfg)
    assert a.margin_shortfall_inr == b.margin_shortfall_inr
    assert a.required_promoter_margin_inr == b.required_promoter_margin_inr


# --- loan terms provenance ------------------------------------------------


def test_loan_terms_are_all_assumed_config_sourced() -> None:
    cfg = _cfg(
        interest_rate_pct=Decimal("11"),
        tenure_months=60,
        moratorium_months=6,
    )
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert result.loan_terms is not None
    for fi in (
        result.loan_terms.principal_requested,
        result.loan_terms.interest_rate_pct,
        result.loan_terms.tenure_months,
        result.loan_terms.moratorium_months,
    ):
        assert fi.kind is InputKind.ASSUMED
        assert fi.source == "config:sih_scheme"
        assert fi.rationale


def test_no_full_loan_terms_declared_yields_no_loan_terms_object() -> None:
    cfg = _cfg()  # interest/tenure/moratorium all None
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert result.loan_terms is None
    assert result.warnings  # honestly reported, not silently absent


def test_zero_moratorium_forces_none_treatment() -> None:
    cfg = _cfg(
        interest_rate_pct=Decimal("11"),
        tenure_months=60,
        moratorium_months=0,
        moratorium_treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    result = structure_financing(_plan(), scheme_cfg=cfg)
    assert result.loan_terms is not None
    assert result.loan_terms.moratorium_treatment is MoratoriumTreatment.NONE


# --- apply_structure: never overwrites -------------------------------------


def test_apply_structure_writes_loan_when_absent() -> None:
    cfg = _cfg(interest_rate_pct=Decimal("11"), tenure_months=60, moratorium_months=6)
    plan = _plan()
    result = structure_financing(plan, scheme_cfg=cfg)
    new_plan = apply_structure(plan, result)
    assert new_plan.financing.loan is not None
    assert new_plan.financing.loan.principal_requested.value == result.indicated_loan_inr


def test_apply_structure_never_overwrites_an_existing_loan() -> None:
    cfg = _cfg(interest_rate_pct=Decimal("11"), tenure_months=60, moratorium_months=6)
    existing_loan = LoanTerms(
        principal_requested=_provided(999_999, Unit.INR),
        interest_rate_pct=_provided(Decimal("9"), Unit.PERCENT_PER_ANNUM),
        tenure_months=_provided(48, Unit.MONTHS),
        moratorium_months=_provided(3, Unit.MONTHS),
        moratorium_treatment=MoratoriumTreatment.INTEREST_SERVICED,
    )
    plan = _plan(financing=FinancingInput(loan=existing_loan))
    result = structure_financing(plan, scheme_cfg=cfg)
    new_plan = apply_structure(plan, result)
    assert new_plan.financing.loan is existing_loan


def test_apply_structure_is_a_noop_when_structuring_produced_no_loan_terms() -> None:
    plan = _plan()
    result = structure_financing(plan, scheme_cfg=_cfg())  # no rate/tenure/moratorium declared
    new_plan = apply_structure(plan, result)
    assert new_plan == plan


# --- the structured loan actually reaches Phase 4 ---------------------------


def test_structured_loan_reaches_dscr_and_emi() -> None:
    cfg = _cfg(interest_rate_pct=Decimal("11"), tenure_months=60, moratorium_months=6)
    plan = _plan()
    structure = structure_financing(plan, scheme_cfg=cfg)
    structured_plan = apply_structure(plan, structure)
    assessment = assess_financials(structured_plan)
    assert assessment.status is not FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert assessment.debt is not None
    assert assessment.debt.emi_inr is not None
    assert assessment.dscr is not None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
