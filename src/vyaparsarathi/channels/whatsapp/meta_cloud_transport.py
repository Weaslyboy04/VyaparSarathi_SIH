"""A real Meta Cloud API send transport (CLAUDE.md §25 Phase 7). PURE w.r.t.
`httpx` — this module builds request payloads and reads responses, but never
performs the HTTP call itself, so it stays inside `channels/` without
violating `tests/test_channels_purity.py`'s ban on the channel layer
importing an HTTP client directly. The actual network call is injected as a
plain callable (`JsonPoster`/`MediaUploader`); the real implementation lives
in `scripts/whatsapp_webhook_server.py` (the one live entry point), built
from `utils/http.py`.

Two sends are supported: `send()` (matches `FakeWhatsAppTransport`'s
signature — plain text bubbles, e.g. the advisory itself) and
`send_document()` (a generated DPR PDF, requested explicitly per
CLAUDE.md §25 Phase 6/8 — never sent unprompted). Meta's document send is
two calls: upload the file to `/{phone_number_id}/media` to get a
`media_id`, then send a message referencing that id.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from vyaparsarathi.channels.whatsapp.models import OutboundWhatsAppMessage
from vyaparsarathi.errors import VyaparError


class MetaSendError(VyaparError):
    """A send/upload to the Meta Cloud API failed. Never raised past a
    caller that cannot usefully react to it — the live server logs and
    continues, it does not crash the webhook response."""


class JsonPoster(Protocol):
    def __call__(self, url: str, *, json: Mapping[str, object]) -> dict[str, object]: ...


class MediaUploader(Protocol):
    def __call__(self, url: str, *, file_bytes: bytes, filename: str, mime_type: str) -> str:
        """Upload `file_bytes` to `url`, returning Meta's assigned `media_id`."""


class MetaCloudApiTransport:
    def __init__(
        self,
        *,
        phone_number_id: str,
        poster: JsonPoster,
        uploader: MediaUploader | None = None,
        api_base_url: str = "https://graph.facebook.com/v21.0",
    ) -> None:
        self._phone_number_id = phone_number_id
        self._poster = poster
        self._uploader = uploader
        self._base = api_base_url.rstrip("/")

    def send(self, messages: Iterable[OutboundWhatsAppMessage]) -> None:
        """Same signature as `FakeWhatsAppTransport.send` — a drop-in real
        transport. `DEFERRED_NOTICE` is sent as plain text; there is no
        provider-level distinction for it."""
        for message in messages:
            if not message.body.strip():
                continue
            self._poster(
                f"{self._base}/{self._phone_number_id}/messages",
                json={
                    "messaging_product": "whatsapp",
                    "to": message.to,
                    "type": "text",
                    "text": {"body": message.body},
                },
            )

    def send_document(
        self, *, to: str, file_bytes: bytes, filename: str, caption: str = ""
    ) -> None:
        """Upload `file_bytes` then send it as a document message — the
        channel-neutral `ReportResult.status == REQUESTED` path's WhatsApp
        delivery (CLAUDE.md §25 Phase 6/8: never sent unless explicitly
        requested; the caller decides that, this method only delivers)."""
        if self._uploader is None:
            raise MetaSendError("MetaCloudApiTransport was built without a MediaUploader")
        media_id = self._uploader(
            f"{self._base}/{self._phone_number_id}/media",
            file_bytes=file_bytes,
            filename=filename,
            mime_type="application/pdf",
        )
        document: dict[str, object] = {"id": media_id, "filename": filename}
        if caption:
            document["caption"] = caption
        self._poster(
            f"{self._base}/{self._phone_number_id}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "document",
                "document": document,
            },
        )


__all__ = ["JsonPoster", "MediaUploader", "MetaCloudApiTransport", "MetaSendError"]
