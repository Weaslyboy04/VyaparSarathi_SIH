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

from vyaparsarathi.conversation.clarify import (
    question_for_item,
    question_for_missing_driver,
    question_for_slot,
)
from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
    ConversationMode,
)
from vyaparsarathi.conversation.outcomes import outcome_for
from vyaparsarathi.conversation.readiness import advisory_readiness
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    SlotState,
    StepId,
    satisfied_slots,
)
from vyaparsarathi.conversation.severity import Severity
from vyaparsarathi.conversation.understanding import TurnUnderstanding
from vyaparsarathi.conversation.workflow import DAG_ORDER, STEP_SPECS, ready_steps
from vyaparsarathi.errors import VyaparError
from vyaparsarathi.models.results import DiscoveryStatus


class NextActionKind(StrEnum):
    ASK_CONTRADICTION = "ask_contradiction"
    ASK_DISAMBIGUATION = "ask_disambiguation"
    ASK_CLARIFICATION = "ask_clarification"
    RUN_STEP = "run_step"
    STAGE_FAILED = "stage_failed"
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
    # Only set by the STAGE_FAILED rung, carrying the underlying outcome's
    # severity (BLOCKED vs ERROR) through to `app/service.py`; every other
    # rung leaves this `None` and relies on the static kind->severity map.
    severity: Severity | None = None


def completed_steps(session: ConversationSession) -> frozenset[StepId]:
    return frozenset(session.artifacts.keys())


def _first_ambiguous(session: ConversationSession) -> SlotName | None:
    for name, slot in session.slots.items():
        if slot.state is SlotState.AMBIGUOUS:
            return name
    return None


# Statuses `discovery/service.py` records as DISCOVER's artifact when the
# location could not be resolved at all -- caught internally there rather
# than raised (CLAUDE.md §6.1's "never crash, never fabricate" contract for
# that boundary), which means `ready_steps()`'s artifact-presence check alone
# cannot distinguish these from a real success. `LOCATION_AMBIGUOUS` is
# deliberately excluded: rung 2 (`_first_ambiguous`) always intercepts it
# first, via `SlotState.AMBIGUOUS`, before this is ever consulted.
_DISCOVER_HARD_FAILURES = frozenset(
    {DiscoveryStatus.LOCATION_NOT_FOUND, DiscoveryStatus.SOURCE_UNAVAILABLE}
)


def _discover_failure(session: ConversationSession) -> DiscoveryStatus | None:
    """DISCOVER's status, if its artifact represents a terminal failure that
    must stop every step depending on it — `None` otherwise (no artifact
    yet, or a genuinely usable one: `OK`/`NO_RESULTS`/`LOCATION_AMBIGUOUS`)."""
    artifact = session.artifacts.get(StepId.DISCOVER)
    if artifact is None:
        return None
    status = DiscoveryStatus(artifact.payload["status"])
    return status if status in _DISCOVER_HARD_FAILURES else None


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
    satisfied = satisfied_slots(session)
    if (
        SlotName.LIQUID_CASH_INR in satisfied
        and SlotName.PROMOTER_CASH_CONTRIBUTION_INR in satisfied
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
    mode: ConversationMode = ConversationMode.DEVELOPER,
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
    # first would waste a turn). A step that directly requires DISCOVER is
    # excluded here once DISCOVER has hard-failed — its artifact exists (so
    # it stays `completed`, and DISCOVER itself is never re-run), but it must
    # never satisfy a downstream step's `required_steps`. Every direct
    # dependent of DISCOVER (ANALYZE, METRICS, DEMAND_EVIDENCE,
    # OPPORTUNITY_EVIDENCE) is blocked by this one check, which transitively
    # blocks everything that in turn requires those (DEMAND_SIGNALS,
    # ASSESS_MARKET, OPPORTUNITY, RECOMMEND, SWOT) since they can then never
    # get an artifact either. FINANCE_KNOWLEDGE lists DISCOVER only as
    # `optional_steps`, so it is unaffected — it may still run without a
    # resolved location, matching its own documented intent.
    completed = completed_steps(session)
    discover_failure = _discover_failure(session)
    ready = ready_steps(completed, satisfied_slots(session))
    if discover_failure is not None:
        ready = frozenset(s for s in ready if StepId.DISCOVER not in STEP_SPECS[s].required_steps)
    if ready:
        requested = understanding.requested_step
        if requested is not None and requested in ready:
            chosen = requested
        else:
            chosen = min(ready, key=DAG_ORDER.index)
        return NextAction(kind=NextActionKind.RUN_STEP, step=chosen, rung="5_runnable")

    # rung 4: a prerequisite hard-failed and nothing else is left runnable.
    # Terminal for this turn — never DELIVER_FINAL/DELIVER_PARTIAL built from
    # empty/default data, and never re-asks for more missing slots (rung 3)
    # when the pipeline can never reach a real market/opportunity/
    # recommendation conclusion anyway.
    if discover_failure is not None:
        outcome = outcome_for(discover_failure)
        artifact = session.artifacts[StepId.DISCOVER]
        detail_warnings = artifact.payload.get("warnings") or []
        detail = f" Detail: {'; '.join(detail_warnings)}." if detail_warnings else ""
        message = (
            f"Stage failed: location resolution (DISCOVER). {outcome.message}{detail} "
            "No market, opportunity, or financial recommendation can be produced until "
            "this is resolved — please correct the location and try again."
        )
        return NextAction(
            kind=NextActionKind.STAGE_FAILED,
            step=StepId.DISCOVER,
            message=message,
            severity=outcome.severity,
            rung="4_stage_failed",
        )

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

    # rung 6: NORMAL mode collects before it delivers (CLAUDE.md's "COLLECT →
    # VALIDATE → COLLECT → ANALYSE → one advisory" UX). The DAG has already
    # run everything it structurally can by this point (rung 5 found nothing
    # ready) — this rung only ever gates the DISPLAY of that work, never its
    # execution: market/opportunity/finance evidence is still gathered
    # eagerly, in the background, exactly as in DEVELOPER mode. DEVELOPER
    # mode (the default) skips this rung entirely, so every pre-Phase-B test
    # and demo keeps today's incremental behaviour unchanged.
    if mode is ConversationMode.NORMAL:
        readiness = advisory_readiness(session)
        if not readiness.ready:
            assert readiness.next_ask is not None  # implied by `not ready`
            return NextAction(
                kind=NextActionKind.ASK_CLARIFICATION,
                message=question_for_item(readiness.next_ask),
                rung="6_collect",
            )

        # rung 7: Tier-A is satisfied — now collect the four viability
        # drivers (revenue, margin, project cost, fixed opex), one at a
        # time, so the eventual DELIVER_FINAL/DELIVER_PARTIAL is the single
        # consolidated advisory the entrepreneur sees, not a mid-collection
        # "insufficient evidence" dump of the same questions (CLAUDE.md's
        # "COLLECT → VALIDATE → COLLECT → ANALYSE → one advisory"). A
        # `DECLINED` driver counts as asked and is never re-asked
        # (`readiness.next_missing_driver_text` skips it); once every
        # remaining driver is supplied or declined this falls through to
        # rung 8/9 exactly as before — the DAG itself is never gated by
        # this rung (rung 5 already ran everything it structurally could).
        if readiness.next_missing_driver_text is not None:
            return NextAction(
                kind=NextActionKind.ASK_CLARIFICATION,
                slot=readiness.next_missing_driver_slot,
                message=question_for_missing_driver(readiness.next_missing_driver_text),
                rung="7_collect_financial",
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
