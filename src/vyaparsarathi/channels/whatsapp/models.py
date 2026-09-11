"""Provider-neutral WhatsApp message DTOs (CLAUDE.md §4, §25 Phase 7).

This module deliberately does **not** model Meta Cloud API's
`entry[].changes[].value.messages[]` nesting or Twilio's form-encoded
`Body`/`From` — a provider-specific normaliser (added when a real provider is
chosen) is responsible for producing the small neutral `dict` this package
parses. The neutral inbound contract `mapping.parse_inbound` accepts:

    {
      "from": "<sender id, required>",       # channel-scoped identifier only
      "type": "text" | "interactive" | "<anything else>",
      "text": "<the message>",               # required when type == "text"
      "interactive": {                        # required when type == "interactive"
        "reply_id": "<the row/button id we set on the outbound choice>",
        "title":    "<the row/button label, optional>"
      },
      "message_id": "<provider id, optional, audit only>",
      "locale":     "<optional BCP-47 hint>"
    }

Any `type` other than `text` / `interactive` (audio, voice, image, document,
location, ...) parses to `InboundKind.UNSUPPORTED` — a first-class "we can't
do that yet" path (CLAUDE.md §2: voice is deferred, never faked), not an
error.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class InboundKind(StrEnum):
    TEXT = "text"
    INTERACTIVE_REPLY = "interactive_reply"  # a tap on a numbered choice we sent
    UNSUPPORTED = "unsupported"  # media / voice / location / ... — deferred


class OutboundKind(StrEnum):
    TEXT = "text"
    DEFERRED_NOTICE = "deferred_notice"  # "voice isn't supported yet", etc.


class InboundWhatsAppMessage(BaseModel):
    """The parsed, validated neutral inbound message. Immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    sender: str = Field(alias="from", min_length=1)
    kind: InboundKind
    # TEXT: the raw message text. INTERACTIVE_REPLY: the choice label (kept for
    # audit / fallback). UNSUPPORTED: "".
    text: str = ""
    # INTERACTIVE_REPLY only: the id we put on the outbound choice row — for
    # choices this package renders, that is the 1-based index as a string.
    choice_id: str = ""
    # UNSUPPORTED only: the provider's message type we cannot handle yet.
    unsupported_type: str = ""
    # Audit only — never used to change advice (CLAUDE.md §23).
    provider_message_id: str = ""
    locale_hint: str = ""


class OutboundWhatsAppMessage(BaseModel):
    """One outbound bubble. A real provider adapter turns each of these into a
    single text send to `to`; numbered choices are already inline in `body`
    (CLAUDE.md-safe: no dependency on interactive-list rendering)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    to: str = Field(min_length=1)
    body: str
    kind: OutboundKind = OutboundKind.TEXT


__all__ = [
    "InboundKind",
    "InboundWhatsAppMessage",
    "OutboundKind",
    "OutboundWhatsAppMessage",
]
