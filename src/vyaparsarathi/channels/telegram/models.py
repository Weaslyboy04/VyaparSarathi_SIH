"""Provider-neutral Telegram message DTOs (CLAUDE.md §4, §25 Phase 7).
Mirrors `channels/whatsapp/models.py` — no `OutboundKind` distinction
matters at the transport level (both send as plain text/document), it exists
only so the deferred-notice path is self-documenting, same as WhatsApp's.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InboundKind(StrEnum):
    TEXT = "text"
    UNSUPPORTED = "unsupported"  # voice / photo / document / sticker / location / ...


class OutboundKind(StrEnum):
    TEXT = "text"
    DEFERRED_NOTICE = "deferred_notice"


class InboundTelegramMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: str = Field(min_length=1)
    kind: InboundKind
    text: str = ""  # TEXT only
    unsupported_type: str = ""  # UNSUPPORTED only
    provider_message_id: str = ""  # audit only, never used to change advice


class OutboundTelegramMessage(BaseModel):
    """One outbound bubble. Numbered choices are already inline in `body`,
    exactly like WhatsApp's — no Telegram inline-keyboard dependency
    (CLAUDE.md-safe parity: a plain-text-only provider would still work)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chat_id: str = Field(min_length=1)
    body: str
    kind: OutboundKind = OutboundKind.TEXT


__all__ = [
    "InboundKind",
    "InboundTelegramMessage",
    "OutboundKind",
    "OutboundTelegramMessage",
]
