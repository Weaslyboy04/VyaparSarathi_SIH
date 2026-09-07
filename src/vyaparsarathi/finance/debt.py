"""EMI, amortisation and moratorium (CLAUDE.md §15, §17).

Pure. The reducing-balance convention throughout: a fixed EMI is quantised
once and held for the whole schedule; interest is quantised each period
(`q_money(opening_balance * i)`) so the reported per-period figures sum
exactly to the reported totals; the final instalment absorbs whatever
principal remains, so the schedule always closes at exactly `0.00` and
`sum(principal) == principal_at_emi_start` to the paise.

`moratorium_treatment` is always read from the caller's `LoanTerms` — this
module applies no default. A loan with `moratorium_months > 0` and
`moratorium_treatment == NONE` is a caller contract violation (a
`ValueError`), not a case this module silently resolves.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.finance.money import annual_pct_to_monthly_rate, q_money, rupees
from vyaparsarathi.finance.results import (
    AmortisationPeriod,
    DebtScheduleResult,
    MoratoriumPeriod,
)
from vyaparsarathi.models.finance import LoanTerms, MoratoriumTreatment


def compute_emi(principal_inr: Decimal, monthly_rate: Decimal, tenure_months: int) -> Decimal:
    """The standard reducing-balance EMI formula, quantised once."""
    if tenure_months <= 0:
        raise ValueError("tenure_months must be > 0")
    if monthly_rate == 0:
        return q_money(principal_inr / Decimal(tenure_months))
    one_plus_i_n = (Decimal("1") + monthly_rate) ** tenure_months
    return q_money(principal_inr * monthly_rate * one_plus_i_n / (one_plus_i_n - Decimal("1")))


def _apply_moratorium(
    principal_inr: Decimal,
    monthly_rate: Decimal,
    moratorium_months: int,
    treatment: MoratoriumTreatment,
) -> tuple[Decimal, list[MoratoriumPeriod], Decimal, Decimal]:
    """Returns ``(principal_at_emi_start, schedule, capitalised_interest,
    accrued_interest_paid_at_emi_start)``. Compounds month by month with
    per-period quantisation (not a closed-form power), so it stays exactly
    self-consistent with the amortisation schedule's own convention."""
    if moratorium_months == 0:
        return principal_inr, [], Decimal("0.00"), Decimal("0.00")

    schedule: list[MoratoriumPeriod] = []
    balance = principal_inr
    total_accrued = Decimal("0.00")
    capitalised_total = Decimal("0.00")

    for period in range(1, moratorium_months + 1):
        interest = q_money(balance * monthly_rate)
        total_accrued += interest
        if treatment is MoratoriumTreatment.INTEREST_SERVICED:
            schedule.append(
                MoratoriumPeriod(
                    period=period,
                    opening_balance_inr=balance,
                    interest_accrued_inr=interest,
                    interest_paid_inr=interest,
                    closing_balance_inr=balance,
                )
            )
        elif treatment is MoratoriumTreatment.INTEREST_CAPITALISED:
            new_balance = q_money(balance + interest)
            schedule.append(
                MoratoriumPeriod(
                    period=period,
                    opening_balance_inr=balance,
                    interest_accrued_inr=interest,
                    capitalised_inr=interest,
                    closing_balance_inr=new_balance,
                )
            )
            capitalised_total += interest
            balance = new_balance
        elif treatment is MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START:
            schedule.append(
                MoratoriumPeriod(
                    period=period,
                    opening_balance_inr=balance,
                    interest_accrued_inr=interest,
                    closing_balance_inr=balance,
                )
            )
        else:  # pragma: no cover — guarded by the caller-contract check below
            raise ValueError(f"unhandled moratorium treatment: {treatment}")

    accrued_paid_at_start = (
        q_money(total_accrued)
        if treatment is MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START
        else Decimal("0.00")
    )
    return balance, schedule, q_money(capitalised_total), accrued_paid_at_start


