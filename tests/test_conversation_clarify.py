"""`conversation/clarify.py` (CLAUDE.md §25 Phase 6).

Asserts the `MISSING_DRIVER_QUESTIONS` map's keys equal exactly what
`finance/assessment.py::_missing_core_drivers` can emit, so a Phase 4 wording
change fails this suite loudly instead of falling back to raw text silently.
"""

from __future__ import annotations

import pytest

from vyaparsarathi.conversation.clarify import MISSING_DRIVER_QUESTIONS, question_for_missing_driver
from vyaparsarathi.finance.assessment import _missing_core_drivers
from vyaparsarathi.models.finance import (
    FinancialPlanInput,
    FinancingInput,
    OperatingCostInput,
    ProjectCostInput,
    RevenueInput,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory


def _empty_plan() -> FinancialPlanInput:
    return FinancialPlanInput(
        category=BusinessCategory.GROCERY,
        profile=EntrepreneurProfile(),
        project_cost=ProjectCostInput(),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(),
        operating_costs=OperatingCostInput(),
        financing=FinancingInput(),
        horizon_months=12,
    )


def test_missing_driver_questions_cover_every_possible_message() -> None:
    all_missing = set(_missing_core_drivers(_empty_plan()))
    assert all_missing, "expected _missing_core_drivers to report gaps for an empty plan"
    assert set(MISSING_DRIVER_QUESTIONS) == all_missing


def test_question_for_missing_driver_never_falls_back_for_a_real_message() -> None:
    for text in _missing_core_drivers(_empty_plan()):
        question = question_for_missing_driver(text)
        assert question != f"I still need: {text}"


def test_question_for_missing_driver_falls_back_gracefully_for_an_unknown_message() -> None:
    assert question_for_missing_driver("something new") == "I still need: something new"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
