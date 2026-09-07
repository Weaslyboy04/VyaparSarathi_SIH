"""Revenue, operating costs, profitability and break-even (CLAUDE.md §15).
Pure & offline."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.operations import (
    compute_break_even,
    compute_operating_costs,
    compute_revenue_schedule,
)
from vyaparsarathi.models.finance import (
    FinancialInput,
    InputKind,
    OperatingCostInput,
    OpexLine,
    RevenueInput,
    Unit,
)


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


# ======================================================================
# revenue schedule
# ======================================================================


def test_revenue_schedule_requires_a_driver() -> None:
    with pytest.raises(ValueError, match="requires"):
        compute_revenue_schedule(RevenueInput(), horizon_months=12)


def test_flat_monthly_revenue_with_no_ramp_is_constant_every_month() -> None:
    res = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(50_000, Unit.INR_PER_MONTH)), horizon_months=3
    )
    assert [m.revenue_inr for m in res.months] == [Decimal("50000.00")] * 3
    assert any("no ramp period" in n for n in res.notes)


def test_unit_price_times_volume_model() -> None:
    res = compute_revenue_schedule(
        RevenueInput(
            unit_price=_provided(Decimal("20"), Unit.INR_PER_UNIT),
            units_per_month=_provided(1000, Unit.UNITS_PER_MONTH),
        ),
        horizon_months=1,
    )
    assert res.months[0].revenue_inr == Decimal("20000.00")


def test_ramp_scales_revenue_from_ramp_start_to_full_by_ramp_months() -> None:
    res = compute_revenue_schedule(
        RevenueInput(
            monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH),
            ramp_months=_provided(4, Unit.MONTHS),
            ramp_start_pct=_provided(Decimal("0.25"), Unit.RATIO),
        ),
        horizon_months=5,
    )
    # month 1: 0.25 + 0.75*(1/4) = 0.4375 -> 43750
    assert res.months[0].revenue_inr == Decimal("43750.00")
    # month 4: fully ramped -> 100000
    assert res.months[3].revenue_inr == Decimal("100000.00")
    # month 5: still fully ramped (min(t, ramp_months) caps at ramp_months)
    assert res.months[4].revenue_inr == Decimal("100000.00")


def test_ramp_months_without_ramp_start_pct_assumes_zero_start() -> None:
    res = compute_revenue_schedule(
        RevenueInput(
            monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH),
            ramp_months=_provided(2, Unit.MONTHS),
        ),
        horizon_months=2,
    )
    assert res.months[0].revenue_inr == Decimal("50000.00")  # 0 + 1.0*(1/2)
    assert any("assumed to" in n for n in res.notes)


def test_seasonality_index_scales_each_calendar_month() -> None:
    index = tuple(Decimal("1.5") if i == 0 else Decimal((12 - 1.5) / 11) for i in range(12))
    res = compute_revenue_schedule(
        RevenueInput(
            monthly_revenue=_provided(10_000, Unit.INR_PER_MONTH), seasonality_index=index
        ),
        horizon_months=13,
    )
    assert res.months[0].revenue_inr == Decimal("15000.00")  # month 1 -> index[0] = 1.5
    assert res.months[12].revenue_inr == Decimal("15000.00")  # month 13 wraps to index[0]
    assert res.seasonality_applied is True


def test_seasonality_index_wrong_length_raises() -> None:
    with pytest.raises(ValueError, match="12"):
        compute_revenue_schedule(
            RevenueInput(
                monthly_revenue=_provided(10_000, Unit.INR_PER_MONTH),
                seasonality_index=(Decimal("1"),) * 11,
            ),
            horizon_months=1,
        )


def test_seasonality_index_not_averaging_one_raises() -> None:
    with pytest.raises(ValueError, match="average"):
        compute_revenue_schedule(
            RevenueInput(
                monthly_revenue=_provided(10_000, Unit.INR_PER_MONTH),
                seasonality_index=(Decimal("2"),) * 12,
            ),
            horizon_months=1,
        )


def test_annual_revenue_buckets_by_project_year() -> None:
    res = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(10_000, Unit.INR_PER_MONTH)), horizon_months=14
    )
    assert res.annual_revenue_inr[1] == Decimal("120000.00")  # months 1-12
    assert res.annual_revenue_inr[2] == Decimal("20000.00")  # months 13-14


def test_horizon_months_must_be_positive() -> None:
    with pytest.raises(ValueError, match="horizon_months"):
        compute_revenue_schedule(
            RevenueInput(monthly_revenue=_provided(1000, Unit.INR_PER_MONTH)), horizon_months=0
        )


# ======================================================================
# operating costs
# ======================================================================


def test_operating_costs_requires_a_margin_driver() -> None:
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(10_000, Unit.INR_PER_MONTH)), horizon_months=1
    )
    with pytest.raises(ValueError, match="requires"):
        compute_operating_costs(schedule, OperatingCostInput())


def test_gross_margin_and_cogs_pct_are_complementary() -> None:
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH)), horizon_months=1
    )
    via_margin = compute_operating_costs(
        schedule, OperatingCostInput(gross_margin_pct=_provided(Decimal("0.4"), Unit.RATIO))
    )
    via_cogs = compute_operating_costs(
        schedule, OperatingCostInput(cogs_pct=_provided(Decimal("0.6"), Unit.RATIO))
    )
    assert via_margin.cogs_pct == via_cogs.cogs_pct == Decimal("0.6000")
    assert via_margin.gross_margin_pct == via_cogs.gross_margin_pct == Decimal("0.4000")


def test_supplying_both_cogs_and_margin_is_rejected_at_the_model() -> None:
    with pytest.raises(ValueError):
        OperatingCostInput(
            cogs_pct=_provided(Decimal("0.6"), Unit.RATIO),
            gross_margin_pct=_provided(Decimal("0.4"), Unit.RATIO),
        )


def test_missing_fixed_lines_and_variable_opex_default_to_zero_with_notes() -> None:
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH)), horizon_months=1
    )
    res = compute_operating_costs(
        schedule, OperatingCostInput(gross_margin_pct=_provided(Decimal("0.4"), Unit.RATIO))
    )
    assert res.fixed_opex_monthly_inr == Decimal("0.00")
    assert res.variable_opex_pct == Decimal("0.0000")
    assert any("fixed operating-expense" in n for n in res.notes)
    assert any("variable operating-cost" in n for n in res.notes)


def test_monthly_operating_profit_arithmetic() -> None:
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH)), horizon_months=1
    )
    res = compute_operating_costs(
        schedule,
        OperatingCostInput(
            gross_margin_pct=_provided(Decimal("0.4"), Unit.RATIO),
            variable_opex_pct=_provided(Decimal("0.05"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_provided(10_000, Unit.INR_PER_MONTH))],
        ),
    )
    m = res.months[0]
    assert m.cogs_inr == Decimal("60000.00")  # 100000 * 0.6
    assert m.gross_profit_inr == Decimal("40000.00")
    assert m.variable_opex_inr == Decimal("5000.00")  # 100000 * 0.05
    assert m.contribution_inr == Decimal("35000.00")  # 40000 - 5000
    assert m.operating_profit_inr == Decimal("25000.00")  # 35000 - 10000
    assert res.depreciation_modelled is False
    assert res.tax_modelled is False


# ======================================================================
# break-even
# ======================================================================


def _operating(gross_margin_pct: str, variable_opex_pct: str = "0", fixed_opex: int = 20_000):
    schedule = compute_revenue_schedule(
        RevenueInput(monthly_revenue=_provided(100_000, Unit.INR_PER_MONTH)), horizon_months=6
    )
    return compute_operating_costs(
        schedule,
        OperatingCostInput(
            gross_margin_pct=_provided(Decimal(gross_margin_pct), Unit.RATIO),
            variable_opex_pct=_provided(Decimal(variable_opex_pct), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_provided(fixed_opex, Unit.INR_PER_MONTH))],
        ),
    )


def test_break_even_revenue_is_fixed_opex_over_contribution_margin_ratio() -> None:
    op = _operating("0.40")
    be = compute_break_even(op)
    assert be.contribution_margin_ratio == Decimal("0.4000")
    assert be.break_even_revenue_monthly_inr == Decimal("50000.00")  # 20000 / 0.4


def test_break_even_including_debt_service_is_higher() -> None:
    op = _operating("0.40")
    be = compute_break_even(op, monthly_debt_service_inr=Decimal("4000"))
    assert be.break_even_revenue_incl_debt_monthly_inr == Decimal("60000.00")  # 24000/0.4


def test_break_even_units_uses_unit_price() -> None:
    op = _operating("0.40")
    be = compute_break_even(op, unit_price_inr=Decimal("50"))
    assert be.break_even_units_monthly == Decimal("1000.00")  # 50000/50


def test_zero_or_negative_contribution_margin_makes_break_even_undefined() -> None:
    op = _operating("0.0", variable_opex_pct="1.0")  # cm_ratio = 1 - 1 - 1 = -1
    be = compute_break_even(op)
    assert be.contribution_margin_ratio is None
    assert be.break_even_revenue_monthly_inr is None
    assert "undefined" in be.undefined_reason


def test_operating_break_even_month_is_the_first_month_with_nonnegative_profit() -> None:
    op = _operating("0.40", fixed_opex=39_999)  # operating_profit = 40000-39999 = 1 > 0 immediately
    be = compute_break_even(op)
    assert be.operating_break_even_month == 1


def test_cash_break_even_month_is_none_until_cash_flow_is_computed() -> None:
    op = _operating("0.40")
    be = compute_break_even(op)
    assert be.cash_break_even_month is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
