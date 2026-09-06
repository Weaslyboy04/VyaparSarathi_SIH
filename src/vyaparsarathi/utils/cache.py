"""Tiny on-disk JSON cache for outbound API responses (CLAUDE.md §6.1, §13).

Keyed by a caller-supplied string (which must already fold in the normalised
request parameters). Values are JSON-serialisable. Entries expire after
``ttl_s``. This keeps repeated demo runs and the test suite off the network.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class JsonFileCache:
    def __init__(self, directory: str | Path, ttl_s: int, enabled: bool = True) -> None:
        self.directory = Path(directory)
        self.ttl_s = ttl_s
        self.enabled = enabled
        if self.enabled:
            self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        path = self._path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if time.time() - payload.get("_cached_at", 0) > self.ttl_s:
            return None
        logger.debug("cache hit: %s", key)
        return payload.get("value")

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        path = self._path(key)
        record = {"_cached_at": time.time(), "_key": key, "value": value}
        try:
            path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # pragma: no cover - disk edge case
            logger.warning("cache write failed for %s: %s", key, exc)
