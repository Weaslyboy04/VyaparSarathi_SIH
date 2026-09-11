"""Provider-neutral Telegram channel boundary (CLAUDE.md §4, §25 Phase 7),
mirroring `channels/whatsapp/`'s design exactly. `TelegramGateway` takes a
raw Telegram `getUpdates` update `dict` and returns neutral
`OutboundTelegramMessage`s (plus, when relevant, a `ReportResult`) —
`AdvisoryService` is the same channel-neutral backend WhatsApp and the CLI
use, so the conversation/DPR behaviour is identical across every channel.

Telegram is simpler than WhatsApp in one structural way this package relies
on: `scripts/telegram_bot_server.py` **long-polls** `getUpdates` rather than
receiving a webhook, so there is no public callback URL, no signature
header, and no tunnel to run at all.
"""

from __future__ import annotations

from vyaparsarathi.channels.telegram.errors import MalformedUpdateError
from vyaparsarathi.channels.telegram.fake_transport import FakeTelegramTransport
from vyaparsarathi.channels.telegram.gateway import TelegramGateway
from vyaparsarathi.channels.telegram.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_telegram_messages,
)
from vyaparsarathi.channels.telegram.models import (
    InboundKind,
    InboundTelegramMessage,
    OutboundKind,
    OutboundTelegramMessage,
)
from vyaparsarathi.channels.telegram.telegram_transport import (
    DocumentSender,
    JsonPoster,
    TelegramSendError,
    TelegramTransport,
)

__all__ = [
    "DocumentSender",
    "FakeTelegramTransport",
    "InboundKind",
    "InboundTelegramMessage",
    "JsonPoster",
    "MalformedUpdateError",
    "OutboundKind",
    "OutboundTelegramMessage",
    "TelegramGateway",
    "TelegramSendError",
    "TelegramTransport",
    "channel_session_id",
    "parse_inbound",
    "to_message_request",
    "to_telegram_messages",
]
