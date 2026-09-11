"""A record-only stand-in for a real Telegram send (mirrors
`channels/whatsapp/fake_transport.py`). `TelegramTransport` implements the
same `send` signature with a real outbound HTTP call.
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.channels.telegram.models import OutboundTelegramMessage


class FakeTelegramTransport:
    def __init__(self) -> None:
        self.sent: list[OutboundTelegramMessage] = []

    def send(self, messages: Iterable[OutboundTelegramMessage]) -> None:
        self.sent.extend(messages)

    @property
    def bodies(self) -> list[str]:
        return [m.body for m in self.sent]

    def clear(self) -> None:
        self.sent.clear()


__all__ = ["FakeTelegramTransport"]