def compute_debt_schedule(loan: LoanTerms) -> DebtScheduleResult:
    principal = q_money(rupees(loan.principal_requested.value))
    annual_rate = rupees(loan.interest_rate_pct.value)
    monthly_rate = annual_pct_to_monthly_rate(annual_rate)
    tenure_months = int(loan.tenure_months.value)
    moratorium_months = int(loan.moratorium_months.value)
    treatment = loan.moratorium_treatment

    if tenure_months <= 0:
        raise ValueError("tenure_months must be > 0")
    if moratorium_months < 0:
        raise ValueError("moratorium_months must be >= 0")
    if moratorium_months > 0 and treatment is MoratoriumTreatment.NONE:
        raise ValueError(
            "moratorium_months > 0 requires an explicit moratorium_treatment other than "
            "NONE — the caller must state one (CLAUDE.md §17)"
        )

    notes: list[str] = []
    if moratorium_months == 0 and treatment is not MoratoriumTreatment.NONE:
        notes.append("moratorium_months is 0; the stated moratorium_treatment has no effect")

    principal_at_emi_start, moratorium_schedule, capitalised, accrued_paid = _apply_moratorium(
        principal, monthly_rate, moratorium_months, treatment
    )

    interest_1 = q_money(principal_at_emi_start * monthly_rate)
    emi = compute_emi(principal_at_emi_start, monthly_rate, tenure_months)

    if monthly_rate > 0 and emi <= interest_1:
        notes.append(
            f"the EMI (Rs {emi}) does not exceed the first period's interest "
            f"(Rs {interest_1}); this loan would never amortise"
        )
        return DebtScheduleResult(
            principal_inr=principal,
            annual_rate_pct=annual_rate,
            monthly_rate=monthly_rate,
            rate_convention="nominal_annual/12",
            tenure_months=tenure_months,
            moratorium_months=moratorium_months,
            moratorium_treatment=treatment,
            negative_amortisation=True,
            emi_inr=None,
            principal_at_emi_start_inr=principal_at_emi_start,
            capitalised_interest_inr=capitalised,
            accrued_interest_paid_at_emi_start_inr=accrued_paid,
            moratorium_schedule=moratorium_schedule,
            amortisation_schedule=[],
            total_interest_inr=None,
            total_payment_inr=None,
            notes=notes,
        )

    schedule: list[AmortisationPeriod] = []
    balance = principal_at_emi_start
    total_interest = Decimal("0.00")
    total_payment = Decimal("0.00")
    for period in range(1, tenure_months + 1):
        interest_k = q_money(balance * monthly_rate)
        if period < tenure_months:
            principal_k = q_money(emi - interest_k)
        else:
            principal_k = balance  # residual absorption: closes exactly at 0.00
        payment_k = q_money(principal_k + interest_k)
        closing = q_money(balance - principal_k)
        schedule.append(
            AmortisationPeriod(
                period=period,
                opening_balance_inr=balance,
                interest_inr=interest_k,
                principal_inr=principal_k,
                payment_inr=payment_k,
                closing_balance_inr=closing,
            )
        )
        total_interest += interest_k
        total_payment += payment_k
        balance = closing

    total_interest_all = q_money(total_interest + accrued_paid)
    total_payment_all = q_money(total_payment + accrued_paid)

    return DebtScheduleResult(
        principal_inr=principal,
        annual_rate_pct=annual_rate,
        monthly_rate=monthly_rate,
        rate_convention="nominal_annual/12",
        tenure_months=tenure_months,
        moratorium_months=moratorium_months,
        moratorium_treatment=treatment,
        negative_amortisation=False,
        emi_inr=emi,
        principal_at_emi_start_inr=principal_at_emi_start,
        capitalised_interest_inr=capitalised,
        accrued_interest_paid_at_emi_start_inr=accrued_paid,
        moratorium_schedule=moratorium_schedule,
        amortisation_schedule=schedule,
        total_interest_inr=total_interest_all,
        total_payment_inr=total_payment_all,
        notes=notes,
    )
