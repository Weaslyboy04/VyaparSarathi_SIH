"""Revenue, operating costs, profitability and break-even (CLAUDE.md §15).

Pure. Like `costs.py`, these functions assume their required drivers are
already resolved by the caller (`finance/assessment.py`); a `ValueError` here
signals a caller mistake, not a data-quality gap.

Terminology guard (CLAUDE.md — do not claim accounting standards or tax
treatment that are not modelled): the profitability figure this module
produces is named `operating_profit`, a **cash-basis** operating surplus. It
is never called EBITDA, PAT, or "net profit" — no depreciation and no tax are
modelled anywhere in this engine, and `OperatingCostResult` says so on every
result (`depreciation_modelled=False`, `tax_modelled=False`).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from vyaparsarathi.finance.money import q_money, q_ratio, rupees
from vyaparsarathi.finance.results import (
    BreakEvenResult,
    MonthlyOperatingCost,
    MonthlyRevenue,
    OperatingCostResult,
    RevenueScheduleResult,
)
from vyaparsarathi.models.finance import OperatingCostInput, RevenueInput

_UNITS_Q = Decimal("0.01")


def _validate_seasonality(index: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
    if len(index) != 12:
        raise ValueError(
            f"seasonality_index must have exactly 12 monthly factors; got {len(index)}"
        )
    mean = sum(index) / Decimal(12)
    if abs(mean - Decimal("1")) > Decimal("0.005"):
        raise ValueError(f"seasonality_index must average 1.0 (+/- 0.005); got {mean}")
    return index


def compute_revenue_schedule(revenue: RevenueInput, horizon_months: int) -> RevenueScheduleResult:
    if horizon_months <= 0:
        raise ValueError("horizon_months must be > 0")

    notes: list[str] = []

    if revenue.monthly_revenue is not None:
        base = q_money(rupees(revenue.monthly_revenue.value))
    elif revenue.unit_price is not None and revenue.units_per_month is not None:
        base = q_money(rupees(revenue.unit_price.value) * rupees(revenue.units_per_month.value))
    else:
        raise ValueError(
            "compute_revenue_schedule requires monthly_revenue or (unit_price, "
            "units_per_month); the caller must resolve missing-evidence cases before "
            "calling into the pure engine"
        )

    if revenue.ramp_months is not None:
        ramp_months = int(revenue.ramp_months.value)
        if ramp_months < 0:
            raise ValueError("ramp_months must be >= 0")
    else:
        ramp_months = 0
        notes.append("no ramp period specified; revenue modelled as flat from month 1")

    if ramp_months > 0 and revenue.ramp_start_pct is not None:
        ramp_start_pct = rupees(revenue.ramp_start_pct.value)
    elif ramp_months > 0:
        ramp_start_pct = Decimal("0")
        notes.append(
            "ramp_months was supplied without ramp_start_pct; the ramp is assumed to "
            "start from zero"
        )
    else:
        ramp_start_pct = Decimal("1")

    if revenue.seasonality_index is not None:
        factors = _validate_seasonality(revenue.seasonality_index)
        seasonality_applied = True
    else:
        factors = tuple(Decimal("1") for _ in range(12))
        seasonality_applied = False
        notes.append("no seasonality supplied; revenue modelled as flat across the year")

    months: list[MonthlyRevenue] = []
    annual: dict[int, Decimal] = {}
    for t in range(1, horizon_months + 1):
        if ramp_months > 0:
            ramp_factor = ramp_start_pct + (Decimal("1") - ramp_start_pct) * (
                min(Decimal(t), Decimal(ramp_months)) / Decimal(ramp_months)
            )
        else:
            ramp_factor = Decimal("1")
        season_factor = factors[(t - 1) % 12]
        month_revenue = q_money(base * ramp_factor * season_factor)
        months.append(
            MonthlyRevenue(
                month=t,
                ramp_factor=q_ratio(ramp_factor),
                seasonality_factor=q_ratio(season_factor),
                revenue_inr=month_revenue,
            )
        )
        year = (t - 1) // 12 + 1
        annual[year] = annual.get(year, Decimal("0.00")) + month_revenue

    return RevenueScheduleResult(
        base_monthly_revenue_inr=base,
        ramp_months=ramp_months,
        ramp_start_pct=q_ratio(ramp_start_pct),
        seasonality_applied=seasonality_applied,
        months=months,
        annual_revenue_inr={y: q_money(v) for y, v in annual.items()},
        notes=notes,
    )


def compute_operating_costs(
    revenue_schedule: RevenueScheduleResult,
    operating_costs: OperatingCostInput,
) -> OperatingCostResult:
    notes: list[str] = []

    if operating_costs.gross_margin_pct is not None:
        gross_margin_pct = rupees(operating_costs.gross_margin_pct.value)
        cogs_pct = Decimal("1") - gross_margin_pct
    elif operating_costs.cogs_pct is not None:
        cogs_pct = rupees(operating_costs.cogs_pct.value)
        gross_margin_pct = Decimal("1") - cogs_pct
    else:
        raise ValueError(
            "compute_operating_costs requires cogs_pct or gross_margin_pct; the caller "
            "must resolve missing-evidence cases before calling into the pure engine"
        )

    if operating_costs.variable_opex_pct is not None:
        variable_opex_pct = rupees(operating_costs.variable_opex_pct.value)
    else:
        variable_opex_pct = Decimal("0")
        notes.append("no variable operating-cost percentage supplied; treated as zero beyond COGS")

    if operating_costs.fixed_lines:
        fixed_opex_monthly = sum(
            (rupees(line.amount.value) for line in operating_costs.fixed_lines),
            start=Decimal("0"),
        )
    else:
        fixed_opex_monthly = Decimal("0")
        notes.append("no fixed operating-expense lines supplied; fixed opex treated as zero")
    fixed_opex_monthly = q_money(fixed_opex_monthly)

    months: list[MonthlyOperatingCost] = []
    for mr in revenue_schedule.months:
        cogs = q_money(mr.revenue_inr * cogs_pct)
        variable_opex = q_money(mr.revenue_inr * variable_opex_pct)
        gross_profit = mr.revenue_inr - cogs
        contribution = mr.revenue_inr - cogs - variable_opex
        operating_profit = contribution - fixed_opex_monthly
        months.append(
            MonthlyOperatingCost(
                month=mr.month,
                revenue_inr=mr.revenue_inr,
                cogs_inr=cogs,
                variable_opex_inr=variable_opex,
                fixed_opex_inr=fixed_opex_monthly,
                gross_profit_inr=gross_profit,
                contribution_inr=contribution,
                operating_profit_inr=operating_profit,
            )
        )

    return OperatingCostResult(
        cogs_pct=q_ratio(cogs_pct),
        gross_margin_pct=q_ratio(gross_margin_pct),
        variable_opex_pct=q_ratio(variable_opex_pct),
        fixed_opex_monthly_inr=fixed_opex_monthly,
        months=months,
        depreciation_modelled=False,
        tax_modelled=False,
        notes=notes,
    )


def _first_break_even_month(months: list[MonthlyOperatingCost]) -> int | None:
    for m in months:
        if m.operating_profit_inr >= 0:
            return m.month
    return None


def compute_break_even(
    operating: OperatingCostResult,
    *,
    monthly_debt_service_inr: Decimal | None = None,
    unit_price_inr: Decimal | None = None,
) -> BreakEvenResult:
    """The contribution-margin ratio is constant across months (COGS and
    variable opex are flat percentages of revenue), so break-even is computed
    directly from the percentages and fixed opex rather than by scanning the
    monthly schedule. ``cash_break_even_month`` is left `None` here — it needs
    the monthly cash-flow simulation and is filled in by `finance/assessment.py`
    once that is computed (4B)."""
    cm_ratio = Decimal("1") - operating.cogs_pct - operating.variable_opex_pct

    operating_break_even_month = _first_break_even_month(operating.months)

    if cm_ratio <= 0:
        return BreakEvenResult(
            contribution_margin_ratio=None,
            break_even_revenue_monthly_inr=None,
            break_even_revenue_incl_debt_monthly_inr=None,
            break_even_units_monthly=None,
            operating_break_even_month=operating_break_even_month,
            cash_break_even_month=None,
            undefined_reason="contribution margin is zero or negative; break-even is undefined",
        )

    be_revenue = operating.fixed_opex_monthly_inr / cm_ratio
    be_revenue_q = q_money(be_revenue)

    be_incl_debt: Decimal | None = None
    if monthly_debt_service_inr is not None:
        be_incl_debt = q_money(
            (operating.fixed_opex_monthly_inr + monthly_debt_service_inr) / cm_ratio
        )

    be_units: Decimal | None = None
    if unit_price_inr is not None and unit_price_inr > 0:
        be_units = (be_revenue / unit_price_inr).quantize(_UNITS_Q, rounding=ROUND_HALF_UP)

    return BreakEvenResult(
        contribution_margin_ratio=q_ratio(cm_ratio),
        break_even_revenue_monthly_inr=be_revenue_q,
        break_even_revenue_incl_debt_monthly_inr=be_incl_debt,
        break_even_units_monthly=be_units,
        operating_break_even_month=operating_break_even_month,
        cash_break_even_month=None,
        undefined_reason="",
    )
