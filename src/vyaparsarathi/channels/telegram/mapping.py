"""Pure translation between Telegram's real `getUpdates` shape and
`app/dto.py` (CLAUDE.md §4, §25 Phase 7). No I/O, no clock — mirrors
`channels/whatsapp/mapping.py` exactly.

A Telegram update looks like::

    {
      "update_id": 123456,
      "message": {
        "message_id": 1,
        "from": {"id": 987, "first_name": "..."},
        "chat": {"id": 987, "type": "private"},
        "date": 1234567890,
        "text": "hello"
      }
    }

— or, for unsupported content, `message` carries `voice`/`photo`/`video`/
`document`/`sticker`/`location`/`contact` instead of `text`. Update types
this system doesn't act on at all (`edited_message`, `channel_post`,
`my_chat_member`, `callback_query`, ...) have no `message` key — `parse_inbound`
raises `MalformedUpdateError` for those, which the poll loop catches and
skips (never a crash, never a fabricated reply).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from vyaparsarathi.app.dto import AdvisoryReply, ChannelId, ExpectedInput, MessageRequest
from vyaparsarathi.channels.telegram.errors import MalformedUpdateError
from vyaparsarathi.channels.telegram.models import (
    InboundKind,
    InboundTelegramMessage,
    OutboundKind,
    OutboundTelegramMessage,
)

_SESSION_PREFIX = "telegram:"
# A bare "2" reply to a numbered-choice question — same narrow 1-3 digit
# bound as WhatsApp's, so a stray amount typed as a fact is never mistaken
# for a choice.
_BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,3})\s*$")
_EMPTY_REPLY_FALLBACK = "I don't have anything to add right now. Please send your question as text."
# Telegram message keys that indicate unsupported content, in the order
# checked (a message will only ever carry one of these).
_UNSUPPORTED_CONTENT_KEYS = (
    "voice",
    "audio",
    "photo",
    "video",
    "video_note",
    "document",
    "sticker",
    "location",
    "contact",
    "animation",
    "poll",
)


def channel_session_id(chat_id: str) -> str:
    """Namespaced session key (CLAUDE.md §24: the same chat id on `web` vs
    `telegram` is two isolated sessions)."""
    cleaned = str(chat_id).strip()
    if not cleaned:
        raise MalformedUpdateError("chat id is empty")
    return f"{_SESSION_PREFIX}{cleaned}"


def parse_inbound(update: Mapping[str, Any]) -> InboundTelegramMessage:
    """Raises `MalformedUpdateError` for a structurally invalid payload, or
    an update type this system does not act on (no `message` key at all —
    e.g. `edited_message`, `my_chat_member`, `callback_query`)."""
    if not isinstance(update, Mapping):
        raise MalformedUpdateError(f"update must be an object, got {type(update).__name__}")

    message = update.get("message")
    if not isinstance(message, Mapping):
        raise MalformedUpdateError("update has no 'message' (an update type this bot ignores)")

    chat = message.get("chat")
    chat_id = chat.get("id") if isinstance(chat, Mapping) else None
    if chat_id is None:
        raise MalformedUpdateError("message has no 'chat.id'")

    message_id = message.get("message_id")
    provider_message_id = str(message_id) if message_id is not None else ""

    text = message.get("text")
    if isinstance(text, str) and text.strip():
        return InboundTelegramMessage(
            chat_id=str(chat_id),
            kind=InboundKind.TEXT,
            text=text,
            provider_message_id=provider_message_id,
        )

    for key in _UNSUPPORTED_CONTENT_KEYS:
        if key in message:
            return InboundTelegramMessage(
                chat_id=str(chat_id),
                kind=InboundKind.UNSUPPORTED,
                unsupported_type=key,
                provider_message_id=provider_message_id,
            )

    raise MalformedUpdateError("message has no recognised content (no text, no known media key)")


def to_message_request(
    message: InboundTelegramMessage, *, session_id: str, received_at: datetime
) -> MessageRequest:
    """`UNSUPPORTED` never reaches here — the gateway answers it directly."""
    if message.kind is InboundKind.UNSUPPORTED:  # pragma: no cover - gateway guards this
        raise ValueError("to_message_request called with an UNSUPPORTED inbound message")

    match = _BARE_NUMBER_RE.match(message.text)
    selected_choice = int(match.group(1)) if match else None
    return MessageRequest(
        session_id=session_id,
        text=message.text,
        channel=ChannelId.TELEGRAM,
        received_at=received_at,
        selected_choice=selected_choice,
    )


def to_telegram_messages(
    reply: AdvisoryReply, *, chat_id: str
) -> tuple[OutboundTelegramMessage, ...]:
    """`AdvisoryReply` -> one or more outbound bubbles, same shape as
    `channels/whatsapp/mapping.py::to_whatsapp_messages` — numbered choice
    lines are already inline in each message's text."""
    bubbles = [
        OutboundTelegramMessage(chat_id=chat_id, body=m.text, kind=OutboundKind.TEXT)
        for m in reply.messages
        if m.text.strip()
    ]
    if reply.choices and reply.expects is ExpectedInput.CHOICE:
        bubbles.append(
            OutboundTelegramMessage(
                chat_id=chat_id,
                body=f"Reply with a number from 1 to {len(reply.choices)}.",
                kind=OutboundKind.TEXT,
            )
        )
    if not bubbles:
        bubbles.append(OutboundTelegramMessage(chat_id=chat_id, body=_EMPTY_REPLY_FALLBACK))
    return tuple(bubbles)


__all__ = [
    "channel_session_id",
    "parse_inbound",
    "to_message_request",
    "to_telegram_messages",
]
