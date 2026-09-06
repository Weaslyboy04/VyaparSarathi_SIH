"""Overpass QL builder + HTTP client with mirror fallback (CLAUDE.md §6.1)."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import HttpError, SourceUnavailableError
from vyaparsarathi.utils.cache import JsonFileCache
from vyaparsarathi.utils.http import request_with_retry
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


def build_overpass_ql(
    selectors: Sequence[tuple[str, str]],
    latitude: float,
    longitude: float,
    radius_m: int,
    timeout_s: int,
) -> str:
    """Assemble an Overpass QL query.

    ``nwr`` matches nodes, ways and relations; ``out center tags`` returns tags
    plus a representative point for ways/relations (CLAUDE.md §6.1).
    """
    if not selectors:
        raise ValueError("At least one tag selector is required for an Overpass query")
    lines = [f"[out:json][timeout:{timeout_s}];", "("]
    for key, value in selectors:
        k = key.replace('"', '\\"')
        v = value.replace('"', '\\"')
        lines.append(f'  nwr["{k}"="{v}"](around:{radius_m},{latitude:.7f},{longitude:.7f});')
    lines.append(");")
    lines.append("out center tags;")
    return "\n".join(lines)


class OverpassClient:
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
                timeout=self._settings.http_timeout_s,
                headers={"User-Agent": self._settings.user_agent},
                follow_redirects=True,
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

    def __enter__(self) -> OverpassClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run(self, ql: str) -> tuple[list[dict], str, bool]:
        """Execute ``ql``. Return ``(elements, endpoint_used, mirror_fallback_used)``.

        Tries the primary endpoint, then each mirror, on transient failure or an
        unparseable body. Raises :class:`SourceUnavailableError` only when every
        endpoint fails.
        """
        cache_key = f"overpass:v1:{ql}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached, "cache", False

        endpoints = self._settings.overpass_endpoints
        errors: list[str] = []
        retry_kwargs: dict[str, Any] = {
            "max_retries": self._settings.http_max_retries,
            "backoff_base_s": self._settings.http_backoff_base_s,
        }
        if self._sleep is not None:
            retry_kwargs["sleep"] = self._sleep

        for index, endpoint in enumerate(endpoints):
            try:
                response = request_with_retry(
                    self._client, "POST", endpoint, data={"data": ql}, **retry_kwargs
                )
            except HttpError as exc:
                errors.append(f"{endpoint}: {exc}")
                logger.warning("Overpass endpoint failed, will try next: %s", endpoint)
                continue

            if response.status_code >= 400:
                errors.append(f"{endpoint}: HTTP {response.status_code}")
                logger.warning("Overpass %s -> HTTP %d", endpoint, response.status_code)
                continue

            try:
                payload = response.json()
                elements = payload["elements"]
            except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
                errors.append(f"{endpoint}: bad payload ({exc})")
                logger.warning("Overpass %s sent an unparseable body", endpoint)
                continue

            if not isinstance(elements, list):
                errors.append(f"{endpoint}: 'elements' is not a list")
                continue

            fallback_used = index > 0
            if fallback_used:
                logger.warning("Overpass mirror fallback used: %s", endpoint)
            self._cache.set(cache_key, elements)
            return elements, endpoint, fallback_used

        raise SourceUnavailableError("All Overpass endpoints failed: " + " | ".join(errors))
