"""Money and rate arithmetic for the financial engine (CLAUDE.md §4.2, §15).

**Decision:** Indian rupees as `decimal.Decimal`, quantised to paise (2 dp),
`ROUND_HALF_UP`, never `float`. Binary floating point cannot represent Rs 0.01
exactly, which would make golden-value tests platform-fragile; integer paise
would work but pushes manual scaling into every multiplication and division
along a ratio-heavy path (interest, DSCR, contribution margin, break-even).
`Decimal` is stdlib, deterministic, and lets every rounding point below be
named and tested individually.

The module-level `decimal` context (``decimal.getcontext()``) is never
mutated — that would be hidden global state. Every rounding decision in this
engine is an explicit call to one of the functions below, at a named point.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Annotated, Any

from pydantic import AfterValidator, BeforeValidator

from vyaparsarathi.errors import FinancialInputError

MONEY_Q = Decimal("0.01")  # paise
RATE_Q = Decimal("0.00000001")  # 8 dp — enough precision for a monthly rate
RATIO_Q = Decimal("0.0001")  # 4 dp — DSCR / margin ratios, internal precision


def rupees(value: int | str | Decimal) -> Decimal:
    """Build a `Decimal` rupee amount. Rejects `float` — CLAUDE.md §4.2 forbids
    binary floating point for money; a float argument almost always means a
    literal like ``1.1`` that cannot be represented exactly. Pass an `int`,
    a `str` (``"1234.56"``), or a `Decimal` instead."""
    if isinstance(value, float):
        raise FinancialInputError(
            f"rupees() rejects float ({value!r}); pass an int, str, or Decimal so the "
            "value is exact (CLAUDE.md §4.2)."
        )
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise FinancialInputError(f"not a valid rupee amount: {value!r}") from exc


def q_money(value: Decimal) -> Decimal:
    """Quantise to paise, HALF_UP. The single rounding rule for every stored or
    emitted money figure in this engine."""
    return value.quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def q_rate(value: Decimal) -> Decimal:
    """Quantise a period interest rate (a fraction, e.g. 0.01 for 1%/month) to
    8 dp. Rates are not money; they get their own, finer, quantum."""
    return value.quantize(RATE_Q, rounding=ROUND_HALF_UP)


def q_ratio(value: Decimal) -> Decimal:
    """Quantise a dimensionless ratio (DSCR, contribution-margin ratio, ...) to
    4 dp internally. Results narrow this further to 2 dp for display."""
    return value.quantize(RATIO_Q, rounding=ROUND_HALF_UP)


def to_paise(value: Decimal) -> int:
    """Exact integer paise, for equality assertions in tests. Raises if
    ``value`` is not already quantised to the paise grid."""
    scaled = value * 100
    if scaled != scaled.to_integral_value():
        raise ValueError(f"{value} is not quantised to paise; call q_money() first")
    return int(scaled)


def whole_rupees(value: Decimal) -> int:
    """Round to the nearest whole rupee, HALF_UP. Used only at the Phase 3
    handoff (`FinancialFitInput.capital_gap_inr` etc. are `int`) and for
    display — never inside the calculation pipeline itself."""
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def annual_pct_to_monthly_rate(annual_rate_pct: Decimal) -> Decimal:
    """Nominal-annual-over-12 monthly rate — the Indian reducing-balance
    convention this engine uses throughout (recorded on results as
    ``rate_convention = "nominal_annual/12"``). Deliberately not an effective
    (compounded) rate's 12th root."""
    return q_rate(annual_rate_pct / Decimal(100) / Decimal(12))


# --- pydantic field types -------------------------------------------------
#
# Reusable annotated types so every money / rate field on every Phase 4 model
# rejects `float` at construction (not just at arithmetic time) and is always
# already quantised. Pydantic v2 coerces a bare `float` into `Decimal` by
# default, which silently reintroduces binary floating-point error before our
# own arithmetic ever runs — these validators close that hole at the model
# boundary.


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise FinancialInputError(
            f"money/rate fields reject float ({value!r}); pass an int, str, or Decimal "
            "(CLAUDE.md §4.2)."
        )
    return value


MoneyINR = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(q_money)]
"""A rupee amount: `Decimal`, quantised to paise, never accepted as `float`."""

RateFrac = Annotated[Decimal, BeforeValidator(_reject_float), AfterValidator(q_rate)]
"""A period interest rate as a fraction (e.g. `Decimal("0.01")` for 1%/month)."""
