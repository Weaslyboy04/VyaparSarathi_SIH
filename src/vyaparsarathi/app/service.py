"""`AdvisoryService` — the one entry point a channel calls (CLAUDE.md §25
Phase 6). Channel-neutral: nothing here knows about WhatsApp, HTTP, or a
terminal.

At this milestone (`llm_enabled=False`, the deterministic structured-input
path — see `app/dto.py::MessageRequest`'s docstring), `_understand` builds a
`TurnUnderstanding` directly from the request's structured fields; no free
text is parsed. `llm/orchestrator.py::run_turn` does everything else, so a
channel-neutral, multi-turn, evidence-grounded conversation already runs the
full Phase 1-5 pipeline with zero LLM involvement.
"""

from __future__ import annotations

from vyaparsarathi.app.dto import (
    AdvisoryPhase,
    AdvisoryReply,
    AdvisorySnapshot,
    Choice,
    ExpectedInput,
    MessageRequest,
    OutboundMessage,
    SessionHandle,
    StartSessionRequest,
    new_session_id,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.conversation.plan_builder import build_profile
from vyaparsarathi.conversation.planner import NextActionKind
from vyaparsarathi.conversation.render import render_reply
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.conversation.severity import Severity, max_severity
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.llm.orchestrator import run_turn
from vyaparsarathi.llm.structured import extract_understanding
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_PHASE_BY_ACTION: dict[NextActionKind, AdvisoryPhase] = {
    NextActionKind.ASK_CONTRADICTION: AdvisoryPhase.BLOCKED,
    NextActionKind.ASK_DISAMBIGUATION: AdvisoryPhase.BLOCKED,
    NextActionKind.ASK_CLARIFICATION: AdvisoryPhase.COLLECTING,
    NextActionKind.DELIVER_FINAL: AdvisoryPhase.COMPLETE,
    NextActionKind.DELIVER_PARTIAL: AdvisoryPhase.ANALYSING,
    NextActionKind.END: AdvisoryPhase.COMPLETE,
}
_SEVERITY_BY_ACTION: dict[NextActionKind, Severity] = {
    NextActionKind.ASK_CONTRADICTION: Severity.BLOCKED,
    NextActionKind.ASK_DISAMBIGUATION: Severity.BLOCKED,
    NextActionKind.ASK_CLARIFICATION: Severity.BLOCKED,
    NextActionKind.DELIVER_FINAL: Severity.INFO,
    NextActionKind.DELIVER_PARTIAL: Severity.DEGRADED,
    NextActionKind.END: Severity.INFO,
}


class AdvisoryService:
    def __init__(self, *, sessions: SessionRepository, runtime: AdvisoryRuntime) -> None:
        self._sessions = sessions
        self._runtime = runtime

    def start_session(self, req: StartSessionRequest) -> SessionHandle:
        session_id = req.session_id or new_session_id()
        session = ConversationSession(
            session_id=session_id, created_at=req.started_at, updated_at=req.started_at
        )
        self._sessions.save(session)
        logger.info("session %s started on channel=%s", session_id, req.channel.value)
        return SessionHandle(session_id=session_id, created_at=req.started_at)

    def get_session(self, session_id: str) -> SessionHandle | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        return SessionHandle(session_id=session.session_id, created_at=session.created_at)

    def end_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None:
            return
        session = session.model_copy(update={"ended": True})
        self._sessions.save(session)

    def send_message(self, req: MessageRequest) -> AdvisoryReply:
        session = self._sessions.get(req.session_id)
        if session is None:
            raise SessionNotFoundError(req.session_id)

        understanding, llm_used = self._understand(req)
        outcome = run_turn(
            session,
            understanding,
            self._runtime.run_context,
            clock=lambda: req.received_at,
            llm_used=llm_used,
        )
        self._sessions.save(outcome.session)

        lines, narrative = render_reply(outcome.session, outcome.action)
        severity = max_severity(
            _SEVERITY_BY_ACTION.get(outcome.action.kind, Severity.INFO),
            Severity.DEGRADED if outcome.session.session_warnings else Severity.INFO,
        )
        expects = self._expects_for(outcome.action.kind)
        choices = tuple(
            Choice(index=i, label=o) for i, o in enumerate(outcome.action.options, start=1)
        )
        messages = self._build_messages(lines, choices)

        return AdvisoryReply(
            session_id=outcome.session.session_id,
            turn_index=outcome.session.turn_index,
            messages=messages,
            expects=expects,
            choices=choices,
            state=_PHASE_BY_ACTION.get(outcome.action.kind, AdvisoryPhase.COLLECTING),
            severity=severity,
            narrative=narrative,
            warnings=list(outcome.session.session_warnings),
        )

    def snapshot(self, session_id: str) -> AdvisorySnapshot | None:
        session = self._sessions.get(session_id)
        if session is None:
            return None
        recommend = session.artifacts.get(StepId.RECOMMEND)
        return AdvisorySnapshot(
            session_id=session.session_id,
            turn_index=session.turn_index,
            updated_at=session.updated_at,
            profile=build_profile(session).model_dump(mode="json"),
            slots={
                name.value: slot.model_dump(mode="json") for name, slot in session.slots.items()
            },
            artifacts={
                step.value: artifact.model_dump(mode="json")
                for step, artifact in session.artifacts.items()
            },
            recommendation=recommend.payload if recommend is not None else None,
            warnings=list(session.session_warnings),
        )

    # -- internal -----------------------------------------------------

    def _understand(self, req: MessageRequest) -> tuple[TurnUnderstanding, bool]:
        structured = (
            req.slot_updates or req.asset_update or req.experience_update or req.declined_slots
        )
        has_choice = req.selected_choice is not None

        # llm_enabled=False (or no structured input AND no configured
        # provider): the deterministic path. CLAUDE.md's own instruction for
        # this mode — no arbitrary free-text extraction is attempted without
        # an LLM; a channel must supply structured fields directly.
        if (
            not structured
            and not has_choice
            and req.text.strip()
            and self._runtime.llm_provider is not None
        ):
            understanding, llm_used = extract_understanding(
                req.text, self._runtime.llm_provider, self._runtime.settings
            )
            return understanding.model_copy(update={"requested_step": req.requested_step}), llm_used

        if req.declined_slots:
            intent = Intent.DECLINE_SLOT
        elif has_choice:
            intent = Intent.SELECT_CANDIDATE
        elif structured:
            intent = Intent.PROVIDE_INFO
        else:
            intent = Intent.UNCLEAR
        return (
            TurnUnderstanding(
                intent=intent,
                slot_updates=req.slot_updates,
                asset_update=req.asset_update,
                experience_update=req.experience_update,
                declined_slots=req.declined_slots,
                selected_choice=req.selected_choice,
                requested_step=req.requested_step,
                raw_message=req.text,
            ),
            False,
        )

    @staticmethod
    def _build_messages(
        lines: list[str], choices: tuple[Choice, ...]
    ) -> tuple[OutboundMessage, ...]:
        """One `OutboundMessage` per line; numbered `choices` (if any) are
        attached to the last message, since that is the one asking the
        question they answer."""
        if not lines:
            return (OutboundMessage(text="", choices=choices),) if choices else ()
        last = len(lines) - 1
        return tuple(
            OutboundMessage(text=text, choices=choices if i == last else ())
            for i, text in enumerate(lines)
        )

    @staticmethod
    def _expects_for(kind: NextActionKind) -> ExpectedInput:
        if kind is NextActionKind.ASK_DISAMBIGUATION:
            return ExpectedInput.CHOICE
        if kind in (NextActionKind.ASK_CONTRADICTION, NextActionKind.ASK_CLARIFICATION):
            return ExpectedInput.FREE_TEXT
        return ExpectedInput.NONE


__all__ = ["AdvisoryService"]
