"""Project cost and working-capital calculation (CLAUDE.md §14, §15). Pure &
offline."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.finance.costs import (
    compute_capital_gap,
    compute_project_cost,
    compute_promoter_contribution_pct,
    compute_working_capital,
)
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG
from vyaparsarathi.models.finance import (
    AssetSpendOffset,
    CostLine,
    CostLineKind,
    FinancialInput,
    InputKind,
    ProjectCostInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import AssetKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _sourced(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x",
        value=value,
        unit=unit,
        kind=InputKind.SOURCED,
        source="scheme:PMEGP",
        source_ref="s.1",
        retrieved_at=_NOW,
    )


def _line(label: str, amount: int, kind: CostLineKind = CostLineKind.EQUIPMENT) -> CostLine:
    return CostLine(label=label, kind=kind, amount=_provided(amount, Unit.INR))


# ======================================================================
# working capital
# ======================================================================


def test_working_capital_derives_inventory_from_days_and_cogs() -> None:
    wc = WorkingCapitalInput(
        inventory_days=_provided(30, Unit.DAYS),
        receivable_days=_provided(0, Unit.DAYS),
        payable_days=_provided(15, Unit.DAYS),
        opex_cushion_months=_provided(2, Unit.MONTHS),
    )
    res = compute_working_capital(
        wc,
        monthly_revenue_inr=Decimal("100000"),
        monthly_cogs_inr=Decimal("60000"),
        monthly_fixed_opex_inr=Decimal("10000"),
    )
    assert res.inventory_requirement_inr == Decimal("60000.00")  # (60000/30)*30
    assert res.payables_inr == Decimal("30000.00")  # (60000/30)*15
    assert res.operating_reserve_inr == Decimal("20000.00")  # 10000*2
    assert res.gross_working_capital_inr == Decimal("80000.00")  # 60000 + 0 + 20000
    assert res.net_working_capital_inr == Decimal("50000.00")  # 80000 - 30000


def test_opening_inventory_overrides_days_based_calculation() -> None:
    wc = WorkingCapitalInput(opening_inventory=_provided(75_000, Unit.INR))
    res = compute_working_capital(
        wc,
        monthly_revenue_inr=Decimal("100000"),
        monthly_cogs_inr=Decimal("60000"),
        monthly_fixed_opex_inr=Decimal("10000"),
    )
    assert res.inventory_requirement_inr == Decimal("75000.00")
    assert res.inventory_days == Decimal("0")
    assert any("opening_inventory" in n for n in res.notes)


def test_missing_working_capital_inputs_fall_back_to_config_with_a_note() -> None:
    res = compute_working_capital(
        WorkingCapitalInput(),
        monthly_revenue_inr=Decimal("100000"),
        monthly_cogs_inr=Decimal("60000"),
        monthly_fixed_opex_inr=Decimal("10000"),
    )
    cfg = DEFAULT_FINANCE_CONFIG
    assert res.inventory_requirement_inr == Decimal("0.00")
    assert res.receivable_days == cfg.receivable_days_default
    assert res.payable_days == cfg.payable_days_default
    assert res.opex_cushion_months == cfg.opex_cushion_months
    assert len(res.notes) >= 4  # inventory, receivable, payable, cushion each note


# ======================================================================
# project cost
# ======================================================================


def test_project_cost_requires_at_least_one_line() -> None:
    with pytest.raises(ValueError, match="at least one"):
        compute_project_cost(ProjectCostInput(), _working_capital_result())


def _working_capital_result():  # helper avoiding an import cycle in this test module
    wc = WorkingCapitalInput()
    return compute_working_capital(
        wc,
        monthly_revenue_inr=Decimal("0"),
        monthly_cogs_inr=Decimal("0"),
        monthly_fixed_opex_inr=Decimal("0"),
    )


def test_project_cost_aggregates_lines_and_applies_contingency_to_capex_only() -> None:
    pc = ProjectCostInput(
        lines=[_line("shop fit-out", 200_000), _line("initial equipment", 100_000)],
        contingency_pct=_provided(Decimal("0.05"), Unit.RATIO),
    )
    wc_result = _working_capital_result()
    res = compute_project_cost(pc, wc_result)
    assert res.capex_subtotal_inr == Decimal("300000.00")
    assert res.contingency_inr == Decimal("15000.00")  # 5% of 300000
    assert res.project_cost_inr == Decimal("315000.00")  # + 0 working capital


def test_asset_offset_reduces_the_named_line() -> None:
    offset = AssetSpendOffset(
        asset=AssetKind.STOREFRONT,
        reduces_line="shop fit-out",
        amount_avoided=_provided(200_000, Unit.INR),
    )
    pc = ProjectCostInput(
        lines=[_line("shop fit-out", 200_000), _line("initial equipment", 100_000)],
        offsets=[offset],
        contingency_pct=_provided(Decimal("0"), Unit.RATIO),
    )
    res = compute_project_cost(pc, _working_capital_result())
    fit_out = next(line for line in res.lines if line.label == "shop fit-out")
    assert fit_out.net_amount_inr == Decimal("0.00")
    assert res.capex_subtotal_inr == Decimal("100000.00")


def test_offset_exceeding_the_line_is_floored_at_zero_with_a_warning() -> None:
    offset = AssetSpendOffset(
        asset=AssetKind.STOREFRONT,
        reduces_line="shop fit-out",
        amount_avoided=_provided(500_000, Unit.INR),
    )
    pc = ProjectCostInput(
        lines=[_line("shop fit-out", 200_000)],
        offsets=[offset],
        contingency_pct=_provided(Decimal("0"), Unit.RATIO),
    )
    res = compute_project_cost(pc, _working_capital_result())
    assert res.capex_subtotal_inr == Decimal("0.00")
    assert any("exceeds" in n for n in res.notes)


def test_offset_naming_an_unknown_line_is_not_applied_and_is_noted() -> None:
    offset = AssetSpendOffset(
        asset=AssetKind.VEHICLE,
        reduces_line="delivery van",  # not a line in this plan
        amount_avoided=_provided(50_000, Unit.INR),
    )
    pc = ProjectCostInput(
        lines=[_line("shop fit-out", 200_000)],
        offsets=[offset],
        contingency_pct=_provided(Decimal("0"), Unit.RATIO),
    )
    res = compute_project_cost(pc, _working_capital_result())
    assert res.capex_subtotal_inr == Decimal("200000.00")
    assert any("not a project-cost line" in n for n in res.notes)


def test_missing_contingency_pct_falls_back_to_config_default() -> None:
    pc = ProjectCostInput(lines=[_line("shop fit-out", 200_000)])
    res = compute_project_cost(pc, _working_capital_result())
    cfg = DEFAULT_FINANCE_CONFIG
    assert res.contingency_pct == cfg.contingency_pct
    assert any("config:finance default" in n for n in res.notes)


def test_opening_inventory_is_counted_once_inside_working_capital() -> None:
    pc = ProjectCostInput(
        lines=[_line("shop fit-out", 200_000)], contingency_pct=_provided(Decimal("0"), Unit.RATIO)
    )
    wc = WorkingCapitalInput(opening_inventory=_provided(50_000, Unit.INR))
    wc_result = compute_working_capital(
        wc,
        monthly_revenue_inr=Decimal("0"),
        monthly_cogs_inr=Decimal("0"),
        monthly_fixed_opex_inr=Decimal("0"),
    )
    res = compute_project_cost(pc, wc_result)
    # capex never includes the 50,000 opening inventory; it only enters via
    # net_working_capital_inr, once.
    assert res.capex_subtotal_inr == Decimal("200000.00")
    assert res.net_working_capital_inr == Decimal("50000.00")
    assert res.project_cost_inr == Decimal("250000.00")
    assert any("counted once" in n for n in res.notes)


# ======================================================================
# capital gap / promoter contribution
# ======================================================================


def test_capital_gap_is_zero_when_fully_funded() -> None:
    gap = compute_capital_gap(
        Decimal("500000"),
        promoter_cash_inr=Decimal("100000"),
        other_committed_funds_inr=Decimal("0"),
        loan_principal_inr=Decimal("400000"),
    )
    assert gap == Decimal("0.00")


def test_capital_gap_is_never_negative_on_a_surplus() -> None:
    gap = compute_capital_gap(
        Decimal("500000"),
        promoter_cash_inr=Decimal("100000"),
        other_committed_funds_inr=Decimal("0"),
        loan_principal_inr=Decimal("600000"),
    )
    assert gap == Decimal("0.00")


def test_capital_gap_reports_the_shortfall() -> None:
    gap = compute_capital_gap(
        Decimal("500000"),
        promoter_cash_inr=Decimal("100000"),
        other_committed_funds_inr=Decimal("0"),
        loan_principal_inr=Decimal("300000"),
    )
    assert gap == Decimal("100000.00")


def test_promoter_contribution_pct_is_none_for_zero_project_cost() -> None:
    assert compute_promoter_contribution_pct(Decimal("0"), Decimal("0")) is None


def test_promoter_contribution_pct_is_a_ratio() -> None:
    pct = compute_promoter_contribution_pct(Decimal("500000"), Decimal("100000"))
    assert pct == Decimal("0.2000")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
