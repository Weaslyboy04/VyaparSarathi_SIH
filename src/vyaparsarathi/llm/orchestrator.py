"""Turn-level orchestration: apply deltas, invalidate, run the DAG as far as
it structurally can, stop at the next thing that needs the user (CLAUDE.md
§25 Phase 6).

`run_turn` is LLM-agnostic — it takes an already-built `TurnUnderstanding`
(from `llm/structured.py`'s extraction when `llm_enabled=True`, or built
directly from a structured `MessageRequest` by `app/service.py` when it is
not) and runs the deterministic step machinery underneath it. This is why
`llm_enabled=False` reaches the exact same recommendation as the LLM path:
both funnel into this one function.
"""

from __future__ import annotations

from dataclasses import dataclass

from vyaparsarathi.conversation.artifacts import invalidate
from vyaparsarathi.conversation.deltas import apply_understanding
from vyaparsarathi.conversation.planner import NextAction, NextActionKind, decide
from vyaparsarathi.conversation.session_models import ConversationSession, StepId, TurnRecord
from vyaparsarathi.conversation.understanding import TurnUnderstanding
from vyaparsarathi.errors import (
    HttpError,
    LlmPayloadError,
    LlmUnavailableError,
    SourcePayloadError,
    SourceUnavailableError,
)
from vyaparsarathi.llm.tools import CONFIG_BLOBS, RunContext, run_step
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)

_DEGRADED_EXC = (
    SourceUnavailableError,
    SourcePayloadError,
    HttpError,
    LlmUnavailableError,
    LlmPayloadError,
)

# A turn never runs more steps than this — a structural safety bound, not a
# tuning knob a real conversation would ever approach (the DAG has 17 nodes).
_MAX_STEPS_PER_TURN = len(StepId) + 2


@dataclass
class TurnOutcome:
    session: ConversationSession
    action: NextAction
    executed_steps: tuple[StepId, ...]
    invalidated_steps: tuple[StepId, ...]
    apply_warnings: tuple[str, ...]


def run_turn(
    session: ConversationSession,
    understanding: TurnUnderstanding,
    ctx: RunContext,
    *,
    clock: Clock = utcnow,
    llm_used: bool = False,
) -> TurnOutcome:
    now = clock()
    turn_index = session.turn_index + 1

    session, apply_warnings = apply_understanding(session, understanding, turn_index=turn_index)
    session, invalidated = invalidate(session, CONFIG_BLOBS)
    session = session.model_copy(update={"turn_index": turn_index, "updated_at": now})

    executed: list[StepId] = []
    step_warnings: list[str] = []
    action = decide(session, understanding, cfg=ctx.conv_cfg, mode=ctx.mode)
    # requested_step only ever governs the FIRST decision of a turn (rung 5's
    # narrowing property) — subsequent iterations run whatever the DAG makes
    # ready next, using a request-free understanding.
    quiet_understanding = TurnUnderstanding(intent=understanding.intent)

    while action.kind is NextActionKind.RUN_STEP and len(executed) < _MAX_STEPS_PER_TURN:
        step = action.step
        assert step is not None
        try:
            session = run_step(step, session, ctx, turn_index=turn_index)
        except _DEGRADED_EXC as exc:
            logger.warning("step %s failed: %s", step.value, exc)
            step_warnings.append(f"{step.value} could not complete: {exc}")
            break
        executed.append(step)
        action = decide(session, quiet_understanding, cfg=ctx.conv_cfg, mode=ctx.mode)

    all_warnings = (*apply_warnings, *step_warnings)
    session = session.model_copy(
        update={"session_warnings": (*session.session_warnings, *all_warnings)}
    )

    record = TurnRecord(
        turn_index=turn_index,
        channel="",
        intent=understanding.intent.value,
        slots_touched=tuple(u.slot for u in understanding.slot_updates),
        requested_step=understanding.requested_step,
        executed_step=executed[-1] if executed else None,
        steps_invalidated=invalidated,
        outcome=action.kind.value,
        llm_used=llm_used,
        warnings=tuple(all_warnings),
    )
    session = session.model_copy(update={"turns": (*session.turns, record)})

    return TurnOutcome(
        session=session,
        action=action,
        executed_steps=tuple(executed),
        invalidated_steps=invalidated,
        apply_warnings=tuple(all_warnings),
    )


__all__ = ["TurnOutcome", "run_turn"]
