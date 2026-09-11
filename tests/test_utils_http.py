"""`utils/http.py`'s `post_json`/`post_multipart`/`redact_url` (CLAUDE.md
§4.1, §24). All HTTP is mocked (`respx`)."""

from __future__ import annotations

import httpx
import pytest
import respx

from vyaparsarathi.errors import HttpError
from vyaparsarathi.utils.http import build_client, post_json, post_multipart, redact_url

URL = "https://api.example.test/v1/send"


@respx.mock
def test_post_json_returns_the_parsed_body() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "id": "abc"}))
    with build_client("test-agent", 5.0) as client:
        body = post_json(client, URL, json={"a": 1}, max_retries=0, backoff_base_s=0.0)
    assert body == {"ok": True, "id": "abc"}


@respx.mock
def test_post_json_raises_http_error_on_4xx_without_retrying() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(400, json={"error": "bad"}))
    with build_client("test-agent", 5.0) as client:
        with pytest.raises(HttpError):
            post_json(client, URL, json={}, max_retries=2, backoff_base_s=0.0)
    assert route.call_count == 1  # a plain 4xx is not retryable


@respx.mock
def test_post_multipart_sends_the_file_and_returns_parsed_body() -> None:
    respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True, "media_id": "m1"}))
    with build_client("test-agent", 5.0) as client:
        body = post_multipart(
            client,
            URL,
            file_bytes=b"%PDF-1.4",
            filename="report.pdf",
            mime_type="application/pdf",
            data={"chat_id": "987"},
            max_retries=0,
            backoff_base_s=0.0,
        )
    assert body == {"ok": True, "media_id": "m1"}
    sent = respx.calls.last.request
    assert b"report.pdf" in sent.content
    assert b"987" in sent.content
    # Default field name matches Meta's media-upload endpoint.
    assert b'name="file"' in sent.content


@respx.mock
def test_post_multipart_honours_a_custom_field_name() -> None:
    """Regression: Telegram's `sendDocument` specifically requires the
    multipart field to be named `"document"`, not `"file"` — a hardcoded
    field name here caused a real live failure ("there is no document in
    the request") on the first Telegram DPR send."""
    respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True}))
    with build_client("test-agent", 5.0) as client:
        post_multipart(
            client,
            URL,
            file_bytes=b"%PDF-1.4",
            filename="report.pdf",
            mime_type="application/pdf",
            field_name="document",
            max_retries=0,
            backoff_base_s=0.0,
        )
    sent = respx.calls.last.request
    assert b'name="document"' in sent.content
    assert b'name="file"' not in sent.content


def test_redact_url_masks_a_telegram_bot_token() -> None:
    url = "https://api.telegram.org/bot123456789:AAEfake-token-value/sendMessage"
    redacted = redact_url(url)
    assert "123456789:AAEfake-token-value" not in redacted
    assert redacted == "https://api.telegram.org/bot<redacted>/sendMessage"


def test_redact_url_leaves_a_token_free_url_unchanged() -> None:
    url = "https://graph.facebook.com/v21.0/123/messages"
    assert redact_url(url) == url


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
