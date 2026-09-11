"""HTTP request helper with bounded retry + exponential backoff (CLAUDE.md §4.1, §6.1).

Only *transient* failures are retried: HTTP 429, HTTP 5xx, and connect/read
timeouts or connection errors. Any other 4xx is returned to the caller
immediately (retrying would not help). ``Retry-After`` is honoured when present.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from vyaparsarathi.errors import HttpError
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

# Telegram's Bot API has no header-based auth — the bot token is a URL path
# segment (`/bot<token>/method`), unlike every other provider this codebase
# talks to. Redact it before a URL ever reaches a log line or an exception
# message (CLAUDE.md §24: never print secrets in logs/errors).
_TELEGRAM_TOKEN_RE = re.compile(r"(/bot)\d+:[A-Za-z0-9_-]+")


def redact_url(url: str) -> str:
    """Public alias of the redaction this module applies to its own log/
    error text — for a caller (e.g. `scripts/telegram_bot_server.py`) that
    catches an `httpx` exception directly and must sanitize its own message
    before printing/logging it."""
    return _TELEGRAM_TOKEN_RE.sub(r"\1<redacted>", url)


_redact = redact_url


_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_RETRYABLE_EXC = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None  # HTTP-date form is ignored for the MVP


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    max_retries: int,
    backoff_base_s: float,
    sleep: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> httpx.Response:
    """Perform ``client.request(method, url, **kwargs)`` with retry/backoff.

    Returns the final :class:`httpx.Response` (which may still carry a non-2xx
    status the caller chose not to treat as retryable). Raises :class:`HttpError`
    only when every attempt failed with a transient error.
    """
    attempts = max_retries + 1
    last_exc: Exception | None = None
    safe_url = _redact(url)

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except _RETRYABLE_EXC as exc:
            last_exc = exc
            logger.warning(
                "HTTP %s %s failed (attempt %d/%d): %s",
                method,
                safe_url,
                attempt,
                attempts,
                exc.__class__.__name__,
            )
        else:
            if response.status_code not in _RETRYABLE_STATUS:
                return response
            last_exc = None
            logger.warning(
                "HTTP %s %s -> %d (attempt %d/%d)",
                method,
                safe_url,
                response.status_code,
                attempt,
                attempts,
            )
            if attempt < attempts:
                wait = _retry_after_seconds(response) or backoff_base_s * (2 ** (attempt - 1))
                sleep(wait)
            continue

        if attempt < attempts:
            sleep(backoff_base_s * (2 ** (attempt - 1)))

    if last_exc is not None:
        raise HttpError(
            f"{method} {safe_url} failed after {attempts} attempts: {last_exc}"
        ) from last_exc
    # All attempts returned a retryable status code.
    raise HttpError(f"{method} {safe_url} returned a retryable status on all {attempts} attempts")


def build_client(
    user_agent: str, timeout_s: float, headers: Mapping[str, str] | None = None
) -> httpx.Client:
    """Construct a configured sync client. Caller is responsible for closing it."""
    merged = {"User-Agent": user_agent, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return httpx.Client(timeout=timeout_s, headers=merged, follow_redirects=True)


def post_json(
    client: httpx.Client,
    url: str,
    *,
    json: Mapping[str, Any],
    headers: Mapping[str, str] | None = None,
    max_retries: int,
    backoff_base_s: float,
) -> dict[str, Any]:
    """POST a JSON body with the same retry/backoff policy as
    :func:`request_with_retry`, raising :class:`HttpError` on a non-2xx
    response (never returning a caller-visible partial/garbage result). Used
    by any provider adapter (e.g. Meta's Cloud API) that needs a plain
    "send JSON, get JSON back" call — never a bespoke per-provider HTTP stack
    (CLAUDE.md §4.1)."""
    response = request_with_retry(
        client,
        "POST",
        url,
        json=dict(json),
        headers=dict(headers) if headers else None,
        max_retries=max_retries,
        backoff_base_s=backoff_base_s,
    )
    if response.status_code >= 400:
        raise HttpError(
            f"POST {_redact(url)} returned {response.status_code}: {response.text[:500]}"
        )
    body: dict[str, Any] = response.json()
    return body


def post_multipart(
    client: httpx.Client,
    url: str,
    *,
    file_bytes: bytes,
    filename: str,
    mime_type: str,
    field_name: str = "file",
    data: Mapping[str, str] | None = None,
    max_retries: int,
    backoff_base_s: float,
) -> dict[str, Any]:
    """POST a `multipart/form-data` file upload (e.g. Meta Cloud API's media
    upload endpoint, which expects `field_name="file"` — the default) with
    the same retry/backoff policy, raising :class:`HttpError` on a non-2xx
    response. `data` carries any additional plain form fields the provider
    requires alongside the file. Not every provider agrees on the field
    name: Telegram's `sendDocument` specifically requires `"document"`, not
    `"file"` — a real incident (rejected with "there is no document in the
    request") caught by a hardcoded field name here on the first live
    Telegram DPR send."""
    response = request_with_retry(
        client,
        "POST",
        url,
        files={field_name: (filename, file_bytes, mime_type)},
        data=dict(data) if data else None,
        max_retries=max_retries,
        backoff_base_s=backoff_base_s,
    )
    if response.status_code >= 400:
        raise HttpError(
            f"POST {_redact(url)} returned {response.status_code}: {response.text[:500]}"
        )
    body: dict[str, Any] = response.json()
    return body
