"""data.gov.in AGMARKNET HTTP client (CLAUDE.md §6, §6.1's conventions
applied to a single canonical government API endpoint — there is no mirror
list here, unlike Overpass; data.gov.in has one endpoint).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, SourcePayloadError, SourceUnavailableError
from vyaparsarathi.utils.cache import JsonFileCache
from vyaparsarathi.utils.http import request_with_retry
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class AgmarknetClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        cache: JsonFileCache | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = httpx.Client(
                timeout=self._settings.http_timeout_s, follow_redirects=True
            )
            self._owns_client = True
        self._cache = cache or JsonFileCache(
            self._settings.cache_dir,
            self._settings.cache_ttl_s,
            self._settings.cache_enabled,
        )
        self._sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AgmarknetClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run(self, filters: dict[str, str], *, limit: int) -> list[dict[str, Any]]:
        """Query the configured AGMARKNET resource with the given
        `filters[<field>]` values. Returns the raw ``records`` list.

        Raises :class:`SourceUnavailableError` when the API key is not
        configured, the endpoint could not be reached after retries, or it
        returned a non-2xx status. Raises :class:`SourcePayloadError` when it
        returned 2xx but the body is not the expected
        ``{"records": [...]}`` shape — never fabricates a result either way.
        """
        api_key = self._settings.agmarknet_api_key
        if api_key is None:
            raise SourceUnavailableError("AGMARKNET is not configured (no API key set)")

        params: dict[str, Any] = {
            "api-key": api_key.get_secret_value(),
            "format": "json",
            "offset": 0,
            "limit": limit,
        }
        for field, value in filters.items():
            params[f"filters[{field}]"] = value

        # The key is deliberately excluded from the cache key string — it is
        # a request credential, not part of what makes two queries distinct.
        cache_key = "agmarknet:v1:" + ",".join(
            f"{k}={v}" for k, v in sorted(params.items()) if k != "api-key"
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        retry_kwargs: dict[str, Any] = {
            "max_retries": self._settings.http_max_retries,
            "backoff_base_s": self._settings.http_backoff_base_s,
        }
        if self._sleep is not None:
            retry_kwargs["sleep"] = self._sleep

        try:
            response = request_with_retry(
                self._client,
                "GET",
                self._settings.agmarknet_base_url,
                params=params,
                **retry_kwargs,
            )
        except HttpError as exc:
            raise SourceUnavailableError(f"AGMARKNET unreachable: {exc}") from exc

        if response.status_code >= 400:
            logger.warning("AGMARKNET returned HTTP %d", response.status_code)
            raise SourceUnavailableError(f"AGMARKNET returned HTTP {response.status_code}")

        try:
            payload = response.json()
            records = payload["records"]
        except (ValueError, KeyError, TypeError) as exc:
            raise SourcePayloadError(f"AGMARKNET sent an unparseable body: {exc}") from exc

        if not isinstance(records, list):
            raise SourcePayloadError("AGMARKNET 'records' is not a list")

        self._cache.set(cache_key, records)
        return records
