"""Shared test fixtures. No test in this suite touches the network (CLAUDE.md §28)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vyaparsarathi.config import Settings

FIXTURES = Path(__file__).parent / "fixtures"
FROZEN_NOW = datetime(2024, 5, 1, 12, 0, 0, tzinfo=UTC)


def load_fixture(*parts: str) -> object:
    return json.loads((FIXTURES.joinpath(*parts)).read_text(encoding="utf-8"))


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def frozen_now() -> datetime:
    return FROZEN_NOW


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Deterministic settings: caching off, tiny backoff, stable endpoints."""
    return Settings(
        overpass_url="https://overpass.test/api/interpreter",
        overpass_mirrors=["https://overpass-mirror.test/api/interpreter"],
        nominatim_url="https://nominatim.test",
        agmarknet_base_url="https://agmarknet.test/resource/test-resource-id",
        agmarknet_api_key="test-agmarknet-key",
        nominatim_min_interval_s=0.0,
        http_timeout_s=5.0,
        http_max_retries=2,
        http_backoff_base_s=0.0,
        user_agent="VyaparSarathi-tests/0.1 (contact: test@example.org)",
        cache_enabled=False,
        cache_dir=str(tmp_path / "cache"),
        max_radius_m=25_000,
        db_url=f"sqlite:///{tmp_path / 'test.sqlite3'}",
        log_level="WARNING",
        llm_enabled=False,
        llm_base_url="https://llm.test/v1/complete",
        llm_model="test-model",
        llm_timeout_s=5.0,
        llm_max_retries=2,
        llm_backoff_base_s=0.0,
    )


@pytest.fixture
def no_sleep() -> Iterator[list[float]]:
    """Collects sleep durations instead of sleeping."""
    calls: list[float] = []
    yield calls
