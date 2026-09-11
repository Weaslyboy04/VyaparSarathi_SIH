"""Quarter-by-quarter repayment view for the DPR (CLAUDE.md §17, §25 Phase 8).

`finance/debt.py::compute_debt_schedule` already produces a full month-by-month
moratorium + amortisation schedule (`DebtScheduleResult`); the Financial
assessment section only ever surfaced the single EMI figure from it. This
module groups that existing schedule into calendar quarters so a reader can
see, at a glance: when repayment actually starts, how the moratorium's
interest was treated, and how the outstanding balance moves over time — the
exact gap the SIH26091 judge feedback named ("Add a proper repayment
schedule"). Pure re-composition of an already-computed schedule; no new
financial arithmetic, no engine call, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from vyaparsarathi.dpr.format import format_inr
from vyaparsarathi.dpr.provenance import pv_calc
from vyaparsarathi.dpr.report_models import RepaymentQuarterLine
from vyaparsarathi.finance.money import q_money
from vyaparsarathi.finance.results import DebtScheduleResult

_INPUTS = ("debt amortisation schedule",)
_ZERO = Decimal("0.00")


@dataclass(frozen=True)
class _MonthRow:
    calendar_month: int
    is_moratorium: bool
    opening_inr: Decimal
    principal_inr: Decimal
    interest_inr: Decimal
    closing_inr: Decimal
    note: str = ""


def _month_rows(debt: DebtScheduleResult) -> list[_MonthRow]:
    rows: list[_MonthRow] = [
        _MonthRow(
            calendar_month=mp.period,
            is_moratorium=True,
            opening_inr=mp.opening_balance_inr,
            principal_inr=_ZERO,
            interest_inr=mp.interest_paid_inr,
            closing_inr=mp.closing_balance_inr,
        )
        for mp in debt.moratorium_schedule
    ]
    for i, ap in enumerate(debt.amortisation_schedule):
        # The moratorium's deferred interest (INTEREST_ACCRUED_PAID_ON_EMI_START)
        # is settled as one lump sum the moment EMI begins — attach it to the
        # first repayment month so it is never silently lost between the two
        # schedules.
        lump = debt.accrued_interest_paid_at_emi_start_inr if i == 0 else _ZERO
        rows.append(
            _MonthRow(
                calendar_month=debt.moratorium_months + ap.period,
                is_moratorium=False,
                opening_inr=ap.opening_balance_inr,
                principal_inr=ap.principal_inr,
                interest_inr=q_money(ap.interest_inr + lump),
                closing_inr=ap.closing_balance_inr,
                note=(
                    "includes moratorium-period interest settled as a lump sum at EMI start"
                    if lump > _ZERO
                    else ""
                ),
            )
        )
    return rows


def build_quarterly_repayment_schedule(
    debt: DebtScheduleResult,
) -> tuple[RepaymentQuarterLine, ...]:
    """Group `debt`'s month-by-month schedule into calendar quarters. Empty
    when `debt` never amortised (`negative_amortisation`) — never fabricates
    a schedule the engine itself refused to produce."""
    rows = _month_rows(debt)
    if not rows:
        return ()

    quarters: list[RepaymentQuarterLine] = []
    for start in range(0, len(rows), 3):
        chunk = rows[start : start + 3]
        first, last = chunk[0], chunk[-1]
        moratorium_months = sum(1 for r in chunk if r.is_moratorium)
        if moratorium_months == len(chunk):
            status = "Moratorium"
        elif moratorium_months == 0:
            status = "Repayment"
        else:
            status = "Moratorium -> Repayment"

        principal_paid = q_money(sum((r.principal_inr for r in chunk), _ZERO))
        interest_paid = q_money(sum((r.interest_inr for r in chunk), _ZERO))
        total_payment = q_money(principal_paid + interest_paid)
        note = "; ".join(dict.fromkeys(r.note for r in chunk if r.note))

        n = len(quarters) + 1
        label = (
            f"Q{n} (Month {first.calendar_month})"
            if len(chunk) == 1
            else f"Q{n} (Months {first.calendar_month}-{last.calendar_month})"
        )

        quarters.append(
            RepaymentQuarterLine(
                label=label,
                status=status,
                opening_balance=pv_calc(
                    "Opening balance",
                    format_inr(first.opening_inr),
                    inputs=_INPUTS,
                    raw=str(first.opening_inr),
                ),
                principal_paid=pv_calc(
                    "Principal paid",
                    format_inr(principal_paid),
                    inputs=_INPUTS,
                    raw=str(principal_paid),
                ),
                interest_paid=pv_calc(
                    "Interest paid",
                    format_inr(interest_paid),
                    inputs=_INPUTS,
                    raw=str(interest_paid),
                ),
                total_payment=pv_calc(
                    "Total payment",
                    format_inr(total_payment),
                    inputs=_INPUTS,
                    raw=str(total_payment),
                ),
                closing_balance=pv_calc(
                    "Closing balance",
                    format_inr(last.closing_inr),
                    inputs=_INPUTS,
                    raw=str(last.closing_inr),
                ),
                note=note,
            )
        )
    return tuple(quarters)


__all__ = ["build_quarterly_repayment_schedule"]
