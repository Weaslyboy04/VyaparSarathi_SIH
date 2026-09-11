"""Priority 8 fix from the SIH26091 judge feedback pass (CLAUDE.md §17, §25
Phase 8): the DPR shows EMI, moratorium and DSCR but no repayment timeline —
when repayment actually starts, what happens to interest during the
moratorium, and how the outstanding balance moves over time. `finance/debt.py`
already computes a full month-by-month moratorium + amortisation schedule
(`DebtScheduleResult`); this module only aggregates that into a
reader-sized quarterly view — a pure re-composition, no new financial
arithmetic (CLAUDE.md §15: "DPR ... only composes existing engine outputs").
Offline; pure.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.dpr.repayment_schedule import build_quarterly_repayment_schedule
from vyaparsarathi.finance.debt import compute_debt_schedule
from vyaparsarathi.models.finance import (
    FinancialInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    Unit,
)


def _fi(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _loan(
    principal: int,
    rate_pct: str,
    tenure: int,
    moratorium: int = 0,
    treatment: MoratoriumTreatment = MoratoriumTreatment.NONE,
) -> LoanTerms:
    return LoanTerms(
        principal_requested=_fi(principal, Unit.INR),
        interest_rate_pct=_fi(Decimal(rate_pct), Unit.PERCENT_PER_ANNUM),
        tenure_months=_fi(tenure, Unit.MONTHS),
        moratorium_months=_fi(moratorium, Unit.MONTHS),
        moratorium_treatment=treatment,
    )


def test_mixed_quarter_spans_moratorium_and_repayment() -> None:
    """1-month moratorium + 5-month tenure = 6 calendar months = 2 quarters.
    Q1 (months 1-3) straddles the moratorium/repayment boundary (month 1
    moratorium, months 2-3 repayment) — it must say so, not silently pick one."""
    debt = compute_debt_schedule(
        _loan(100_000, "12", 5, moratorium=1, treatment=MoratoriumTreatment.INTEREST_SERVICED)
    )
    quarters = build_quarterly_repayment_schedule(debt)
    assert len(quarters) == 2

    q1 = quarters[0]
    assert "1" in q1.label and "3" in q1.label
    status = q1.status.lower()
    assert "moratorium" in status and "repayment" in status

    # principal is only ever paid down during repayment months — cross-check
    # against a plain sum over the raw engine output, independent of the
    # aggregation logic under test.
    expected_principal_q1 = sum(
        (p.principal_inr for p in debt.amortisation_schedule if p.period <= 2),
        Decimal("0.00"),
    )
    assert Decimal(q1.principal_paid.raw or "0") == expected_principal_q1

    q2 = quarters[1]
    assert q2.status.lower() == "repayment"
    last_closing = debt.amortisation_schedule[-1].closing_balance_inr
    assert Decimal(q2.closing_balance.raw or "0") == last_closing


def test_final_partial_quarter_is_still_included() -> None:
    """0-month moratorium + 4-month tenure = 4 calendar months: a full
    quarter (1-3) plus a single trailing month (4) — the partial quarter
    must not be silently dropped."""
    debt = compute_debt_schedule(_loan(50_000, "0", 4))
    quarters = build_quarterly_repayment_schedule(debt)
    assert len(quarters) == 2
    assert "4" in quarters[1].label
    assert quarters[1].status.lower() == "repayment"
    assert Decimal(quarters[1].closing_balance.raw or "0") == Decimal("0.00")


def test_accrued_interest_paid_at_emi_start_shows_in_first_repayment_quarter() -> None:
    """Under INTEREST_ACCRUED_PAID_ON_EMI_START, moratorium-period interest is
    deferred and settled as a lump sum the moment EMI begins — the quarter
    that first month falls in must carry that lump sum and say so, not lose
    it silently in a per-period-only view."""
    debt = compute_debt_schedule(
        _loan(
            100_000,
            "12",
            2,
            moratorium=2,
            treatment=MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
        )
    )
    assert debt.accrued_interest_paid_at_emi_start_inr > Decimal("0.00")
    quarters = build_quarterly_repayment_schedule(debt)
    first_repayment_quarter = next(q for q in quarters if "repayment" in q.status.lower())
    assert "moratorium" in first_repayment_quarter.note.lower()
    interest_paid = Decimal(first_repayment_quarter.interest_paid.raw or "0")
    assert interest_paid >= debt.accrued_interest_paid_at_emi_start_inr


def test_negative_amortisation_yields_no_schedule() -> None:
    """`compute_debt_schedule` already refuses to fabricate a schedule that
    never amortises (empty `amortisation_schedule`); this must not crash.
    P=Rs 1, 12% p.a., n=360 is the proven edge case from
    `test_finance_debt.py::test_negative_amortisation_is_flagged_not_looped`."""
    debt = compute_debt_schedule(_loan(1, "12", 360))
    assert debt.negative_amortisation is True
    assert build_quarterly_repayment_schedule(debt) == ()
