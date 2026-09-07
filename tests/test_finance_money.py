"""Money and rate arithmetic (CLAUDE.md §4.2). Pure & offline."""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.finance.money import (
    annual_pct_to_monthly_rate,
    q_money,
    q_rate,
    q_ratio,
    rupees,
    to_paise,
    whole_rupees,
)

# ======================================================================
# rupees() — rejects float, accepts int/str/Decimal
# ======================================================================


def test_rupees_rejects_float() -> None:
    with pytest.raises(FinancialInputError):
        rupees(1.1)  # type: ignore[arg-type]


def test_rupees_accepts_int_str_and_decimal() -> None:
    assert rupees(100) == Decimal("100")
    assert rupees("100.50") == Decimal("100.50")
    assert rupees(Decimal("100.50")) == Decimal("100.50")


def test_rupees_rejects_garbage_string() -> None:
    with pytest.raises(FinancialInputError):
        rupees("not-a-number")


# ======================================================================
# quantisation — HALF_UP at the paise / rate / ratio grid
# ======================================================================


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (Decimal("10.005"), Decimal("10.01")),  # half-up rounds away from zero
        (Decimal("10.004"), Decimal("10.00")),
        (Decimal("10.995"), Decimal("11.00")),
        (Decimal("100"), Decimal("100.00")),
    ],
)
def test_q_money_rounds_half_up_to_paise(raw: Decimal, expected: Decimal) -> None:
    assert q_money(raw) == expected


def test_q_rate_quantises_to_eight_decimal_places() -> None:
    r = q_rate(Decimal("12") / Decimal(100) / Decimal(12))
    assert r == Decimal("0.01000000")


def test_q_ratio_quantises_to_four_decimal_places() -> None:
    assert q_ratio(Decimal("1") / Decimal(3)) == Decimal("0.3333")


def test_annual_pct_to_monthly_rate_is_nominal_annual_over_twelve() -> None:
    # Explicitly NOT an effective (compounded) rate's 12th root.
    assert annual_pct_to_monthly_rate(Decimal("12")) == Decimal("0.01000000")
    assert annual_pct_to_monthly_rate(Decimal("0")) == Decimal("0.00000000")


# ======================================================================
# to_paise() / whole_rupees()
# ======================================================================


def test_to_paise_is_exact_for_a_quantised_value() -> None:
    assert to_paise(Decimal("1234.56")) == 123456


def test_to_paise_raises_on_sub_paise_precision() -> None:
    with pytest.raises(ValueError):
        to_paise(Decimal("1234.567"))


def test_whole_rupees_rounds_half_up() -> None:
    assert whole_rupees(Decimal("1234.50")) == 1235
    assert whole_rupees(Decimal("1234.49")) == 1234


# ======================================================================
# determinism — repeat calls are byte-identical
# ======================================================================


def test_q_money_is_deterministic_across_repeated_calls() -> None:
    values = [Decimal("1000.005"), Decimal("999.995"), Decimal("0.005")]
    once = [q_money(v) for v in values]
    again = [q_money(v) for v in values]
    assert once == again


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
