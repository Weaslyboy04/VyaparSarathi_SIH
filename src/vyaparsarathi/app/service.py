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
    ReportResult,
    ReportStatus,
    SessionHandle,
    StartSessionRequest,
    new_session_id,
)
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.runtime import AdvisoryRuntime
from vyaparsarathi.conversation.bundle import build_bundle
from vyaparsarathi.conversation.plan_builder import build_profile
from vyaparsarathi.conversation.planner import NextActionKind, decide
from vyaparsarathi.conversation.render import Narrative, render_reply
from vyaparsarathi.conversation.report_intent import ReportIntent, classify_report_intent
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.conversation.severity import Severity, max_severity
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.llm.extraction_context import build_extraction_context
from vyaparsarathi.llm.language_guard import (
    CLARIFICATION_MESSAGE,
    contains_unsupported_devanagari_numeral,
)
from vyaparsarathi.llm.orchestrator import run_turn
from vyaparsarathi.llm.reply_authoring import author_reply_sections
from vyaparsarathi.llm.structured import ExtractionOutcome, extract_understanding
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_PROVIDER_UNAVAILABLE_MESSAGE = (
    "I'm having trouble reaching the assistant service right now — this is a "
    "temporary problem on our end, not something wrong with your answer. "
    "Nothing you've told me so far is lost; please try sending that again "
    "in a moment."
)
_REPORT_REQUESTED_MESSAGE = "Great — preparing your PDF project report now."
_REPORT_DECLINED_MESSAGE = 'No problem — just say "generate the report" any time you would like it.'

