"""Time helpers. Timestamps are timezone-aware UTC (CLAUDE.md §4.2).

``now`` is injectable so deterministic engines and tests can pin it
(CLAUDE.md §28, §33).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)
