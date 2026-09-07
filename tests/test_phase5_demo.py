"""Phase 5 demo scenario as a gate test (CLAUDE.md §18, §22, §23).

`scripts/phase5_demo.py` builds the plan/query/evidence and checks its own
invariants; this test imports those same builders so the demo script and the
gate assert exactly the same behaviour, mirroring
`tests/test_finance_demos.py`'s relationship to `scripts/phase4_demo.py`.
"""

from __future__ import annotations

import pytest
from scripts.phase5_demo import check_common, run

from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus
from vyaparsarathi.models.finance import InputKind


def test_phase5_demo_has_no_failing_invariants() -> None:
    assert check_common() == []


def test_phase5_demo_binds_evidence_and_lowers_assumption_share() -> None:
    unbound_result, bound_result = run()
    assert bound_result.assumptions.assumption_share < unbound_result.assumptions.assumption_share
    assert bound_result.assumptions.counts_by_kind.get(InputKind.SOURCED, 0) >= 1


def test_phase5_demo_never_reaches_an_impossible_status() -> None:
    _unbound_result, bound_result = run()
    assert bound_result.status in set(FinancialFeasibilityStatus)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
