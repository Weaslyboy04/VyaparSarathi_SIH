"""Pure translation between the neutral WhatsApp DTOs and `app/dto.py`
(CLAUDE.md §4, §25 Phase 7). No I/O, no clock, no `AdvisoryService` — the
`WhatsAppGateway` supplies `session_id` and `received_at`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from vyaparsarathi.app.dto import AdvisoryReply, ChannelId, ExpectedInput, MessageRequest
from vyaparsarathi.channels.whatsapp.errors import MalformedWebhookError
from vyaparsarathi.channels.whatsapp.models import (
    InboundKind,
    InboundWhatsAppMessage,
    OutboundKind,
    OutboundWhatsAppMessage,
)

_SESSION_PREFIX = "whatsapp:"
# A bare "2" reply to a numbered-choice question. Deliberately narrow: 1-3
# digits, so a stray amount like "40000" typed as a fact is never mistaken for
# a choice. An out-of-range number still flows through as `selected_choice`;
# the planner ignores a selection with no pending choice (verified) rather
# than erroring.
_BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*$")

_EMPTY_REPLY_FALLBACK = "I don't have anything to add right now. Please send your question as text."


def channel_session_id(sender: str) -> str:
    """The sender's id, namespaced to this channel, used verbatim as the
    `SessionRepository` key (CLAUDE.md §24: the same number on `web` vs
    `whatsapp` is two isolated sessions; the phone number is only ever an
    identifier here, never data that changes advice)."""
    cleaned = sender.strip()
    if not cleaned:
        raise MalformedWebhookError("inbound message has an empty sender")
    return f"{_SESSION_PREFIX}{cleaned}"


def parse_inbound(payload: Mapping[str, Any]) -> InboundWhatsAppMessage:
    """Validate a neutral inbound webhook `dict` (see `models.py`). Raises
    `MalformedWebhookError` on a structural problem; an unknown message
    `type` is NOT an error — it maps to `InboundKind.UNSUPPORTED`."""
    if not isinstance(payload, Mapping):
        raise MalformedWebhookError(
            f"webhook payload must be an object, got {type(payload).__name__}"
        )

    sender = payload.get("from")
    if not isinstance(sender, str) or not sender.strip():
        raise MalformedWebhookError("webhook payload has no non-empty 'from'")

    msg_type = payload.get("type")
    if not isinstance(msg_type, str) or not msg_type.strip():
        raise MalformedWebhookError("webhook payload has no non-empty 'type'")
    msg_type = msg_type.strip()

    provider_message_id = _as_str(payload.get("message_id"))
    locale_hint = _as_str(payload.get("locale"))

    if msg_type == "text":
        text = payload.get("text")
        if not isinstance(text, str) or not text.strip():
            raise MalformedWebhookError("a 'text' message must carry non-empty 'text'")
        return InboundWhatsAppMessage(
            **{"from": sender},
            kind=InboundKind.TEXT,
            text=text,
            provider_message_id=provider_message_id,
            locale_hint=locale_hint,
        )

    if msg_type == "interactive":
        interactive = payload.get("interactive")
        if not isinstance(interactive, Mapping):
            raise MalformedWebhookError(
                "an 'interactive' message must carry an 'interactive' object"
            )
        reply_id = interactive.get("reply_id")
        if not isinstance(reply_id, str) or not reply_id.strip():
            raise MalformedWebhookError(
                "an 'interactive' message must carry 'interactive.reply_id'"
            )
        return InboundWhatsAppMessage(
            **{"from": sender},
            kind=InboundKind.INTERACTIVE_REPLY,
            text=_as_str(interactive.get("title")),
            choice_id=reply_id.strip(),
            provider_message_id=provider_message_id,
            locale_hint=locale_hint,
        )

    return InboundWhatsAppMessage(
        **{"from": sender},
        kind=InboundKind.UNSUPPORTED,
        unsupported_type=msg_type,
        provider_message_id=provider_message_id,
        locale_hint=locale_hint,
    )


def to_message_request(
    message: InboundWhatsAppMessage, *, session_id: str, received_at: datetime
) -> MessageRequest:
    """Map a supported inbound message onto a channel-neutral `MessageRequest`.
    `UNSUPPORTED` never reaches here — the gateway answers it directly."""
    if message.kind is InboundKind.UNSUPPORTED:  # pragma: no cover - gateway guards this
        raise ValueError("to_message_request called with an UNSUPPORTED inbound message")

    selected_choice = _choice_from(message)
    return MessageRequest(
        session_id=session_id,
        text=message.text,
        channel=ChannelId.WHATSAPP,
        received_at=received_at,
        locale_hint=message.locale_hint,
        selected_choice=selected_choice,
    )


def to_whatsapp_messages(reply: AdvisoryReply, *, to: str) -> tuple[OutboundWhatsAppMessage, ...]:
    """`AdvisoryReply` -> one or more outbound bubbles. Each non-empty
    `OutboundMessage.text` becomes one bubble (the deterministic renderer
    already emits numbered choice lines as their own lines); when the reply
    expects a choice, a final plain-text instruction bubble is appended so a
    text-only provider is sufficient (requirement: numbered choices rendered
    safely as text first)."""
    bubbles = [
        OutboundWhatsAppMessage(to=to, body=m.text, kind=OutboundKind.TEXT)
        for m in reply.messages
        if m.text.strip()
    ]
    if reply.choices and reply.expects is ExpectedInput.CHOICE:
        bubbles.append(
            OutboundWhatsAppMessage(
                to=to,
                body=f"Reply with a number from 1 to {len(reply.choices)}.",
                kind=OutboundKind.TEXT,
            )
        )
    if not bubbles:
        bubbles.append(
            OutboundWhatsAppMessage(to=to, body=_EMPTY_REPLY_FALLBACK, kind=OutboundKind.TEXT)
        )
    return tuple(bubbles)


def _choice_from(message: InboundWhatsAppMessage) -> int | None:
    if message.kind is InboundKind.INTERACTIVE_REPLY:
        return int(message.choice_id) if message.choice_id.isdigit() else None
    match = _BARE_NUMBER_RE.match(message.text)
    return int(match.group(1)) if match else None


def _as_str(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


__all__ = [
    "channel_session_id",
    "parse_inbound",
    "to_message_request",
    "to_whatsapp_messages",
]
