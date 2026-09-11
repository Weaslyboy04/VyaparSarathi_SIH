"""`TelegramGateway` — one inbound update -> one advisory turn -> outbound
bubbles (CLAUDE.md §4, §25 Phase 7). Mirrors
`channels/whatsapp/gateway.py::WhatsAppGateway` exactly — owns session
identity (from the chat id) and nothing else; every domain decision stays
inside `AdvisoryService.send_message`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from vyaparsarathi.app.clear_command import CLEAR_CONFIRMATION_MESSAGE, is_clear_command
from vyaparsarathi.app.dto import ChannelId, ReportResult, StartSessionRequest
from vyaparsarathi.app.errors import SessionNotFoundError
from vyaparsarathi.app.greeting import GREETING_MESSAGE
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.channels.telegram.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_telegram_messages,
)
from vyaparsarathi.channels.telegram.models import (
    InboundKind,
    OutboundKind,
    OutboundTelegramMessage,
)
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

VOICE_DEFERRED_NOTICE = (
    "Voice notes aren't supported yet. Please type your message as text and I'll help."
)
MEDIA_DEFERRED_NOTICE = (
    "Attachments aren't supported yet. Please describe what you need in a text message."
)
_VOICE_TYPES = frozenset({"voice", "audio", "video_note"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TelegramGateway:
    def __init__(
        self, service: AdvisoryService, *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._service = service
        self._clock = clock

    def handle_update(
        self, update: Mapping[str, Any]
    ) -> tuple[tuple[OutboundTelegramMessage, ...], ReportResult | None]:
        """Parse -> (deferred notice | run one turn) -> outbound bubbles, and
        the turn's `ReportResult` (`None` on every ordinary turn). Raises
        `MalformedUpdateError` for a structurally invalid update, or an
        update type this system does not act on at all — the poll loop
        catches and skips those, never crashing."""
        message = parse_inbound(update)
        chat_id = message.chat_id

        if message.kind is InboundKind.TEXT and is_clear_command(message.text):
            self._service.delete_session(channel_session_id(chat_id))
            bubble = OutboundTelegramMessage(
                chat_id=chat_id, body=CLEAR_CONFIRMATION_MESSAGE, kind=OutboundKind.TEXT
            )
            return (bubble,), None

        if message.kind is InboundKind.UNSUPPORTED:
            notice = (
                VOICE_DEFERRED_NOTICE
                if message.unsupported_type in _VOICE_TYPES
                else MEDIA_DEFERRED_NOTICE
            )
            logger.info("telegram: deferred unsupported inbound type=%s", message.unsupported_type)
            bubble = OutboundTelegramMessage(
                chat_id=chat_id, body=notice, kind=OutboundKind.DEFERRED_NOTICE
            )
            return (bubble,), None

        session_id = channel_session_id(chat_id)
        now = self._clock()
        is_new_session = self._ensure_session(session_id, now)

        request = to_message_request(message, session_id=session_id, received_at=now)
        try:
            reply = self._service.send_message(request)
        except SessionNotFoundError:
            # Race: the session was evicted between _ensure_session and here.
            self._start_session(session_id, now)
            reply = self._service.send_message(request)

        bubbles = to_telegram_messages(reply, chat_id=chat_id)
        if is_new_session:
            greeting = OutboundTelegramMessage(
                chat_id=chat_id, body=GREETING_MESSAGE, kind=OutboundKind.TEXT
            )
            bubbles = (greeting, *bubbles)
        return bubbles, reply.report

    def _ensure_session(self, session_id: str, now: datetime) -> bool:
        """`True` iff a session did not already exist and was just created."""
        if self._service.get_session(session_id) is None:
            self._start_session(session_id, now)
            return True
        return False

    def _start_session(self, session_id: str, now: datetime) -> None:
        self._service.start_session(
            StartSessionRequest(session_id=session_id, channel=ChannelId.TELEGRAM, started_at=now)
        )


__all__ = ["MEDIA_DEFERRED_NOTICE", "VOICE_DEFERRED_NOTICE", "TelegramGateway"]
