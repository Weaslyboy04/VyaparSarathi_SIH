"""`channels/whatsapp/meta_cloud_transport.py` (CLAUDE.md §25 Phase 7). Pure
w.r.t. the network — a fake `JsonPoster`/`MediaUploader` stands in for the
real `httpx` call, so this never touches the network."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from vyaparsarathi.channels.whatsapp.meta_cloud_transport import (
    MetaCloudApiTransport,
    MetaSendError,
)
from vyaparsarathi.channels.whatsapp.models import OutboundKind, OutboundWhatsAppMessage


class _FakePoster:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url: str, *, json: Mapping[str, object]) -> dict[str, object]:
        self.calls.append((url, dict(json)))
        return {"messages": [{"id": "wamid.sent"}]}


class _FakeUploader:
    def __init__(self, media_id: str = "media-123") -> None:
        self.calls: list[tuple[str, bytes, str, str]] = []
        self._media_id = media_id

    def __call__(self, url: str, *, file_bytes: bytes, filename: str, mime_type: str) -> str:
        self.calls.append((url, file_bytes, filename, mime_type))
        return self._media_id


def test_send_posts_one_text_message_per_bubble() -> None:
    poster = _FakePoster()
    transport = MetaCloudApiTransport(
        phone_number_id="123", poster=poster, api_base_url="https://graph.facebook.com/v21.0"
    )
    transport.send(
        [
            OutboundWhatsAppMessage(to="9198", body="hello"),
            OutboundWhatsAppMessage(to="9198", body="world", kind=OutboundKind.DEFERRED_NOTICE),
        ]
    )
    assert len(poster.calls) == 2
    url, payload = poster.calls[0]
    assert url == "https://graph.facebook.com/v21.0/123/messages"
    assert payload == {
        "messaging_product": "whatsapp",
        "to": "9198",
        "type": "text",
        "text": {"body": "hello"},
    }


def test_send_skips_empty_bodies() -> None:
    poster = _FakePoster()
    transport = MetaCloudApiTransport(phone_number_id="123", poster=poster)
    transport.send([OutboundWhatsAppMessage(to="9198", body="   ")])
    assert poster.calls == []


def test_send_document_uploads_then_sends_with_media_id() -> None:
    poster = _FakePoster()
    uploader = _FakeUploader(media_id="media-abc")
    transport = MetaCloudApiTransport(
        phone_number_id="123",
        poster=poster,
        uploader=uploader,
        api_base_url="https://graph.facebook.com/v21.0",
    )
    transport.send_document(
        to="9198", file_bytes=b"%PDF-1.4...", filename="report.pdf", caption="Your report"
    )

    assert len(uploader.calls) == 1
    upload_url, file_bytes, filename, mime_type = uploader.calls[0]
    assert upload_url == "https://graph.facebook.com/v21.0/123/media"
    assert file_bytes == b"%PDF-1.4..."
    assert filename == "report.pdf"
    assert mime_type == "application/pdf"

    assert len(poster.calls) == 1
    _url, payload = poster.calls[0]
    assert payload == {
        "messaging_product": "whatsapp",
        "to": "9198",
        "type": "document",
        "document": {"id": "media-abc", "filename": "report.pdf", "caption": "Your report"},
    }


def test_send_document_without_an_uploader_raises() -> None:
    transport = MetaCloudApiTransport(phone_number_id="123", poster=_FakePoster())
    with pytest.raises(MetaSendError):
        transport.send_document(to="9198", file_bytes=b"x", filename="a.pdf")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
