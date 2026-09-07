"""`conversation/outcomes.py` totality + `conversation/severity.py`
(CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import pytest

from vyaparsarathi.conversation.outcomes import OUTCOME_TABLES, outcome_for
from vyaparsarathi.conversation.severity import Severity, exit_code, max_severity


@pytest.mark.parametrize(
    "enum_cls,table", sorted(OUTCOME_TABLES.items(), key=lambda kv: kv[0].__name__)
)
def test_every_enum_member_has_an_outcome(enum_cls: type, table: dict) -> None:
    for member in enum_cls:
        assert member in table, f"{enum_cls.__name__}.{member.name} has no OutcomeSpec"


def test_outcome_for_dispatches_by_type() -> None:
    from vyaparsarathi.models.results import DiscoveryStatus

    spec = outcome_for(DiscoveryStatus.NO_RESULTS)
    assert spec.severity is Severity.DEGRADED


def test_max_severity_beats_last_write_wins() -> None:
    # A later, milder severity must never overwrite an earlier, worse one —
    # the exact defect in scripts/discover_businesses.py:706-723.
    assert max_severity(Severity.ERROR, Severity.INFO) is Severity.ERROR
    assert max_severity(Severity.INFO, Severity.ERROR) is Severity.ERROR
    assert max_severity() is Severity.INFO
    assert max_severity(Severity.BLOCKED, Severity.DEGRADED) is Severity.BLOCKED


def test_exit_code_is_nonzero_only_for_blocked_or_error() -> None:
    assert exit_code(Severity.INFO) == 0
    assert exit_code(Severity.DEGRADED) == 0
    assert exit_code(Severity.BLOCKED) != 0
    assert exit_code(Severity.ERROR) != 0
    assert exit_code(Severity.ERROR) != exit_code(Severity.BLOCKED)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
