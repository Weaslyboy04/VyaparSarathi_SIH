"""`conversation/workflow.py` + `planner.py` (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from vyaparsarathi.conversation.planner import NextActionKind, decide
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName, StepId
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding
from vyaparsarathi.conversation.workflow import STEP_SPECS, is_acyclic, ready_steps

# StepId -> the real engine callable it eventually dispatches to in
# llm/tools.py. Only pure, directly-importable callables are checked here
# (impure steps' runners live behind network/disk adapters); this is exactly
# the "curated engine-argument -> declared-input table" the approved plan's
# Risks section calls for.
_ENGINE_CALLABLES: dict[StepId, tuple[str, str]] = {
    StepId.ANALYZE: ("vyaparsarathi.market.classifier", "analyze_competitors"),
    StepId.METRICS: ("vyaparsarathi.market.metrics", "compute_competition_metrics"),
    StepId.DEMAND_SIGNALS: ("vyaparsarathi.market.demand", "compute_demand_signals"),
    StepId.ASSESS_MARKET: ("vyaparsarathi.market.assessment", "assess_market"),
    StepId.OPPORTUNITY: ("vyaparsarathi.market.opportunity", "score_opportunities"),
    StepId.BIND_PLAN: ("vyaparsarathi.knowledge.plan_binding", "bind_sourced_inputs"),
    StepId.ASSESS_FINANCE: ("vyaparsarathi.finance.assessment", "assess_financials"),
    StepId.FINANCIAL_FIT: ("vyaparsarathi.finance.fit", "to_financial_fit"),
    StepId.RECOMMEND: ("vyaparsarathi.conversation.recommendation", "combine"),
}


def test_dag_is_acyclic() -> None:
    assert is_acyclic()


def test_every_step_has_a_spec() -> None:
    assert set(STEP_SPECS) == set(StepId)


@pytest.mark.parametrize(
    "step,target", sorted(_ENGINE_CALLABLES.items(), key=lambda kv: kv[0].value)
)
def test_config_kwarg_matches_the_real_signature(step: StepId, target: tuple[str, str]) -> None:
    import importlib

    module_name, func_name = target
    func = getattr(importlib.import_module(module_name), func_name)
    params = inspect.signature(func).parameters
    declared = STEP_SPECS[step].config_kwarg
    if declared is None:
        assert "config" not in params and "cfg" not in params, (
            f"{module_name}.{func_name} has a config/cfg parameter but StepSpec declares none"
        )
    else:
        assert declared in params, f"{module_name}.{func_name} has no {declared!r} parameter"


def test_ready_steps_requires_dependencies() -> None:
    assert StepId.RESOLVE_PROPOSED in ready_steps(
        frozenset(), frozenset({SlotName.PROPOSED_BUSINESS_TEXT})
    )
    assert StepId.DISCOVER not in ready_steps(frozenset(), frozenset({SlotName.LOCATION_TEXT}))
    assert StepId.DISCOVER in ready_steps(
        frozenset({StepId.RESOLVE_PROPOSED}), frozenset({SlotName.LOCATION_TEXT})
    )


def test_ready_steps_does_not_require_optional_dependencies() -> None:
    ready = ready_steps(frozenset({StepId.OPPORTUNITY_EVIDENCE}), frozenset())
    assert StepId.OPPORTUNITY in ready  # FINANCIAL_FIT optional, not required


def _session_requesting(requested: StepId | None) -> tuple[ConversationSession, TurnUnderstanding]:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now)
    understanding = TurnUnderstanding(intent=Intent.PROVIDE_INFO, requested_step=requested)
    return session, understanding


def test_requested_step_can_only_narrow_never_expand() -> None:
    """A `requested_step` that is not in the ready set must never be run —
    it can only select among what was already ready."""
    from vyaparsarathi.conversation.deltas import apply_understanding
    from vyaparsarathi.conversation.understanding import SlotUpdateInput
    from vyaparsarathi.models.parameters import ValueNormalization

    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now)
    # Only PROPOSED_BUSINESS_TEXT is satisfied -> only RESOLVE_PROPOSED is ready.
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.PROPOSED_BUSINESS_TEXT,
                    raw_text="a grocery shop",
                    value_token="a grocery shop",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=1,
    )
    # Ask for a step that is nowhere near ready (RECOMMEND needs 6+ upstream steps).
    understanding = TurnUnderstanding(intent=Intent.PROVIDE_INFO, requested_step=StepId.RECOMMEND)
    action = decide(session, understanding)
    assert action.kind is NextActionKind.RUN_STEP
    assert action.step is StepId.RESOLVE_PROPOSED  # narrowed to what was actually ready


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
