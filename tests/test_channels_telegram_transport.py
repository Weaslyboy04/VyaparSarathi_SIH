"""`channels/telegram/telegram_transport.py` (CLAUDE.md §25 Phase 7). Pure
w.r.t. the network — a fake `JsonPoster`/`DocumentSender` stands in for the
real `httpx` call."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from vyaparsarathi.channels.telegram.models import OutboundKind, OutboundTelegramMessage
from vyaparsarathi.channels.telegram.telegram_transport import (
    TelegramSendError,
    TelegramTransport,
)


class _FakePoster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, *, json: Mapping[str, object]) -> dict[str, object]:
        self.calls.append((url, dict(json)))
        return {"ok": True}


class _FakeDocumentSender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes, str, str, dict]] = []

    def __call__(
        self, url: str, *, file_bytes: bytes, filename: str, mime_type: str, data: Mapping[str, str]
    ) -> dict[str, object]:
        self.calls.append((url, file_bytes, filename, mime_type, dict(data)))
        return {"ok": True}


def test_send_posts_one_message_per_bubble() -> None:
    poster = _FakePoster()
    transport = TelegramTransport(bot_token="123:ABC", poster=poster)
    transport.send(
        [
            OutboundTelegramMessage(chat_id="987", body="hello"),
            OutboundTelegramMessage(chat_id="987", body="world", kind=OutboundKind.DEFERRED_NOTICE),
        ]
    )
    assert len(poster.calls) == 2
    url, payload = poster.calls[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert payload == {"chat_id": "987", "text": "hello"}


def test_send_skips_empty_bodies() -> None:
    poster = _FakePoster()
    transport = TelegramTransport(bot_token="123:ABC", poster=poster)
    transport.send([OutboundTelegramMessage(chat_id="987", body="   ")])
    assert poster.calls == []


def test_send_document_carries_chat_id_and_caption() -> None:
    sender = _FakeDocumentSender()
    transport = TelegramTransport(bot_token="123:ABC", poster=_FakePoster(), document_sender=sender)
    transport.send_document(
        chat_id="987", file_bytes=b"%PDF-1.4...", filename="report.pdf", caption="Your report"
    )
    assert len(sender.calls) == 1
    url, file_bytes, filename, mime_type, data = sender.calls[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendDocument"
    assert file_bytes == b"%PDF-1.4..."
    assert filename == "report.pdf"
    assert mime_type == "application/pdf"
    assert data == {"chat_id": "987", "caption": "Your report"}


def test_send_document_without_a_sender_raises() -> None:
    transport = TelegramTransport(bot_token="123:ABC", poster=_FakePoster())
    with pytest.raises(TelegramSendError):
        transport.send_document(chat_id="987", file_bytes=b"x", filename="a.pdf")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
