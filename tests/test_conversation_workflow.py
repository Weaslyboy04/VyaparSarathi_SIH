"""`conversation/workflow.py` + `planner.py` (CLAUDE.md §25 Phase 6)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime

import pytest

from vyaparsarathi.conversation.clarify import question_for_item, question_for_missing_driver
from vyaparsarathi.conversation.deltas import apply_understanding
from vyaparsarathi.conversation.planner import NextActionKind, decide
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    StepArtifact,
    StepId,
)
from vyaparsarathi.conversation.severity import Severity
from vyaparsarathi.conversation.understanding import Intent, SlotUpdateInput, TurnUnderstanding
from vyaparsarathi.conversation.workflow import STEP_SPECS, is_acyclic, ready_steps
from vyaparsarathi.models.parameters import ValueNormalization

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
    StepId.SCHEME_CAPACITY: ("vyaparsarathi.finance.capacity", "compute_scheme_capacity"),
    StepId.STRUCTURE_FINANCE: ("vyaparsarathi.finance.structuring", "structure_financing"),
    StepId.ASSESS_FINANCE: ("vyaparsarathi.finance.assessment", "assess_financials"),
    StepId.FINANCIAL_FIT: ("vyaparsarathi.finance.fit", "to_financial_fit"),
    StepId.RECOMMEND: ("vyaparsarathi.conversation.recommendation", "combine"),
    StepId.SWOT: ("vyaparsarathi.conversation.swot", "build_swot"),
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


def _discover_artifact(status: str, *, warnings: list[str] | None = None) -> StepArtifact:
    return StepArtifact(
        step=StepId.DISCOVER,
        fingerprint="fp-discover",
        computed_on_turn=1,
        payload={
            "status": status,
            "query": None,
            "resolved_place": None,
            "businesses": [],
            "sources_queried": [],
            "confidence": 0.0,
            "warnings": warnings or [],
        },
    )


def _resolve_proposed_artifact() -> StepArtifact:
    return StepArtifact(
        step=StepId.RESOLVE_PROPOSED,
        fingerprint="fp-resolve-proposed",
        computed_on_turn=1,
        payload={"raw_text": "grocery", "category": "grocery", "resolved": True, "subtypes": []},
    )


# Every step reachable from RESOLVE_PROPOSED without ever going through
# DISCOVER (DISCOVER is only `optional_steps` for FINANCE_KNOWLEDGE) — these
# must keep completing normally even while DISCOVER has hard-failed, so the
# "hard failure" tests below pre-complete this whole branch with trivial
# artifacts (planner.py never reads their payload contents, only presence)
# to isolate and exhaust it, leaving only the DISCOVER-blocked branch to
# prove out.
_INDEPENDENT_FINANCE_BRANCH = (
    StepId.BUILD_PLAN,
    StepId.FINANCE_KNOWLEDGE,
    StepId.BIND_PLAN,
    StepId.SCHEME_CAPACITY,
    StepId.STRUCTURE_FINANCE,
    StepId.ASSESS_FINANCE,
    StepId.FINANCIAL_FIT,
)


def _trivial_artifact(step: StepId) -> StepArtifact:
    return StepArtifact(step=step, fingerprint=f"fp-{step.value}", computed_on_turn=1, payload={})


def _session_with_discover_status(
    status: str, *, warnings: list[str] | None = None, exhaust_independent_branch: bool = False
) -> tuple[ConversationSession, TurnUnderstanding]:
    """A session where, but for DISCOVER's failure, ANALYZE would already be
    ready (RESOLVE_PROPOSED — ANALYZE's other required step — is also
    completed) — so this actually exercises the new filtering, not just an
    absence of upstream progress."""
    now = datetime.now(UTC)
    artifacts = {
        StepId.DISCOVER: _discover_artifact(status, warnings=warnings),
        StepId.RESOLVE_PROPOSED: _resolve_proposed_artifact(),
    }
    if exhaust_independent_branch:
        artifacts.update({step: _trivial_artifact(step) for step in _INDEPENDENT_FINANCE_BRANCH})
    session = ConversationSession(
        session_id="s1", created_at=now, updated_at=now, artifacts=artifacts
    )
    understanding = TurnUnderstanding(intent=Intent.PROVIDE_INFO)
    return session, understanding


def test_discover_hard_failure_blocks_dependent_steps_and_returns_stage_failed() -> None:
    """A `LOCATION_NOT_FOUND` DISCOVER artifact must never let ANALYZE (or
    anything else requiring DISCOVER) become ready, and `decide()` must
    return a terminal STAGE_FAILED — never RUN_STEP, DELIVER_FINAL, or
    DELIVER_PARTIAL built from that failed prerequisite."""
    # Sanity: without the failure-awareness fix, ANALYZE WOULD be ready here —
    # `workflow.ready_steps()` itself is unaware of status and only checks
    # artifact presence, confirming this test exercises real filtering.
    assert StepId.ANALYZE in ready_steps(
        frozenset({StepId.DISCOVER, StepId.RESOLVE_PROPOSED}), frozenset()
    )

    session, understanding = _session_with_discover_status(
        "location_not_found",
        warnings=[
            "Could not resolve 'Nowhereville': Nominatim returned HTTP 403 for 'Nowhereville'"
        ],
        exhaust_independent_branch=True,
    )
    action = decide(session, understanding)
    assert action.kind is NextActionKind.STAGE_FAILED
    assert action.step is StepId.DISCOVER
    assert action.severity is Severity.BLOCKED
    assert "location" in action.message.lower()
    assert "403" in action.message  # the underlying technical detail is surfaced


def test_discover_source_unavailable_reports_error_severity() -> None:
    session, understanding = _session_with_discover_status(
        "source_unavailable", exhaust_independent_branch=True
    )
    action = decide(session, understanding)
    assert action.kind is NextActionKind.STAGE_FAILED
    assert action.severity is Severity.ERROR


def test_discover_ok_status_lets_dependent_steps_run_normally() -> None:
    """With DISCOVER genuinely OK (independent branch left untouched, so
    BUILD_PLAN et al are also ready), `decide()` must never terminate with
    STAGE_FAILED — the pipeline is free to keep progressing."""
    session, understanding = _session_with_discover_status("ok")
    action = decide(session, understanding)
    assert action.kind is NextActionKind.RUN_STEP
    assert action.kind is not NextActionKind.STAGE_FAILED


def test_discover_location_ambiguous_is_handled_by_rung_2_not_stage_failed() -> None:
    """`LOCATION_AMBIGUOUS` must still go through the existing disambiguation
    rung (via `SlotState.AMBIGUOUS`) rather than the new STAGE_FAILED rung."""
    from vyaparsarathi.conversation.deltas import set_slot_ambiguous

    session, understanding = _session_with_discover_status("location_ambiguous")
    session = set_slot_ambiguous(
        session,
        SlotName.LOCATION_TEXT,
        ("Place A", "Place B"),
        raw_text="Bhagwanpur",
        turn_index=1,
    )
    action = decide(session, understanding)
    assert action.kind is NextActionKind.ASK_DISAMBIGUATION


# --- rung 6: NORMAL mode collects before it delivers ------------------------


def _session_with_the_whole_dag_exhausted() -> tuple[ConversationSession, TurnUnderstanding]:
    """Business + location satisfied (so rung 3 never fires) and every
    `StepId` already has an artifact (so rung 5 finds nothing left to run) —
    the one state where DEVELOPER and NORMAL mode can actually diverge:
    without this, rung 3 or rung 5 would intercept before rung 6 is ever
    reached, and the divergence this rung exists for would go untested."""
    now = datetime.now(UTC)
    artifacts = {step: _trivial_artifact(step) for step in StepId if step is not StepId.DISCOVER}
    artifacts[StepId.DISCOVER] = _discover_artifact("ok")
    session = ConversationSession(
        session_id="s1", created_at=now, updated_at=now, artifacts=artifacts
    )
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.PROPOSED_BUSINESS_TEXT,
                    raw_text="grocery",
                    value_token="grocery",
                    normalization=ValueNormalization.AS_STATED,
                ),
                SlotUpdateInput(
                    slot=SlotName.LOCATION_TEXT,
                    raw_text="Bhagwanpur, Bihar",
                    value_token="Bhagwanpur, Bihar",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=1,
    )
    understanding = TurnUnderstanding(intent=Intent.PROVIDE_INFO)
    return session, understanding


def test_developer_mode_default_delivers_even_with_nothing_else_collected() -> None:
    """`mode` defaults to DEVELOPER — today's behaviour, unchanged: with the
    DAG exhausted and RECOMMEND present, it delivers immediately regardless
    of assets/experience/cash."""
    session, understanding = _session_with_the_whole_dag_exhausted()
    action = decide(session, understanding)
    assert action.kind is NextActionKind.DELIVER_FINAL


def test_normal_mode_collects_owned_assets_before_delivering() -> None:
    from vyaparsarathi.conversation.conversation_config import ConversationMode
    from vyaparsarathi.conversation.readiness import CollectionItem

    session, understanding = _session_with_the_whole_dag_exhausted()
    action = decide(session, understanding, mode=ConversationMode.NORMAL)
    assert action.kind is NextActionKind.ASK_CLARIFICATION
    assert action.rung == "6_collect"
    assert action.message == question_for_item(CollectionItem.OWNED_ASSETS)


def _session_with_tier_a_satisfied() -> tuple[ConversationSession, TurnUnderstanding]:
    """`_session_with_the_whole_dag_exhausted()`, plus assets/experience
    declined and cash stated — Tier-A (`readiness.COLLECTION_ORDER`) is now
    fully satisfied, but none of the four viability drivers (revenue, margin,
    project cost, fixed opex) have been stated or declined yet."""
    session, understanding = _session_with_the_whole_dag_exhausted()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.DECLINE_SLOT, assets_declined=True, experience_declined=True
        ),
        turn_index=2,
    )
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.DECLINE_SLOT, declined_slots=(SlotName.YEARS_EXPERIENCE,)
        ),
        turn_index=3,
    )
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="100000",
                    value_token="100000",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=3,
    )
    return session, understanding


def test_normal_mode_asks_for_the_first_viability_driver_after_tier_a_is_satisfied() -> None:
    """Once business/location/assets/experience/cash are all in, NORMAL mode
    does not yet deliver — it moves on to rung 7 and asks about the first
    missing viability driver (revenue, in `missing_core_drivers`' own order),
    rather than showing an "insufficient evidence" result with the same
    questions listed as unanswered follow-ups."""
    from vyaparsarathi.conversation.conversation_config import ConversationMode

    session, understanding = _session_with_tier_a_satisfied()
    action = decide(session, understanding, mode=ConversationMode.NORMAL)
    assert action.kind is NextActionKind.ASK_CLARIFICATION
    assert action.rung == "7_collect_financial"
    assert action.slot is SlotName.MONTHLY_REVENUE_INR
    assert action.message == question_for_missing_driver(
        "a revenue driver (monthly_revenue, or unit_price + units_per_month)"
    )


def test_normal_mode_delivers_once_every_viability_driver_is_declined() -> None:
    """Declining all four viability drivers (never inventing a figure) still
    reaches a delivered advisory — the conversation never loops forever on a
    figure the entrepreneur won't give."""
    from vyaparsarathi.conversation.conversation_config import ConversationMode

    session, understanding = _session_with_tier_a_satisfied()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.DECLINE_SLOT,
            declined_slots=(
                SlotName.MONTHLY_REVENUE_INR,
                SlotName.COGS_PCT,
                SlotName.PROJECT_COST_INR,
                SlotName.FIXED_OPEX_INR,
            ),
        ),
        turn_index=4,
    )
    action = decide(session, understanding, mode=ConversationMode.NORMAL)
    assert action.kind is NextActionKind.DELIVER_FINAL


def test_normal_mode_skips_a_declined_viability_driver_and_asks_the_next() -> None:
    """Declining just the revenue driver moves rung 7 on to the margin
    driver next, rather than re-asking the declined one."""
    from vyaparsarathi.conversation.conversation_config import ConversationMode

    session, understanding = _session_with_tier_a_satisfied()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.DECLINE_SLOT, declined_slots=(SlotName.MONTHLY_REVENUE_INR,)
        ),
        turn_index=4,
    )
    action = decide(session, understanding, mode=ConversationMode.NORMAL)
    assert action.kind is NextActionKind.ASK_CLARIFICATION
    assert action.rung == "7_collect_financial"
    assert action.slot is SlotName.COGS_PCT


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
