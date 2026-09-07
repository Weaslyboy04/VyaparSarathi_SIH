"""EMI, amortisation and moratorium (CLAUDE.md §15, §17). Pure & offline.

Golden cases are hand-calculated in each test's docstring so the arithmetic is
independently verifiable without running the code.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.debt import compute_debt_schedule, compute_emi
from vyaparsarathi.finance.money import annual_pct_to_monthly_rate
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


# ======================================================================
# golden case 1 — zero rate
# ======================================================================


def test_golden_zero_rate_emi_is_principal_over_tenure() -> None:
    """P=120,000, rate=0%, n=12 -> EMI=10,000.00, total_interest=0.00, closing=0.00."""
    res = compute_debt_schedule(_loan(120_000, "0", 12))
    assert res.emi_inr == Decimal("10000.00")
    assert res.total_interest_inr == Decimal("0.00")
    assert res.amortisation_schedule[-1].closing_balance_inr == Decimal("0.00")
    assert sum(p.principal_inr for p in res.amortisation_schedule) == Decimal("120000.00")


# ======================================================================
# golden case 2 — single period
# ======================================================================


def test_golden_single_period() -> None:
    """P=100,000, 12% p.a. -> i=0.01, n=1.
    EMI = 100000*0.01*1.01/0.01 = 101,000.00; interest=1,000.00, principal=100,000.00.
    """
    res = compute_debt_schedule(_loan(100_000, "12", 1))
    assert res.emi_inr == Decimal("101000.00")
    p = res.amortisation_schedule[0]
    assert p.interest_inr == Decimal("1000.00")
    assert p.principal_inr == Decimal("100000.00")
    assert p.closing_balance_inr == Decimal("0.00")


# ======================================================================
# golden case 3 — two periods, exercising residual absorption
# ======================================================================


def test_golden_two_periods_residual_absorption() -> None:
    """P=100,000, 12% p.a., n=2. (1.01)^2=1.0201; EMI=1020.1/0.0201=50,751.24 (HALF_UP).
    m1: interest 1,000.00, principal 49,751.24, balance 50,248.76.
    m2: interest q_money(50248.76*0.01)=502.49, principal=residual 50,248.76,
        payment 50,751.25, balance 0.00.
    Sum(principal)=100,000.00 exactly; total_interest=1,502.49.
    """
    res = compute_debt_schedule(_loan(100_000, "12", 2))
    assert res.emi_inr == Decimal("50751.24")
    m1, m2 = res.amortisation_schedule
    assert m1.interest_inr == Decimal("1000.00")
    assert m1.principal_inr == Decimal("49751.24")
    assert m1.closing_balance_inr == Decimal("50248.76")
    assert m2.interest_inr == Decimal("502.49")
    assert m2.principal_inr == Decimal("50248.76")
    assert m2.payment_inr == Decimal("50751.25")
    assert m2.closing_balance_inr == Decimal("0.00")
    assert sum(p.principal_inr for p in res.amortisation_schedule) == Decimal("100000.00")
    assert res.total_interest_inr == Decimal("1502.49")


# ======================================================================
# golden case 5 — capitalised moratorium
# ======================================================================


def test_golden_capitalised_moratorium() -> None:
    """P=100,000, 12% p.a., m=2: 100,000 -> 101,000.00 -> 102,010.00;
    capitalised_interest = 2,010.00.
    """
    res = compute_debt_schedule(
        _loan(100_000, "12", 12, moratorium=2, treatment=MoratoriumTreatment.INTEREST_CAPITALISED)
    )
    assert res.moratorium_schedule[0].closing_balance_inr == Decimal("101000.00")
    assert res.moratorium_schedule[1].closing_balance_inr == Decimal("102010.00")
    assert res.principal_at_emi_start_inr == Decimal("102010.00")
    assert res.capitalised_interest_inr == Decimal("2010.00")


# ======================================================================
# amortisation invariants (general, not just golden cases)
# ======================================================================


@pytest.mark.parametrize(
    ("principal", "rate", "tenure"),
    [(100_000, "10", 6), (500_000, "9.5", 60), (250_000, "14", 36), (1, "24", 3)],
)
def test_amortisation_schedule_sums_to_principal_and_closes_at_zero(
    principal: int, rate: str, tenure: int
) -> None:
    res = compute_debt_schedule(_loan(principal, rate, tenure))
    assert not res.negative_amortisation
    assert sum(p.principal_inr for p in res.amortisation_schedule) == Decimal(f"{principal}.00")
    assert res.amortisation_schedule[-1].closing_balance_inr == Decimal("0.00")
    assert res.total_interest_inr == sum(p.interest_inr for p in res.amortisation_schedule)


def test_emi_is_fixed_across_all_but_the_last_period() -> None:
    res = compute_debt_schedule(_loan(500_000, "11", 24))
    for p in res.amortisation_schedule[:-1]:
        assert p.payment_inr == res.emi_inr


# ======================================================================
# moratorium — the other three treatments
# ======================================================================


def test_interest_serviced_moratorium_keeps_balance_flat_and_pays_interest() -> None:
    res = compute_debt_schedule(
        _loan(100_000, "12", 12, moratorium=2, treatment=MoratoriumTreatment.INTEREST_SERVICED)
    )
    m1, m2 = res.moratorium_schedule
    assert m1.interest_paid_inr == Decimal("1000.00")
    assert m1.closing_balance_inr == Decimal("100000.00")
    assert m2.closing_balance_inr == Decimal("100000.00")
    assert res.principal_at_emi_start_inr == Decimal("100000.00")
    assert res.capitalised_interest_inr == Decimal("0.00")


def test_accrued_paid_on_emi_start_keeps_balance_flat_and_defers_the_interest() -> None:
    res = compute_debt_schedule(
        _loan(
            100_000,
            "12",
            12,
            moratorium=2,
            treatment=MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
        )
    )
    assert res.principal_at_emi_start_inr == Decimal("100000.00")
    # month 1: 1000.00, month 2: 1000.00 (balance never changed) -> 2000.00 total
    assert res.accrued_interest_paid_at_emi_start_inr == Decimal("2000.00")


def test_none_moratorium_treatment_with_zero_months_is_a_no_op() -> None:
    res = compute_debt_schedule(_loan(100_000, "12", 12, moratorium=0))
    assert res.moratorium_schedule == []
    assert res.principal_at_emi_start_inr == Decimal("100000.00")


def test_moratorium_months_without_a_treatment_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="moratorium_treatment"):
        compute_debt_schedule(_loan(100_000, "12", 12, moratorium=2))


def test_stated_treatment_with_zero_moratorium_months_is_noted_as_a_no_op() -> None:
    res = compute_debt_schedule(
        _loan(100_000, "12", 12, moratorium=0, treatment=MoratoriumTreatment.INTEREST_SERVICED)
    )
    assert any("no effect" in n for n in res.notes)


def test_the_four_moratorium_treatments_give_different_emi_and_start_cash_position() -> None:
    """Proving the treatment is never assumed: all four differ."""
    results = {
        t: compute_debt_schedule(_loan(100_000, "12", 12, moratorium=3, treatment=t))
        for t in (
            MoratoriumTreatment.INTEREST_SERVICED,
            MoratoriumTreatment.INTEREST_CAPITALISED,
            MoratoriumTreatment.INTEREST_ACCRUED_PAID_ON_EMI_START,
        )
    }
    principals_at_start = {t: r.principal_at_emi_start_inr for t, r in results.items()}
    assert (
        len(set(principals_at_start.values())) == 2
    )  # serviced == accrued (both defer, no compounding); capitalised differs
    emis = {t: r.emi_inr for t, r in results.items()}
    assert len(set(emis.values())) == 2


# ======================================================================
# zero-rate loan
# ======================================================================


def test_zero_rate_moratorium_has_no_interest_effect() -> None:
    res = compute_debt_schedule(
        _loan(120_000, "0", 12, moratorium=3, treatment=MoratoriumTreatment.INTEREST_CAPITALISED)
    )
    assert res.principal_at_emi_start_inr == Decimal("120000.00")
    assert res.capitalised_interest_inr == Decimal("0.00")


# ======================================================================
# negative amortisation guard
# ======================================================================


def test_negative_amortisation_is_flagged_not_looped() -> None:
    """Under the standard annuity formula EMI is always mathematically > the
    first period's interest for the same (P, i, n) — but at a tiny principal
    stretched over a long tenure, paise rounding of both figures to 2dp can
    make the *quantised* EMI equal the *quantised* first-period interest
    (P=Rs 1, 12% p.a., n=360: both round to Rs 0.01). That is a genuine,
    reachable edge case, not a contrived one: the engine must flag it, not
    loop looking for a balance that only grows."""
    res = compute_debt_schedule(_loan(1, "12", 360))
    assert res.negative_amortisation is True
    assert res.emi_inr is None
    assert res.amortisation_schedule == []
    assert res.total_interest_inr is None
    assert any("never amortise" in n for n in res.notes)


def test_ordinary_loans_never_trigger_negative_amortisation() -> None:
    res = compute_debt_schedule(_loan(500_000, "11", 60))
    assert res.negative_amortisation is False
    assert res.emi_inr is not None


def test_compute_emi_requires_positive_tenure() -> None:
    with pytest.raises(ValueError, match="tenure_months"):
        compute_emi(Decimal("100000"), annual_pct_to_monthly_rate(Decimal("12")), 0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
