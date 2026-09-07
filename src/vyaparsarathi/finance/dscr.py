"""DSCR — the exact definition used here (CLAUDE.md §15).

``CADS(window) = sum of operating_profit_inr over the months in that window`` —
revenue collected minus COGS paid minus operating expenses, BEFORE any debt
service, on a cash basis (in this engine's model, "collected"/"paid" coincide
with "earned"/"incurred" in the same month — see `cashflow.py`). **No
depreciation add-back and no tax deduction**: neither is modelled anywhere in
this engine, so adding either back to CADS would be a fiction.

Three windows are computed. ``average_annual_dscr`` and
``first_post_moratorium_year_dscr`` are the headline pair this engine reports
to the user — the banker's and DPR's usual annual convention, plus the one
number CLAUDE.md §17 cares about most.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.finance.money import q_ratio
from vyaparsarathi.finance.results import (
    CashFlowResult,
    DSCRPeriod,
    DSCRResult,
    OperatingCostResult,
)

_ZERO = Decimal("0.00")


def _dscr(cads_inr: Decimal, debt_service_inr: Decimal) -> Decimal | None:
    """`None` — never `0` and never infinity — when there is no debt service in
    the window ("no debt service in this window")."""
    if debt_service_inr == 0:
        return None
    return q_ratio(cads_inr / debt_service_inr)


def compute_dscr(
    operating: OperatingCostResult,
    cash_flow: CashFlowResult,
    *,
    moratorium_months: int = 0,
) -> DSCRResult:
    cads_by_month = {m.month: m.operating_profit_inr for m in operating.months}
    debt_service_by_month = {m.month: m.debt_service_inr for m in cash_flow.months if m.month > 0}
    horizon = max(cads_by_month) if cads_by_month else 0

    monthly: list[DSCRPeriod] = []
    for month in sorted(cads_by_month):
        cads = cads_by_month[month]
        service = debt_service_by_month.get(month, _ZERO)
        monthly.append(
            DSCRPeriod(
                period=month, cads_inr=cads, debt_service_inr=service, dscr=_dscr(cads, service)
            )
        )

    annual: list[DSCRPeriod] = []
    num_years = (horizon + 11) // 12 if horizon else 0
    for year in range(1, num_years + 1):
        start_month = (year - 1) * 12 + 1
        end_month = min(year * 12, horizon)
        cads = sum((cads_by_month.get(m, _ZERO) for m in range(start_month, end_month + 1)), _ZERO)
        service = sum(
            (debt_service_by_month.get(m, _ZERO) for m in range(start_month, end_month + 1)),
            _ZERO,
        )
        annual.append(
            DSCRPeriod(
                period=year, cads_inr=cads, debt_service_inr=service, dscr=_dscr(cads, service)
            )
        )

    total_cads = sum(cads_by_month.values(), _ZERO)
    total_service = sum(debt_service_by_month.values(), _ZERO)
    project_period_dscr = _dscr(total_cads, total_service)

    servicing_years = [p for p in annual if p.dscr is not None]
    dscr_values: list[Decimal] = [p.dscr for p in annual if p.dscr is not None]
    average_annual_dscr = (
        q_ratio(sum(dscr_values, Decimal("0")) / Decimal(len(dscr_values))) if dscr_values else None
    )

    # The annual window containing the first post-moratorium (EMI) month.
    first_post_moratorium_year = (moratorium_months // 12) + 1
    first_post_moratorium_year_dscr = next(
        (p.dscr for p in annual if p.period == first_post_moratorium_year), None
    )

    notes: list[str] = []
    if not servicing_years:
        notes.append(
            "no year in the horizon carries any debt service; DSCR is undefined throughout"
        )

    return DSCRResult(
        annual_dscr=annual,
        monthly_dscr=monthly,
        project_period_dscr=project_period_dscr,
        average_annual_dscr=average_annual_dscr,
        first_post_moratorium_year_dscr=first_post_moratorium_year_dscr,
        notes=notes,
    )
