"""The turn-level decision ladder (CLAUDE.md §2, §4, §25 Phase 6). PURE.

`decide()` is the one place a turn's next action is chosen. It is a fixed,
ordered ladder — the first matching rung wins — mirroring the `LadderRung`
idiom already used by `market/assessment_models.py` and
`finance/assessment_models.py::FinanceLadderRung`. Every rung either asks the
user something (never runs an engine) or names exactly one `StepId` to run
next; it never decides *how* to run a step — that is `llm/tools.py`'s job.

Rung 5 (`RUN_STEP`) is the only one that reads `TurnUnderstanding.
requested_step`, and only to **narrow**: `chosen = requested if requested in
ready else min(ready, key=DAG_ORDER)`. `test_conversation_workflow.py`
property-tests that a request can never expand the ready set into something
that was not already ready.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.clarify import question_for_slot
from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
)
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    SlotState,
    StepId,
)
from vyaparsarathi.conversation.understanding import TurnUnderstanding
from vyaparsarathi.conversation.workflow import DAG_ORDER, ready_steps
from vyaparsarathi.errors import VyaparError


class NextActionKind(StrEnum):
    ASK_CONTRADICTION = "ask_contradiction"
    ASK_DISAMBIGUATION = "ask_disambiguation"
    ASK_CLARIFICATION = "ask_clarification"
    RUN_STEP = "run_step"
    DELIVER_FINAL = "deliver_final"
    DELIVER_PARTIAL = "deliver_partial"
    END = "end"


class NextAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: NextActionKind
    step: StepId | None = None
    slot: SlotName | None = None
    options: tuple[str, ...] = ()
    message: str = ""
    rung: str = ""


_SATISFIED_STATES = frozenset(
    {SlotState.USER_PROVIDED, SlotState.SOURCED, SlotState.ASSUMED, SlotState.CALCULATED}
)


def satisfied_slots(session: ConversationSession) -> frozenset[SlotName]:
    return frozenset(
        name for name, slot in session.slots.items() if slot.state in _SATISFIED_STATES
    )


def completed_steps(session: ConversationSession) -> frozenset[StepId]:
    return frozenset(session.artifacts.keys())


def _first_ambiguous(session: ConversationSession) -> SlotName | None:
    for name, slot in session.slots.items():
        if slot.state is SlotState.AMBIGUOUS:
            return name
    return None


def _first_missing_hard_blocker(session: ConversationSession) -> SlotName | None:
    """Only the two structural blockers ever gate the DAG itself (see
    `workflow.py`'s module docstring) — never a financial driver."""
    for name in (SlotName.PROPOSED_BUSINESS_TEXT, SlotName.LOCATION_TEXT):
        slot = session.slot(name)
        if slot.state in (SlotState.MISSING,):
            return name
    return None


def _contradiction(session: ConversationSession, cfg: ConversationConfig) -> str | None:
    cash = session.slot(SlotName.LIQUID_CASH_INR)
    contribution = session.slot(SlotName.PROMOTER_CASH_CONTRIBUTION_INR)
    if (
        cash.state in _SATISFIED_STATES
        and contribution.state in _SATISFIED_STATES
        and isinstance(cash.value, int | float)
        and isinstance(contribution.value, int | float)
        and contribution.value > cash.value
    ):
        return (
            f"You said you would put in Rs {contribution.value:,} but also that you have "
            f"Rs {cash.value:,} in liquid cash — the contribution can't exceed what you have. "
            "Which figure is right?"
        )
    radius = session.slot(SlotName.RADIUS_M)
    if (
        radius.state is SlotState.USER_PROVIDED
        and isinstance(radius.value, int | float)
        and radius.value > cfg.max_radius_m
    ):
        return (
            f"That catchment radius ({radius.value:,} m) is larger than this system supports "
            f"({cfg.max_radius_m:,} m). Please give a smaller radius."
        )
    return None


def decide(
    session: ConversationSession,
    understanding: TurnUnderstanding,
    *,
    cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG,
) -> NextAction:
    if session.ended:
        return NextAction(kind=NextActionKind.END, rung="0_ended")

    # rung 1: contradiction
    message = _contradiction(session, cfg)
    if message is not None:
        return NextAction(
            kind=NextActionKind.ASK_CONTRADICTION, message=message, rung="1_contradiction"
        )

    # rung 2: blocking ambiguity
    ambiguous = _first_ambiguous(session)
    if ambiguous is not None:
        options = session.slot(ambiguous).current.options
        field = ambiguous.value.replace("_", " ")
        return NextAction(
            kind=NextActionKind.ASK_DISAMBIGUATION,
            slot=ambiguous,
            options=options,
            message=f"Several matches were found for {field}; please choose one.",
            rung="2_blocking_ambiguity",
        )

    # rung 5: run the next ready step (narrowing requested_step only). Checked
    # BEFORE the missing-slot rung: a slot that only gates a LATER step must
    # never stall progress a currently-ready step could already make (e.g.
    # RESOLVE_PROPOSED is ready with no location yet; asking for the location
    # first would waste a turn).
    completed = completed_steps(session)
    ready = ready_steps(completed, satisfied_slots(session))
    if ready:
        requested = understanding.requested_step
        if requested is not None and requested in ready:
            chosen = requested
        else:
            chosen = min(ready, key=DAG_ORDER.index)
        return NextAction(kind=NextActionKind.RUN_STEP, step=chosen, rung="5_runnable")

    # rung 3: blocking missing (structural only) — reached only once nothing
    # is left that could run without this fact.
    missing = _first_missing_hard_blocker(session)
    if missing is not None:
        return NextAction(
            kind=NextActionKind.ASK_CLARIFICATION,
            slot=missing,
            message=question_for_slot(missing),
            rung="3_blocking_missing",
        )

    # rung 8/9: nothing left to run
    if StepId.RECOMMEND in completed:
        return NextAction(kind=NextActionKind.DELIVER_FINAL, rung="8_final")
    return NextAction(kind=NextActionKind.DELIVER_PARTIAL, rung="9_partial")


class ConversationPlannerError(VyaparError):
    """A planner precondition was violated by the caller — a programming
    error (e.g. `decide()` called with an inconsistent session), never a
    runtime data condition."""


__all__ = [
    "ConversationPlannerError",
    "NextAction",
    "NextActionKind",
    "completed_steps",
    "decide",
    "satisfied_slots",
]
