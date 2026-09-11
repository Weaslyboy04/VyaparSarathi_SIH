"""`utils/time.py::to_ist` — display-only UTC -> Asia/Kolkata conversion
(CLAUDE.md §4.2: canonical storage stays UTC; this is a rendering-boundary
helper, never used to persist or compare timestamps). Offline; pure.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from vyaparsarathi.utils.time import to_ist


def test_ist_is_five_and_a_half_hours_ahead_of_utc() -> None:
    utc_dt = datetime(2026, 9, 9, 9, 0, tzinfo=UTC)
    ist_dt = to_ist(utc_dt)
    assert ist_dt.hour == 14
    assert ist_dt.minute == 30
    assert ist_dt.utcoffset() == timedelta(hours=5, minutes=30)


def test_ist_conversion_uses_zoneinfo_not_a_naive_instant() -> None:
    """The same instant, converted, must still compare equal — this is a
    display re-expression, never a different point in time."""
    utc_dt = datetime(2026, 9, 9, 23, 45, tzinfo=UTC)
    ist_dt = to_ist(utc_dt)
    assert ist_dt == utc_dt
    # crossing midnight IST from a late-UTC-evening timestamp
    assert ist_dt.day == utc_dt.day + 1


def test_to_ist_rejects_a_naive_datetime() -> None:
    """A naive datetime has no defined offset to convert from — accepting
    one silently would risk treating a wall-clock value as UTC by accident."""
    import pytest

    with pytest.raises(ValueError):
        to_ist(datetime(2026, 9, 9, 9, 0))
