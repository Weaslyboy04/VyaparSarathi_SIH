"""OSM Nominatim geocoder (CLAUDE.md §4.1).

Honours the public-instance usage policy: a descriptive ``User-Agent``, at most
one request per ``min_interval_s`` seconds, and on-disk caching so repeated runs
and tests stay off the network. Transient failures are retried with backoff;
4xx (other than 429) is surfaced immediately.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable

import httpx

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.errors import GeocodingError, HttpError, SourcePayloadError
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.utils.cache import JsonFileCache
from vyaparsarathi.utils.http import build_client, request_with_retry
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_PUBLIC_NOMINATIM_URL = "https://nominatim.openstreetmap.org"
# Must match Settings.user_agent's shipped default exactly — used only to
# detect "still unconfigured" for the startup warning below, never compared
# for any behavioural branching.
_DEFAULT_USER_AGENT = (
    "VyaparSarathi/0.1 (SIH26091 hackathon prototype; "
    "no production contact configured -- set VYAPAR_USER_AGENT)"
)

# Nominatim `address` keys -> our admin fields, first present wins.
_DISTRICT_KEYS = ("state_district", "district", "county")
_BLOCK_KEYS = ("county", "subdistrict", "municipality", "city_district", "region")
_VILLAGE_KEYS = ("village", "hamlet", "town", "city", "suburb", "municipality")


def _first(address: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = address.get(key)
        if value:
            return value
    return None


class NominatimGeocoder:
    def __init__(
        self,
        settings: Settings | None = None,
        client: httpx.Client | None = None,
        cache: JsonFileCache | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or build_client(
            self._settings.user_agent, self._settings.http_timeout_s
        )
        self._owns_client = client is None
        self._cache = cache or JsonFileCache(
            self._settings.cache_dir,
            self._settings.cache_ttl_s,
            self._settings.cache_enabled,
        )
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._last_call_at: float | None = None

        if (
            self._settings.user_agent == _DEFAULT_USER_AGENT
            and self._settings.nominatim_url.rstrip("/") == _PUBLIC_NOMINATIM_URL
        ):
            logger.warning(
                "VYAPAR_USER_AGENT is unset (using the shipped placeholder default) while "
                "VYAPAR_NOMINATIM_URL points at the public Nominatim instance; its usage "
                "policy commonly rejects generic User-Agent identification with HTTP 403. "
                "Set VYAPAR_USER_AGENT in .env to a descriptive value with a real contact."
            )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> NominatimGeocoder:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- internal -------------------------------------------------------------

    def _throttle(self) -> None:
        min_interval = self._settings.nominatim_min_interval_s
        with self._lock:
            if self._last_call_at is not None:
                elapsed = self._monotonic() - self._last_call_at
                if elapsed < min_interval:
                    self._sleep(min_interval - elapsed)
            self._last_call_at = self._monotonic()

    def _cache_key(self, query: str, limit: int) -> str:
        return f"nominatim:v1:{self._settings.nominatim_url}:{limit}:{query.strip().lower()}"

    def _fetch(self, query: str, limit: int) -> list[dict]:
        url = f"{self._settings.nominatim_url.rstrip('/')}/search"
        params = {
            "q": query,
            "format": "jsonv2",
            "addressdetails": "1",
            "limit": str(limit),
            "accept-language": "en",
        }
        self._throttle()
        logger.info("geocoding %r via Nominatim (limit=%d)", query, limit)
        try:
            response = request_with_retry(
                self._client,
                "GET",
                url,
                params=params,
                max_retries=self._settings.http_max_retries,
                backoff_base_s=self._settings.http_backoff_base_s,
                sleep=self._sleep,
            )
        except HttpError as exc:
            raise GeocodingError(f"Nominatim request failed for {query!r}: {exc}") from exc

        if response.status_code == 403:
            raise GeocodingError(
                f"Nominatim returned HTTP 403 for {query!r} -- likely rejected by the public "
                "instance's usage policy (a generic/placeholder User-Agent, or the rate limit "
                "was exceeded). Set VYAPAR_USER_AGENT to a descriptive value with a real "
                "contact, or point VYAPAR_NOMINATIM_URL at a self-hosted/alternate instance."
            )
        if response.status_code >= 400:
            raise GeocodingError(f"Nominatim returned HTTP {response.status_code} for {query!r}")
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise SourcePayloadError(f"Nominatim sent non-JSON for {query!r}: {exc}") from exc
        if not isinstance(payload, list):
            raise SourcePayloadError(f"Nominatim sent an unexpected shape for {query!r}")
        return payload

    @staticmethod
    def _to_candidate(item: dict) -> PlaceCandidate | None:
        try:
            lat = float(item["lat"])
            lon = float(item["lon"])
        except (KeyError, TypeError, ValueError):
            return None
        address = item.get("address") or {}
        return PlaceCandidate(
            display_name=item.get("display_name") or item.get("name") or "(unnamed place)",
            latitude=lat,
            longitude=lon,
            country=address.get("country"),
            state=address.get("state") or address.get("region"),
            district=_first(address, _DISTRICT_KEYS),
            block=_first(address, _BLOCK_KEYS),
            village=_first(address, _VILLAGE_KEYS),
            place_rank=item.get("place_rank"),
            importance=item.get("importance"),
            osm_type=item.get("osm_type"),
            osm_id=item.get("osm_id"),
            source="nominatim",
            raw=item,
        )

    # -- public -------------------------------------------------------------

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        query = query.strip()
        if not query:
            raise GeocodingError("Empty location query")

        key = self._cache_key(query, limit)
        cached = self._cache.get(key)
        raw_items = cached if cached is not None else self._fetch(query, limit)
        if cached is None:
            self._cache.set(key, raw_items)

        candidates: list[PlaceCandidate] = []
        for item in raw_items:
            candidate = self._to_candidate(item)
            if candidate is not None and candidate.has_valid_coordinates():
                candidates.append(candidate)
        logger.info("geocoded %r -> %d candidate(s)", query, len(candidates))
        return candidates
