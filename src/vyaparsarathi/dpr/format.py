"""Display-only formatting for the DPR (CLAUDE.md §4.2 units, §3.5 no false
precision). PURE — no I/O, no clock.

Every function here turns an already-computed value into a string for the
report. None of them round or reinterpret a figure in a way that changes its
meaning: money is grouped in the Indian 2-2-3 convention and shown to the
rupee, percentages keep one decimal, and a `None` becomes an explicit phrase,
never a blank or a zero.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

RUPEE = "₹"  # ₹

NOT_AVAILABLE = "Not available from current evidence"
INPUT_REQUIRED = "Additional input required"

_LAKH = Decimal("100000")
_CRORE = Decimal("10000000")


def _to_decimal(value: object) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):  # guard: bool is an int subclass
        return Decimal(int(value))
    if isinstance(value, int):
        return Decimal(value)
    return Decimal(str(value))


def group_indian(digits: str) -> str:
    """`"650000"` -> `"6,50,000"`. Operates on a bare digit string (no sign,
    no decimal part). The last three digits are one group; every group before
    that is two digits (CLAUDE.md's Indian-numbering note in §4.2)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    parts: list[str] = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


def format_inr(value: object | None, *, paise: bool = False) -> str:
    """`650000` -> `"₹6,50,000"`. `None` -> the not-available phrase. With
    `paise=True`, two decimal places are kept (for annexure precision)."""
    if value is None:
        return NOT_AVAILABLE
    amount = _to_decimal(value)
    sign = "-" if amount < 0 else ""
    amount = abs(amount)
    if paise:
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        whole, _, frac = f"{amount:f}".partition(".")
        return f"{sign}{RUPEE}{group_indian(whole)}.{(frac or '00')[:2].ljust(2, '0')}"
    whole = f"{amount.quantize(Decimal('1'), rounding=ROUND_HALF_UP):f}"
    return f"{sign}{RUPEE}{group_indian(whole)}"


def format_inr_words(value: object | None) -> str:
    """A human phrasing alongside the exact figure: `650000` ->
    `"₹6,50,000 (about ₹6.5 lakh)"`. Only adds the words when they help
    (>= ₹1 lakh); never replaces the exact figure with them."""
    if value is None:
        return NOT_AVAILABLE
    amount = _to_decimal(value)
    exact = format_inr(amount)
    magnitude = abs(amount)
    if magnitude >= _CRORE:
        crore = (magnitude / _CRORE).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{exact} (about {RUPEE}{_trim(crore)} crore)"
    if magnitude >= _LAKH:
        lakh = (magnitude / _LAKH).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return f"{exact} (about {RUPEE}{_trim(lakh)} lakh)"
    return exact


def _trim(d: Decimal) -> str:
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def format_ratio_pct(value: object | None, *, decimals: int = 1) -> str:
    """A ratio in [0, 1] -> a percentage string. `0.7` -> `"70.0%"`."""
    if value is None:
        return NOT_AVAILABLE
    pct = _to_decimal(value) * 100
    return f"{pct.quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP):f}%"


def format_pct_value(value: object | None, *, decimals: int = 2) -> str:
    """An already-percentage figure (e.g. an 8 or 11.5 annual rate) -> `"8%"` /
    `"11.5%"`. Trailing zeros in the fractional part are trimmed; the integer
    part is never touched (no exponent notation)."""
    if value is None:
        return NOT_AVAILABLE
    pct = _to_decimal(value).quantize(Decimal(10) ** -decimals, rounding=ROUND_HALF_UP)
    whole, _, frac = f"{pct:f}".partition(".")
    frac = frac.rstrip("0")
    return f"{whole}.{frac}%" if frac else f"{whole}%"


def format_months(value: object | None) -> str:
    if value is None:
        return NOT_AVAILABLE
    n = int(_to_decimal(value))
    if n % 12 == 0 and n >= 12:
        years = n // 12
        return f"{n} months ({years} year{'s' if years != 1 else ''})"
    return f"{n} month{'s' if n != 1 else ''}"


def format_confidence(value: object | None) -> str:
    """A 0..1 coverage figure -> `"76%"`; `None` -> not-available."""
    if value is None:
        return NOT_AVAILABLE
    return f"{round(float(_to_decimal(value)) * 100)}%"


def humanise_token(token: str) -> str:
    """`"proceed_with_caution"` -> `"Proceed with caution"`."""
    return token.replace("_", " ").strip().capitalize()


def format_distance_m(value: object | None) -> str:
    if value is None:
        return NOT_AVAILABLE
    metres = float(_to_decimal(value))
    if metres >= 1000:
        return f"{metres / 1000:.1f} km"
    return f"{round(metres)} m"


__all__ = [
    "INPUT_REQUIRED",
    "NOT_AVAILABLE",
    "RUPEE",
    "format_confidence",
    "format_distance_m",
    "format_inr",
    "format_inr_words",
    "format_months",
    "format_pct_value",
    "format_ratio_pct",
    "group_indian",
    "humanise_token",
]
