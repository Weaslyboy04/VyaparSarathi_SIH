"""HTTP request helper with bounded retry + exponential backoff (CLAUDE.md §4.1, §6.1).

Only *transient* failures are retried: HTTP 429, HTTP 5xx, and connect/read
timeouts or connection errors. Any other 4xx is returned to the caller
immediately (retrying would not help). ``Retry-After`` is honoured when present.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from vyaparsarathi.errors import HttpError
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

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

    for attempt in range(1, attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
        except _RETRYABLE_EXC as exc:
            last_exc = exc
            logger.warning(
                "HTTP %s %s failed (attempt %d/%d): %s",
                method,
                url,
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
                url,
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
            f"{method} {url} failed after {attempts} attempts: {last_exc}"
        ) from last_exc
    # All attempts returned a retryable status code.
    raise HttpError(f"{method} {url} returned a retryable status on all {attempts} attempts")


def build_client(
    user_agent: str, timeout_s: float, headers: Mapping[str, str] | None = None
) -> httpx.Client:
    """Construct a configured sync client. Caller is responsible for closing it."""
    merged = {"User-Agent": user_agent, "Accept": "application/json"}
    if headers:
        merged.update(headers)
    return httpx.Client(timeout=timeout_s, headers=merged, follow_redirects=True)
