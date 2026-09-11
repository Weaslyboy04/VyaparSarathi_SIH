"""A record-only stand-in for a real WhatsApp provider send (CLAUDE.md §25
Phase 7: "Use a fake provider/test adapter only. No Meta/Twilio/live SDK
dependency yet"). A real transport implements the same `send` signature with
an actual outbound HTTP call.
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.channels.whatsapp.models import OutboundWhatsAppMessage


class FakeWhatsAppTransport:
    def __init__(self) -> None:
        self.sent: list[OutboundWhatsAppMessage] = []

    def send(self, messages: Iterable[OutboundWhatsAppMessage]) -> None:
        self.sent.extend(messages)

    @property
    def bodies(self) -> list[str]:
        return [m.body for m in self.sent]

    def clear(self) -> None:
        self.sent.clear()


__all__ = ["FakeWhatsAppTransport"]