_PHASE_BY_ACTION: dict[NextActionKind, AdvisoryPhase] = {
    NextActionKind.ASK_CONTRADICTION: AdvisoryPhase.BLOCKED,
    NextActionKind.ASK_DISAMBIGUATION: AdvisoryPhase.BLOCKED,
    NextActionKind.ASK_CLARIFICATION: AdvisoryPhase.COLLECTING,
    NextActionKind.STAGE_FAILED: AdvisoryPhase.FAILED,
    NextActionKind.DELIVER_FINAL: AdvisoryPhase.COMPLETE,
    NextActionKind.DELIVER_PARTIAL: AdvisoryPhase.ANALYSING,
    NextActionKind.END: AdvisoryPhase.COMPLETE,
}
_SEVERITY_BY_ACTION: dict[NextActionKind, Severity] = {
    NextActionKind.ASK_CONTRADICTION: Severity.BLOCKED,
    NextActionKind.ASK_DISAMBIGUATION: Severity.BLOCKED,
    NextActionKind.ASK_CLARIFICATION: Severity.BLOCKED,
    NextActionKind.STAGE_FAILED: Severity.ERROR,
    NextActionKind.DELIVER_FINAL: Severity.INFO,
    NextActionKind.DELIVER_PARTIAL: Severity.DEGRADED,
    NextActionKind.END: Severity.INFO,
}
# The only two `NextActionKind`s whose narrative is built from an
# `EvidenceBundle` (`render.py::_render_summary`) — the ones reply
# authoring can ever have anything grounded to rewrite.
_AUTHORABLE_ACTION_KINDS = (NextActionKind.DELIVER_FINAL, NextActionKind.DELIVER_PARTIAL)


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

    def delete_session(self, session_id: str) -> None:
        """Unlike `end_session` (marks `ended=True`, which `decide()` then
        answers with a terminal "this conversation has ended" forever), this
        removes the session entirely — a channel's own "/clear"-style reset
        command uses this so the *same* channel-scoped session id (a phone
        number, a chat id) starts a genuinely fresh session, greeting and
        all, on the next message, rather than staying permanently ended.
        A no-op if the session does not exist."""
        self._sessions.delete(session_id)

    def send_message(self, req: MessageRequest) -> AdvisoryReply:
        session = self._sessions.get(req.session_id)
        if session is None:
            raise SessionNotFoundError(req.session_id)

        report_reply = self._maybe_handle_report_intent(req, session)
        if report_reply is not None:
            return report_reply

        if self._wants_free_text_extraction(req) and contains_unsupported_devanagari_numeral(
            req.text
        ):
            return self._language_guard_reply(session)

        understanding, llm_used, extraction_outcome = self._understand(req, session)
        if extraction_outcome is ExtractionOutcome.PROVIDER_UNAVAILABLE:
            # Distinguishes "the service is temporarily unreachable" from "I
            # didn't understand your answer" (CLAUDE.md §25 Phase 6 Priority
            # 6) — never consumes a turn, exactly like the language guard
            # above, so the pending question is unchanged for a retry.
            return self._provider_unavailable_reply(session)
        outcome = run_turn(
            session,
            understanding,
            self._runtime.run_context,
            clock=lambda: req.received_at,
            llm_used=llm_used,
        )
        self._sessions.save(outcome.session)

        lines, narrative = render_reply(outcome.session, outcome.action)
        if (
            self._runtime.settings.llm_reply_authoring_enabled
            and self._runtime.llm_provider is not None
            and outcome.action.kind in _AUTHORABLE_ACTION_KINDS
        ):
            bundle = build_bundle(outcome.session)
            narrative = author_reply_sections(
                narrative,
                bundle,
                self._runtime.llm_provider,
                self._runtime.settings,
                cfg=self._runtime.run_context.conv_cfg,
            )
            lines = list(narrative.sections.values())
        # `NextAction.severity` (set only by the STAGE_FAILED rung) carries
        # the specific underlying outcome's severity (BLOCKED vs ERROR,
        # reused from `conversation/outcomes.py`) — preferred over the
        # static per-kind default below when present.
        action_severity = outcome.action.severity or _SEVERITY_BY_ACTION.get(
            outcome.action.kind, Severity.INFO
        )
        severity = max_severity(
            action_severity,
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

    def _wants_free_text_extraction(self, req: MessageRequest) -> bool:
        """True iff this turn will (absent any pre-check like the language
        guard) be handed to the LLM extractor as free text — no structured
        field, no numbered choice, non-empty text, and a provider actually
        configured (CLAUDE.md §3.1's `llm_enabled=False` deterministic mode
        never attempts free-text extraction at all)."""
        structured = (
            req.slot_updates
            or req.asset_update
            or req.experience_update
            or req.declined_slots
            or req.assets_declined
            or req.experience_declined
        )
        has_choice = req.selected_choice is not None
        return bool(
            not structured
            and not has_choice
            and req.text.strip()
            and self._runtime.llm_provider is not None
        )

    def _language_guard_reply(self, session: ConversationSession) -> AdvisoryReply:
        """A message contained a Devanagari numeral we've made no decision
        to parse (`llm/language_guard.py`) — ask for a restatement without
        consuming a turn or touching session state at all: `decide()` here
        is a pure, side-effect-free peek (the same one `extraction_context.py`
        uses), so the session the user sees is untouched and their pending
        question, if any, is unchanged for their next attempt."""
        action = decide(
            session,
            TurnUnderstanding(intent=Intent.UNCLEAR),
            cfg=self._runtime.run_context.conv_cfg,
            mode=self._runtime.run_context.mode,
        )
        choices = tuple(Choice(index=i, label=o) for i, o in enumerate(action.options, start=1))
        return AdvisoryReply(
            session_id=session.session_id,
            turn_index=session.turn_index,
            messages=(OutboundMessage(text=CLARIFICATION_MESSAGE, choices=choices),),
            expects=ExpectedInput.FREE_TEXT,
            choices=choices,
            state=_PHASE_BY_ACTION.get(action.kind, AdvisoryPhase.COLLECTING),
            severity=Severity.DEGRADED,
            narrative=Narrative(
                sections={"language_notice": CLARIFICATION_MESSAGE},
                generated_by={"language_notice": "template"},
            ),
            warnings=list(session.session_warnings),
        )

    def _provider_unavailable_reply(self, session: ConversationSession) -> AdvisoryReply:
        """The LLM provider itself could not be reached (`ExtractionOutcome.
        PROVIDER_UNAVAILABLE`) — tell the user plainly that the SERVICE is
        the problem, not their answer, without consuming a turn or touching
        session state (same pure `decide()` peek as `_language_guard_reply`,
        so a retry sees exactly the same pending question)."""
        action = decide(
            session,
            TurnUnderstanding(intent=Intent.UNCLEAR),
            cfg=self._runtime.run_context.conv_cfg,
            mode=self._runtime.run_context.mode,
        )
        choices = tuple(Choice(index=i, label=o) for i, o in enumerate(action.options, start=1))
        return AdvisoryReply(
            session_id=session.session_id,
            turn_index=session.turn_index,
            messages=(OutboundMessage(text=_PROVIDER_UNAVAILABLE_MESSAGE, choices=choices),),
            expects=ExpectedInput.FREE_TEXT,
            choices=choices,
            state=_PHASE_BY_ACTION.get(action.kind, AdvisoryPhase.COLLECTING),
            severity=Severity.DEGRADED,
            narrative=Narrative(
                sections={"provider_notice": _PROVIDER_UNAVAILABLE_MESSAGE},
                generated_by={"provider_notice": "template"},
            ),
            warnings=list(session.session_warnings),
        )

    def _maybe_handle_report_intent(
        self, req: MessageRequest, session: ConversationSession
    ) -> AdvisoryReply | None:
        """`None` on every ordinary turn. A bare "yes"/"no" only means
        anything about a PDF report at the one moment nothing else is
        pending — a pure `decide()` peek (same pattern as
        `_language_guard_reply`) confirms the session is already sitting at
        `DELIVER_FINAL`/`DELIVER_PARTIAL` before `req.text` is even looked
        at, so this can never mis-fire mid-collection. Consumes no turn and
        never mutates the session — actual PDF generation stays the
        caller's job (CLAUDE.md §18, §30: DPR generation stays outside the
        LLM/engine pipeline; `AdvisoryService` only classifies intent)."""
        if not req.text.strip():
            return None
        peek = decide(
            session,
            TurnUnderstanding(intent=Intent.UNCLEAR),
            cfg=self._runtime.run_context.conv_cfg,
            mode=self._runtime.run_context.mode,
        )
        if peek.kind not in (NextActionKind.DELIVER_FINAL, NextActionKind.DELIVER_PARTIAL):
            return None
        intent = classify_report_intent(req.text)
        if intent is ReportIntent.UNCLEAR:
            return None
        if intent is ReportIntent.AFFIRM:
            status, message = ReportStatus.REQUESTED, _REPORT_REQUESTED_MESSAGE
        else:
            status, message = ReportStatus.DECLINED, _REPORT_DECLINED_MESSAGE
        return AdvisoryReply(
            session_id=session.session_id,
            turn_index=session.turn_index,
            messages=(OutboundMessage(text=message),),
            expects=ExpectedInput.NONE,
            state=_PHASE_BY_ACTION.get(peek.kind, AdvisoryPhase.COMPLETE),
            severity=Severity.INFO,
            narrative=Narrative(
                sections={"report_intent": message}, generated_by={"report_intent": "template"}
            ),
            warnings=list(session.session_warnings),
            report=ReportResult(status=status, message=message),
        )

    def _understand(
        self, req: MessageRequest, session: ConversationSession
    ) -> tuple[TurnUnderstanding, bool, ExtractionOutcome]:
        # llm_enabled=False (or no structured input AND no configured
        # provider): the deterministic path. CLAUDE.md's own instruction for
        # this mode — no arbitrary free-text extraction is attempted without
        # an LLM; a channel must supply structured fields directly.
        if self._wants_free_text_extraction(req):
            # `_wants_free_text_extraction` already checked this is not None;
            # the assert only restates that invariant for mypy's narrowing,
            # which does not cross the method boundary.
            assert self._runtime.llm_provider is not None
            context = build_extraction_context(
                session,
                cfg=self._runtime.run_context.conv_cfg,
                mode=self._runtime.run_context.mode,
            )
            understanding, llm_used, outcome = extract_understanding(
                req.text, self._runtime.llm_provider, self._runtime.settings, context=context
            )
            understanding = understanding.model_copy(update={"requested_step": req.requested_step})
            return understanding, llm_used, outcome

        structured = (
            req.slot_updates
            or req.asset_update
            or req.experience_update
            or req.declined_slots
            or req.assets_declined
            or req.experience_declined
        )
        if req.declined_slots or req.assets_declined or req.experience_declined:
            intent = Intent.DECLINE_SLOT
        elif req.selected_choice is not None:
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
                assets_declined=req.assets_declined,
                experience_declined=req.experience_declined,
                selected_choice=req.selected_choice,
                requested_step=req.requested_step,
                raw_message=req.text,
            ),
            False,
            ExtractionOutcome.SUCCEEDED,
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
