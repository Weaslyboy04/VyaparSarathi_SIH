"""Time helpers. Timestamps are timezone-aware UTC (CLAUDE.md §4.2).

``now`` is injectable so deterministic engines and tests can pin it
(CLAUDE.md §28, §33).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

Clock = Callable[[], datetime]

IST = ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(UTC)


def to_ist(dt: datetime) -> datetime:
    """An aware datetime -> its Asia/Kolkata wall-clock equivalent, for
    DISPLAY only. Canonical storage stays UTC (CLAUDE.md §4.2) — nothing
    that persists or gets compared should call this; it exists only for the
    handful of report strings a reader actually sees. Uses `zoneinfo`
    (never manual +5:30 arithmetic, which would silently ignore DST-style
    calendar edge cases even though India has none today)."""
    if dt.tzinfo is None:
        raise ValueError("to_ist() requires an aware datetime; got a naive one")
    return dt.astimezone(IST)
