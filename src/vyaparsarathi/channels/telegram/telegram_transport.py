"""A real Telegram Bot API send transport (CLAUDE.md §25 Phase 7). PURE w.r.t.
`httpx` — mirrors `channels/whatsapp/meta_cloud_transport.py`: this module
builds request payloads but never performs the HTTP call itself, so it
stays inside `channels/` without violating `tests/test_channels_purity.py`.
The real `httpx` call is injected (`JsonPoster`/`DocumentSender`); the real
implementation lives in `scripts/telegram_bot_server.py`.

Telegram's document send is simpler than Meta's: one multipart POST to
`sendDocument` carries the file *and* `chat_id`/`caption` together — no
separate upload-then-reference-by-id step.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Protocol

from vyaparsarathi.channels.telegram.models import OutboundTelegramMessage
from vyaparsarathi.errors import VyaparError


class TelegramSendError(VyaparError):
    """A send to the Telegram Bot API failed in a way the caller cannot
    usefully react to — logged and skipped by the poll loop, never raised
    into a crash."""


class JsonPoster(Protocol):
    def __call__(self, url: str, *, json: Mapping[str, object]) -> dict[str, object]: ...


class DocumentSender(Protocol):
    def __call__(
        self,
        url: str,
        *,
        file_bytes: bytes,
        filename: str,
        mime_type: str,
        data: Mapping[str, str],
    ) -> dict[str, object]: ...


class TelegramTransport:
    def __init__(
        self,
        *,
        bot_token: str,
        poster: JsonPoster,
        document_sender: DocumentSender | None = None,
        api_base_url: str = "https://api.telegram.org",
    ) -> None:
        self._base = f"{api_base_url.rstrip('/')}/bot{bot_token}"
        self._poster = poster
        self._document_sender = document_sender

    def send(self, messages: Iterable[OutboundTelegramMessage]) -> None:
        for message in messages:
            if not message.body.strip():
                continue
            self._poster(
                f"{self._base}/sendMessage",
                json={"chat_id": message.chat_id, "text": message.body},
            )

    def send_document(
        self, *, chat_id: str, file_bytes: bytes, filename: str, caption: str = ""
    ) -> None:
        """The channel-neutral `ReportResult.status == REQUESTED` path's
        Telegram delivery (CLAUDE.md §25 Phase 6/8: never sent unless
        explicitly requested; the caller decides that, this method only
        delivers)."""
        if self._document_sender is None:
            raise TelegramSendError("TelegramTransport was built without a DocumentSender")
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption
        self._document_sender(
            f"{self._base}/sendDocument",
            file_bytes=file_bytes,
            filename=filename,
            mime_type="application/pdf",
            data=data,
        )


__all__ = ["DocumentSender", "JsonPoster", "TelegramSendError", "TelegramTransport"]
