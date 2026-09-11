"""Live Telegram bot — real, end-to-end integration (CLAUDE.md §25 Phase
6/7/8). MANUAL-ONLY entry point — like `phase6_chat.py`/
`whatsapp_webhook_server.py`, this is a runnable script, not something
pytest imports or executes.

Unlike WhatsApp, this **long-polls** Telegram's `getUpdates` — no public
callback URL, no webhook signature, no tunnel. Every inbound message runs
through the exact same `AdvisoryService` (NORMAL mode) the CLI and the
WhatsApp channel use: LLM extraction, follow-up questions, one consolidated
advisory, and — on an explicit "yes, generate the report" — a real DPR sent
back as a document. `TelegramGateway`/`TelegramTransport` are the same
channel code exercised by `tests/test_channels_telegram_gateway.py`, just
given a real transport instead of `FakeTelegramTransport`.

Sessions persist in `Settings.db_url` (SQLite by default, shared with the
WhatsApp channel — session ids are namespaced per channel, so there is no
collision) via `database/session_sql.py::create_session_repository`.

Run:
    ./.venv/Scripts/python.exe scripts/telegram_bot_server.py

Requires `VYAPAR_TELEGRAM_BOT_TOKEN` (from @BotFather — see `.env.example`).
"""

from __future__ import annotations

import sys
import time
import traceback
from datetime import UTC, datetime
from typing import Any

import httpx

from vyaparsarathi.app.dto import ReportStatus
from vyaparsarathi.app.runtime import AdvisoryRuntime, build_default_runtime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.channels.telegram import (
    MalformedUpdateError,
    TelegramGateway,
    TelegramTransport,
    channel_session_id,
)
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.conversation.conversation_config import ConversationMode
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.database.session_sql import create_session_repository
from vyaparsarathi.dpr import DprService, render_pdf_bytes
from vyaparsarathi.utils.http import build_client, post_json, post_multipart, redact_url

# Windows' default console/redirected-file encoding (cp1252) cannot encode
# most of what real advisory replies contain (₹, en/em dashes, ...) — without
# this, a single non-ASCII character in a log line kills the whole poll loop
# (a real incident: the process died mid-session on "₹" in a reply's own
# text). UTF-8 with `errors="replace"` so a print always succeeds even for a
# genuinely unencodable character; user-facing message content sent via the
# Telegram API is never affected by this — only our own local logging.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8", errors="replace")

_POLL_TIMEOUT_S = 30  # Telegram holds the connection open server-side up to this long


def _build_transport(settings: Settings, http_client: httpx.Client) -> TelegramTransport:
    assert settings.telegram_bot_token is not None

    def poster(url: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return post_json(
            http_client,
            url,
            json=json,
            max_retries=settings.http_max_retries,
            backoff_base_s=settings.http_backoff_base_s,
        )

    def document_sender(
        url: str, *, file_bytes: bytes, filename: str, mime_type: str, data: dict[str, str]
    ) -> dict[str, Any]:
        return post_multipart(
            http_client,
            url,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            field_name="document",  # Telegram's sendDocument, not Meta's "file"
            data=data,
            max_retries=settings.http_max_retries,
            backoff_base_s=settings.http_backoff_base_s,
        )

    return TelegramTransport(
        bot_token=settings.telegram_bot_token.get_secret_value(),
        poster=poster,
        document_sender=document_sender,
        api_base_url=settings.telegram_api_base_url,
    )


def _get_updates(
    http_client: httpx.Client, base: str, *, offset: int | None
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {"timeout": _POLL_TIMEOUT_S}
    if offset is not None:
        params["offset"] = offset
    response = http_client.get(f"{base}/getUpdates", params=params)
    response.raise_for_status()
    body = response.json()
    if not body.get("ok"):
        raise RuntimeError(f"getUpdates returned ok=false: {body!r}")
    result = body.get("result")
    return result if isinstance(result, list) else []


def _handle_report_if_requested(
    sessions: SessionRepository,
    transport: TelegramTransport,
    chat_id: str,
    report_status: ReportStatus,
) -> None:
    if report_status is not ReportStatus.REQUESTED:
        return
    session_id = channel_session_id(chat_id)
    try:
        doc = DprService(sessions).build(session_id, generated_at=datetime.now(UTC))
        pdf_bytes = render_pdf_bytes(doc)
        transport.send_document(
            chat_id=chat_id,
            file_bytes=pdf_bytes,
            filename=f"VyaparSarathi_DPR_{doc.report_id}.pdf",
            caption="Your VyaparSarathi project report",
        )
        print(f"[telegram] sent DPR {doc.report_id} to {chat_id} ({len(pdf_bytes):,} bytes)")
    except Exception:  # noqa: BLE001 - best-effort delivery; never crash the poll loop
        print(f"[telegram] DPR generation/send FAILED for {session_id}:", file=sys.stderr)
        traceback.print_exc()


def main() -> int:
    settings = get_settings()
    if not (settings.telegram_bot_token and settings.telegram_bot_token.get_secret_value()):
        print(
            "error: VYAPAR_TELEGRAM_BOT_TOKEN is not set (get one from @BotFather)",
            file=sys.stderr,
        )
        return 2
    if not settings.llm_enabled:
        print(
            "warning: VYAPAR_LLM_ENABLED is not true — replies will require "
            "structured input this channel cannot send; free-text messages "
            "will get an 'unclear' response every turn.",
            file=sys.stderr,
        )

    assert settings.telegram_bot_token is not None  # checked above
    http_client = build_client(settings.user_agent, settings.http_timeout_s + _POLL_TIMEOUT_S)
    token = settings.telegram_bot_token.get_secret_value()
    base = f"{settings.telegram_api_base_url.rstrip('/')}/bot{token}"

    runtime: AdvisoryRuntime = build_default_runtime(settings, mode=ConversationMode.NORMAL)
    sessions = create_session_repository(settings.db_url)
    service = AdvisoryService(sessions=sessions, runtime=runtime)
    gateway = TelegramGateway(service)
    transport = _build_transport(settings, http_client)

    print("polling Telegram getUpdates (Ctrl+C to stop)")
    print(f"sessions persisted at: {settings.db_url}")

    offset: int | None = None
    try:
        while True:
            try:
                updates = _get_updates(http_client, base, offset=offset)
            except (httpx.HTTPError, RuntimeError) as exc:
                safe = redact_url(str(exc))
                print(f"[telegram] getUpdates failed, retrying in 2s: {safe}", file=sys.stderr)
                time.sleep(2)
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    offset = update_id + 1
                try:
                    bubbles, report = gateway.handle_update(update)
                except MalformedUpdateError:
                    continue  # an update type we don't act on (or truly malformed) — skip
                except Exception:  # noqa: BLE001 - one bad update must not crash the loop
                    print("[telegram] error handling update:", file=sys.stderr)
                    traceback.print_exc()
                    continue

                transport.send(bubbles)
                for bubble in bubbles:
                    print(f"[telegram] -> {bubble.chat_id}: {bubble.body[:200]!r}")
                if report is not None:
                    message = update.get("message") or {}
                    chat = message.get("chat") or {}
                    chat_id = str(chat.get("id", ""))
                    if chat_id:
                        _handle_report_if_requested(sessions, transport, chat_id, report.status)
    except KeyboardInterrupt:
        pass
    finally:
        http_client.close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
