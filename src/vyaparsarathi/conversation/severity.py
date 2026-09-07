"""A total ordering over how badly a turn/session degraded (CLAUDE.md §33,
§25 Phase 6). Fixes `scripts/discover_businesses.py`'s "last write wins" exit
code (its own known defect — a later, milder warning silently overwrote an
earlier ERROR's exit code); here the maximum always wins, deterministically.
"""

from __future__ import annotations

from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"  # normal operation, or an honest low-confidence result
    DEGRADED = "degraded"  # a source returned nothing / partial coverage; still answered
    BLOCKED = "blocked"  # the turn needs the user (a question, a choice)
    ERROR = "error"  # a source is down / an internal inconsistency was caught

    @property
    def rank(self) -> int:
        return _RANK[self]


_RANK: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.DEGRADED: 1,
    Severity.BLOCKED: 2,
    Severity.ERROR: 3,
}

_EXIT_CODE: dict[Severity, int] = {
    Severity.INFO: 0,
    Severity.DEGRADED: 0,
    Severity.BLOCKED: 1,
    Severity.ERROR: 2,
}


def max_severity(*severities: Severity) -> Severity:
    """The worst of any number of severities; `INFO` if none are given."""
    if not severities:
        return Severity.INFO
    return max(severities, key=lambda s: s.rank)


def exit_code(severity: Severity) -> int:
    return _EXIT_CODE[severity]


__all__ = ["Severity", "exit_code", "max_severity"]
