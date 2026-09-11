"""Live WhatsApp (Meta Cloud API) webhook receiver — the real, end-to-end
integration (CLAUDE.md §25 Phase 6/7/8). MANUAL-ONLY entry point — like
`phase6_chat.py`/`phase6_live_smoke.py`, this is a runnable script, not
something pytest imports or executes.

Every inbound message runs through the exact same `AdvisoryService`
(NORMAL mode) that the CLI uses: LLM extraction, follow-up questions, the
one consolidated advisory, and — on an explicit "yes, generate the report" —
a real DPR, sent back as a WhatsApp document. No shortcuts and no parallel
pipeline: `WhatsAppGateway`/`MetaCloudApiTransport` are the same channel
code exercised by `tests/test_channels_whatsapp_gateway.py`, just given a
real transport instead of `FakeWhatsAppTransport`.

Routes (same two as before):

* ``GET /webhook`` — Meta's one-time verification handshake, checked against
  ``VYAPAR_WHATSAPP_VERIFY_TOKEN``.
* ``POST /webhook`` — a real inbound event. The raw body's
  ``X-Hub-Signature-256`` is checked against ``VYAPAR_WHATSAPP_APP_SECRET``
  before anything else; a payload that fails is rejected (401) and never
  parsed. Each message in the (possibly batched) body is converted to the
  neutral shape, run through `WhatsAppGateway.handle_webhook`, and its
  reply bubbles are sent back for real via `MetaCloudApiTransport`. If that
  turn's `ReportResult.status == REQUESTED`, a DPR is assembled in memory
  (`DprService.build` + `render_pdf_bytes` — no file is written to disk;
  CLAUDE.md §25 Phase 8: still composition-only, no engine re-run) and sent
  as a document.

Sessions persist in `Settings.db_url` (SQLite by default) via
`database/session_sql.py::create_session_repository`, so a server restart
does not lose an in-progress conversation.

Run:
    ./.venv/Scripts/python.exe scripts/whatsapp_webhook_server.py [--port 8000]

Requires `VYAPAR_WHATSAPP_ACCESS_TOKEN`, `VYAPAR_WHATSAPP_PHONE_NUMBER_ID`,
`VYAPAR_WHATSAPP_VERIFY_TOKEN` set (see `.env.example`); a missing
`VYAPAR_WHATSAPP_APP_SECRET` degrades to "accept without verification",
loudly logged, never silent.
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from vyaparsarathi.app.dto import ReportStatus
from vyaparsarathi.app.runtime import AdvisoryRuntime, build_default_runtime
from vyaparsarathi.app.service import AdvisoryService
from vyaparsarathi.channels.whatsapp import (
    MetaCloudApiTransport,
    WhatsAppGateway,
    channel_session_id,
    iter_neutral_inbound_payloads,
    verify_meta_signature,
)
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.conversation.conversation_config import ConversationMode
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.database.session_sql import create_session_repository
from vyaparsarathi.dpr import DprService, render_pdf_bytes
from vyaparsarathi.utils.http import build_client, post_json, post_multipart

# See scripts/telegram_bot_server.py for why: Windows' default
# console/redirected-file encoding cannot encode most of what a real
# advisory reply contains (₹, en/em dashes, ...) — without this, one
# non-ASCII character in a log line kills the whole server.
for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if callable(_reconfigure):
        _reconfigure(encoding="utf-8", errors="replace")

# Set once by `main()` before the server starts serving; read by every
# request in every handler thread. A stdlib-`http.server` script has no
# request-scoped dependency injection, so this mirrors how `phase6_chat.py`
# builds one shared runtime/session-repo for the life of the process.
_gateway: WhatsAppGateway | None = None
_transport: MetaCloudApiTransport | None = None
_sessions: SessionRepository | None = None
_settings: Settings | None = None


def _build_transport(settings: Settings, http_client: httpx.Client) -> MetaCloudApiTransport:
    def poster(url: str, *, json: dict[str, Any]) -> dict[str, Any]:
        return post_json(
            http_client,
            url,
            json=json,
            max_retries=settings.http_max_retries,
            backoff_base_s=settings.http_backoff_base_s,
        )

    def uploader(url: str, *, file_bytes: bytes, filename: str, mime_type: str) -> str:
        result = post_multipart(
            http_client,
            url,
            file_bytes=file_bytes,
            filename=filename,
            mime_type=mime_type,
            data={"messaging_product": "whatsapp"},
            max_retries=settings.http_max_retries,
            backoff_base_s=settings.http_backoff_base_s,
        )
        media_id = result.get("id")
        if not isinstance(media_id, str):
            raise RuntimeError(f"Meta media upload did not return an 'id': {result!r}")
        return media_id

    return MetaCloudApiTransport(
        phone_number_id=settings.whatsapp_phone_number_id,
        poster=poster,
        uploader=uploader,
        api_base_url=settings.whatsapp_api_base_url,
    )


def _handle_report_if_requested(payload: dict[str, Any], report_status: ReportStatus) -> None:
    assert _sessions is not None and _transport is not None
    if report_status is not ReportStatus.REQUESTED:
        return
    sender = payload["from"]
    session_id = channel_session_id(sender)
    try:
        doc = DprService(_sessions).build(session_id, generated_at=datetime.now(UTC))
        pdf_bytes = render_pdf_bytes(doc)
        _transport.send_document(
            to=sender,
            file_bytes=pdf_bytes,
            filename=f"VyaparSarathi_DPR_{doc.report_id}.pdf",
            caption="Your VyaparSarathi project report",
        )
        print(f"[webhook] sent DPR {doc.report_id} to {sender} ({len(pdf_bytes):,} bytes)")
    except Exception:  # noqa: BLE001 - best-effort delivery; never crash the webhook response
        print(f"[webhook] DPR generation/send FAILED for {session_id}:", file=sys.stderr)
        traceback.print_exc()


class _Handler(BaseHTTPRequestHandler):
    server_version = "VyaparSarathiWhatsAppWebhook/0.2"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: A003 - stdlib override
        sys.stderr.write(f"[webhook] {self.address_string()} - {fmt % args}\n")

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlsplit(self.path)
        if parsed.path != "/webhook":
            self._reply(404, b"not found")
            return
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        assert _settings is not None
        expected = _settings.whatsapp_verify_token
        expected_value = expected.get_secret_value() if expected is not None else ""
        if (
            params.get("hub.mode") == "subscribe"
            and expected_value
            and params.get("hub.verify_token") == expected_value
        ):
            challenge = params.get("hub.challenge", "")
            print(
                f"[webhook] GET verification handshake OK, echoing challenge "
                f"({len(challenge)} chars)"
            )
            self._reply(200, challenge.encode("utf-8"))
        else:
            print("[webhook] GET verification handshake REJECTED (bad/missing verify_token)")
            self._reply(403, b"forbidden")

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlsplit(self.path)
        if parsed.path != "/webhook":
            self._reply(404, b"not found")
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw_body = self.rfile.read(length) if length else b""

        assert _settings is not None
        app_secret = _settings.whatsapp_app_secret
        signature = self.headers.get("X-Hub-Signature-256", "")
        if app_secret is not None and app_secret.get_secret_value():
            if not verify_meta_signature(app_secret.get_secret_value(), raw_body, signature):
                print("[webhook] POST REJECTED: signature verification failed")
                self._reply(401, b"invalid signature")
                return
        else:
            print(
                "[webhook] WARNING: VYAPAR_WHATSAPP_APP_SECRET is not set — "
                "accepting this POST WITHOUT signature verification"
            )

        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            print("[webhook] POST body was not valid JSON — ignoring, replying 200 anyway")
            self._reply(200, b"EVENT_RECEIVED")
            return

        # Always 200 immediately after this point — Meta expects a fast ack
        # regardless of what happens processing the message(s) inside it.
        self._reply(200, b"EVENT_RECEIVED")

        payloads = iter_neutral_inbound_payloads(body)
        if not payloads:
            print("[webhook] POST had no actionable message (status update / empty batch)")
        assert _gateway is not None
        for payload in payloads:
            try:
                bubbles, report = _gateway.handle_webhook(payload)
                if _transport is not None:
                    _transport.send(bubbles)
                else:
                    print("[webhook] WARNING: no transport configured, reply not sent")
                for bubble in bubbles:
                    print(f"[webhook] -> {payload['from']}: {bubble.body[:200]!r}")
                if report is not None:
                    _handle_report_if_requested(payload, report.status)
            except Exception:  # noqa: BLE001 - one bad message must not drop the rest
                sender = payload.get("from")
                print(f"[webhook] error handling inbound from {sender}:", file=sys.stderr)
                traceback.print_exc()

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main(argv: list[str] | None = None) -> int:
    global _gateway, _transport, _sessions, _settings

    parser = argparse.ArgumentParser(prog="whatsapp_webhook_server", description=__doc__)
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args(argv)

    settings = get_settings()
    _settings = settings

    missing = [
        name
        for name, value in (
            ("VYAPAR_WHATSAPP_ACCESS_TOKEN", settings.whatsapp_access_token),
            ("VYAPAR_WHATSAPP_PHONE_NUMBER_ID", settings.whatsapp_phone_number_id or None),
        )
        if not value
    ]
    if missing:
        print(f"error: missing required settings: {', '.join(missing)}", file=sys.stderr)
        return 2
    if not (settings.whatsapp_verify_token and settings.whatsapp_verify_token.get_secret_value()):
        print(
            "warning: VYAPAR_WHATSAPP_VERIFY_TOKEN is not set — the GET "
            "verification handshake will always be rejected.",
            file=sys.stderr,
        )
    if not (settings.whatsapp_app_secret and settings.whatsapp_app_secret.get_secret_value()):
        print(
            "warning: VYAPAR_WHATSAPP_APP_SECRET is not set — inbound POSTs "
            "will be accepted WITHOUT signature verification.",
            file=sys.stderr,
        )
    if not settings.llm_enabled:
        print(
            "warning: VYAPAR_LLM_ENABLED is not true — replies will require "
            "structured input the WhatsApp channel cannot send; free-text "
            "messages will get an 'unclear' response every turn.",
            file=sys.stderr,
        )

    assert settings.whatsapp_access_token is not None  # checked above
    http_client = build_client(
        settings.user_agent,
        settings.http_timeout_s,
        headers={"Authorization": f"Bearer {settings.whatsapp_access_token.get_secret_value()}"},
    )

    runtime: AdvisoryRuntime = build_default_runtime(settings, mode=ConversationMode.NORMAL)
    _sessions = create_session_repository(settings.db_url)
    service = AdvisoryService(sessions=_sessions, runtime=runtime)
    _gateway = WhatsAppGateway(service)
    _transport = _build_transport(settings, http_client)

    server = ThreadingHTTPServer(("0.0.0.0", args.port), _Handler)
    print(f"listening on http://0.0.0.0:{args.port}/webhook (Ctrl+C to stop)")
    print(f"sessions persisted at: {settings.db_url}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        http_client.close()
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
