"""Monthly cash flow (CLAUDE.md §15, §16, §17).

Pure. **Modelling choice, stated explicitly (see docs/phase-4.md, Known
limitations):** the receivable/payable timing gap is funded *upfront*, as part
of `net_working_capital_inr` (see `costs.py`), rather than modelled as a
month-by-month collection/payment lag here — doing both would double-count the
same gap. Revenue is therefore treated as collected, and COGS/operating
expenses as paid, in the same month they are earned or incurred; the
receivables cushion and payables credit already funded at month 0 are what
make that safe to assume, not a claim that real-world collections are
same-month.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.finance.money import q_money
from vyaparsarathi.finance.results import (
    CashFlowResult,
    DebtScheduleResult,
    MonthlyCashFlow,
    OperatingCostResult,
)

_ZERO = Decimal("0.00")


def _debt_service_by_month(
    debt: DebtScheduleResult | None, moratorium_months: int
) -> dict[int, Decimal]:
    """Month index (1-based, aligned with `operating.months`) -> debt-service
    outflow for that month. Moratorium months are 1..m; EMI months are
    m+1..m+n."""
    if debt is None:
        return {}
    service: dict[int, Decimal] = {}
    for mp in debt.moratorium_schedule:
        if mp.interest_paid_inr > 0:
            service[mp.period] = service.get(mp.period, _ZERO) + mp.interest_paid_inr
    if debt.accrued_interest_paid_at_emi_start_inr > 0:
        first_emi_month = moratorium_months + 1
        service[first_emi_month] = (
            service.get(first_emi_month, _ZERO) + debt.accrued_interest_paid_at_emi_start_inr
        )
    for ap in debt.amortisation_schedule:
        month = moratorium_months + ap.period
        service[month] = service.get(month, _ZERO) + ap.payment_inr
    return service


def compute_cash_flow(
    operating: OperatingCostResult,
    *,
    capex_and_contingency_inr: Decimal,
    opening_inventory_inr: Decimal,
    financing_inflow_inr: Decimal,
    debt: DebtScheduleResult | None,
    moratorium_months: int = 0,
) -> CashFlowResult:
    debt_service = _debt_service_by_month(debt, moratorium_months)

    setup_outflow = q_money(capex_and_contingency_inr + opening_inventory_inr)
    closing0 = q_money(financing_inflow_inr - setup_outflow)

    months: list[MonthlyCashFlow] = [
        MonthlyCashFlow(
            month=0,
            opening_cash_inr=_ZERO,
            operating_inflow_inr=_ZERO,
            operating_outflow_inr=_ZERO,
            debt_service_inr=_ZERO,
            financing_inflow_inr=q_money(financing_inflow_inr),
            setup_outflow_inr=setup_outflow,
            closing_cash_inr=closing0,
        )
    ]

    balance = closing0
    for mr in operating.months:
        inflow = mr.revenue_inr
        outflow = q_money(mr.cogs_inr + mr.variable_opex_inr + mr.fixed_opex_inr)
        service = debt_service.get(mr.month, _ZERO)
        closing = q_money(balance + inflow - outflow - service)
        months.append(
            MonthlyCashFlow(
                month=mr.month,
                opening_cash_inr=balance,
                operating_inflow_inr=inflow,
                operating_outflow_inr=outflow,
                debt_service_inr=service,
                closing_cash_inr=closing,
            )
        )
        balance = closing

    negative_months = [m.month for m in months if m.closing_cash_inr < 0]
    min_entry = min(months, key=lambda m: m.closing_cash_inr)

    if negative_months:
        runway: int | None = negative_months[0]
        shortfall = q_money(-min_entry.closing_cash_inr)
    else:
        runway = None
        shortfall = _ZERO

    emi_start_month: int | None = None
    cash_at_emi_start: Decimal | None = None
    if debt is not None and debt.amortisation_schedule:
        emi_start_month = moratorium_months + 1
        prev = next((m for m in months if m.month == emi_start_month - 1), None)
        # The cash on hand right when the first EMI comes due — before that
        # month's own EMI is paid (CLAUDE.md §17's central question).
        cash_at_emi_start = prev.closing_cash_inr if prev is not None else balance

    notes = [
        "revenue is treated as collected, and COGS/operating expenses as paid, in the same "
        "month they are earned or incurred; the receivable/payable timing gap is funded "
        "upfront via the working-capital reserve rather than lagged month by month here."
    ]

    return CashFlowResult(
        months=months,
        negative_cash_months=negative_months,
        minimum_cash_balance_inr=min_entry.closing_cash_inr,
        minimum_cash_month=min_entry.month,
        cash_runway_months=runway,
        peak_cash_shortfall_inr=shortfall,
        cash_at_emi_start_inr=cash_at_emi_start,
        emi_start_month=emi_start_month,
        notes=notes,
    )
