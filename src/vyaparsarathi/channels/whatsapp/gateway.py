"""`WhatsAppGateway` — one inbound webhook -> one advisory turn -> outbound
bubbles (CLAUDE.md §4, §25 Phase 7). Owns session identity (from the sender
id) and nothing else; every domain decision stays inside
`AdvisoryService.send_message`.
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
from vyaparsarathi.channels.whatsapp.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_whatsapp_messages,
)
from vyaparsarathi.channels.whatsapp.models import (
    InboundKind,
    OutboundKind,
    OutboundWhatsAppMessage,
)
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

VOICE_DEFERRED_NOTICE = (
    "Voice notes aren't supported yet. Please type your message as text and I'll help."
)
MEDIA_DEFERRED_NOTICE = (
    "Attachments aren't supported yet. Please describe what you need in a text message."
)
_VOICE_TYPES = frozenset({"audio", "voice", "ptt"})


def _utcnow() -> datetime:
    return datetime.now(UTC)


class WhatsAppGateway:
    def __init__(
        self, service: AdvisoryService, *, clock: Callable[[], datetime] = _utcnow
    ) -> None:
        self._service = service
        self._clock = clock

    def handle_webhook(
        self, payload: Mapping[str, Any]
    ) -> tuple[tuple[OutboundWhatsAppMessage, ...], ReportResult | None]:
        """Parse -> (deferred notice | run one turn) -> outbound bubbles, and
        the turn's `ReportResult` (`None` on every ordinary turn — see
        `app/dto.py`). A caller uses the second element to know when to
        generate and separately deliver a DPR document; the gateway itself
        never calls `DprService` (CLAUDE.md §25 Phase 7: no business logic in
        the channel layer). Raises `MalformedWebhookError` for a structurally
        invalid payload; a transport maps that to an HTTP 4xx and sends
        nothing to the user."""
        message = parse_inbound(payload)
        to = message.sender

        if message.kind is InboundKind.TEXT and is_clear_command(message.text):
            self._service.delete_session(channel_session_id(to))
            bubble = OutboundWhatsAppMessage(
                to=to, body=CLEAR_CONFIRMATION_MESSAGE, kind=OutboundKind.TEXT
            )
            return (bubble,), None

        if message.kind is InboundKind.UNSUPPORTED:
            notice = (
                VOICE_DEFERRED_NOTICE
                if message.unsupported_type.lower() in _VOICE_TYPES
                else MEDIA_DEFERRED_NOTICE
            )
            logger.info("whatsapp: deferred unsupported inbound type=%s", message.unsupported_type)
            bubble = OutboundWhatsAppMessage(to=to, body=notice, kind=OutboundKind.DEFERRED_NOTICE)
            return (bubble,), None

        session_id = channel_session_id(to)
        now = self._clock()
        is_new_session = self._ensure_session(session_id, now)

        request = to_message_request(message, session_id=session_id, received_at=now)
        try:
            reply = self._service.send_message(request)
        except SessionNotFoundError:
            # Race: the session was evicted between _ensure_session and here.
            # Start a fresh one and retry once — never drop the user's message.
            self._start_session(session_id, now)
            reply = self._service.send_message(request)

        bubbles = to_whatsapp_messages(reply, to=to)
        if is_new_session:
            # CLI/WhatsApp parity (CLAUDE.md §25 Phase 7): a brand-new session
            # gets the same greeting the CLI prints on `_start_session`,
            # ahead of the first turn's own reply — never on a later turn
            # from an already-known sender.
            greeting = OutboundWhatsAppMessage(to=to, body=GREETING_MESSAGE, kind=OutboundKind.TEXT)
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
            StartSessionRequest(session_id=session_id, channel=ChannelId.WHATSAPP, started_at=now)
        )


__all__ = [
    "MEDIA_DEFERRED_NOTICE",
    "VOICE_DEFERRED_NOTICE",
    "WhatsAppGateway",
]
