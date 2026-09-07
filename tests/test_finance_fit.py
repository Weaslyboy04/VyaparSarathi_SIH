"""The Phase 3 <-> Phase 4 bridge (CLAUDE.md §12, §15). Pure & offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.fit import to_financial_fit
from vyaparsarathi.market.opportunity import score_opportunities
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C

from .test_opportunity_scoring import _demand, _discovery, _evidence, _profile

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.ASSUMED, rationale="t", source="config:t"
    )


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _sourced(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="declared_margin_requirement",
        value=value,
        unit=unit,
        kind=InputKind.SOURCED,
        source="scheme:PMEGP",
        source_ref="s.4.2",
        retrieved_at=_NOW,
    )


def _plan(**overrides: object) -> FinancialPlanInput:
    base = {
        "category": C.GROCERY,
        "profile": EntrepreneurProfile(),
        "project_cost": ProjectCostInput(
            lines=[
                CostLine(
                    label="fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(100_000, Unit.INR),
                )
            ]
        ),
        "working_capital": WorkingCapitalInput(),
        "revenue": RevenueInput(monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH)),
        "operating_costs": OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(20_000, Unit.INR_PER_MONTH))],
        ),
        "financing": FinancingInput(promoter_cash_contribution=_provided(200_000, Unit.INR)),
        "horizon_months": 12,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


# ======================================================================
# status -> feasible tri-state mapping
# ======================================================================


def test_feasible_status_maps_to_true() -> None:
    res = assess_financials(
        _plan(
            project_cost=ProjectCostInput(
                lines=[
                    CostLine(
                        label="fit-out",
                        kind=CostLineKind.CIVIL_WORK,
                        amount=_assumed(50_000, Unit.INR),
                    )
                ]
            ),
            financing=FinancingInput(promoter_cash_contribution=_provided(250_000, Unit.INR)),
        )
    )
    fit = to_financial_fit(res)
    assert fit.feasible is True


def test_insufficient_evidence_maps_to_none() -> None:
    res = assess_financials(_plan(revenue=RevenueInput()))
    fit = to_financial_fit(res)
    assert fit.feasible is None


def test_financing_gap_maps_to_false() -> None:
    res = assess_financials(
        _plan(
            project_cost=ProjectCostInput(
                lines=[
                    CostLine(
                        label="fit-out",
                        kind=CostLineKind.CIVIL_WORK,
                        amount=_assumed(5_000_000, Unit.INR),
                    )
                ]
            ),
            financing=FinancingInput(promoter_cash_contribution=_provided(1_000, Unit.INR)),
        )
    )
    fit = to_financial_fit(res)
    assert fit.feasible is False


def test_category_is_carried_from_the_plan() -> None:
    res = assess_financials(_plan())
    fit = to_financial_fit(res)
    assert fit.category is C.GROCERY


# ======================================================================
# capital_gap_inr / required_promoter_margin_inr
# ======================================================================


def test_capital_gap_is_none_when_evidence_is_insufficient() -> None:
    res = assess_financials(_plan(revenue=RevenueInput()))
    fit = to_financial_fit(res)
    assert fit.capital_gap_inr is None


def test_required_promoter_margin_is_none_without_a_declared_requirement() -> None:
    res = assess_financials(_plan())
    fit = to_financial_fit(res)
    assert fit.required_promoter_margin_inr is None


def test_required_promoter_margin_is_populated_from_a_sourced_declaration() -> None:
    res = assess_financials(
        _plan(
            financing=FinancingInput(
                promoter_cash_contribution=_provided(200_000, Unit.INR),
                declared_margin_requirement=_sourced(50_000, Unit.INR),
            )
        )
    )
    fit = to_financial_fit(res)
    assert fit.required_promoter_margin_inr == 50_000


def test_required_promoter_margin_can_never_come_from_an_assumed_input() -> None:
    # the model itself forbids constructing this -- confirms the seam has no
    # back door.
    with pytest.raises(ValueError, match="ASSUMED"):
        FinancingInput(declared_margin_requirement=_assumed(50_000, Unit.INR))


# ======================================================================
# Phase 3 integration: scores are untouched
# ======================================================================


def test_financial_fit_input_does_not_change_phase_3_scores() -> None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand())
    profile = _profile(proposed_category=C.GROCERY, liquid_cash_inr=400_000)
    plain = score_opportunities(ev, profile)

    fin_res = assess_financials(_plan())
    ffi = to_financial_fit(fin_res)
    with_fi = score_opportunities(ev, profile, financial_fit={C.GROCERY: ffi})

    p = {c.category: c.opportunity_score for c in plain.candidates}
    f = {c.category: c.opportunity_score for c in with_fi.candidates}
    assert p == f

    grocery = next(c for c in with_fi.candidates if c.category is C.GROCERY)
    assert any("financial status" in w for w in grocery.warnings)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
