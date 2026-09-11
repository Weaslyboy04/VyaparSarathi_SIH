"""AGMARKNET source adapter: state/district/commodity -> raw wholesale mandi
price records. Returns raw records + fetch statistics only; no normalization,
no aggregation (CLAUDE.md §6, §7). One query per commodity — the API's
filters match a single exact value, not a list.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.sources.agmarknet.client import AgmarknetClient
from vyaparsarathi.sources.agmarknet.models import RawAgmarknetRecord
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


class AgmarknetFetch(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    records: list[RawAgmarknetRecord] = Field(default_factory=list)
    raw_count: int = 0
    commodities_queried: tuple[str, ...] = ()


class AgmarknetSource:
    def __init__(
        self,
        settings: Settings | None = None,
        client: AgmarknetClient | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or AgmarknetClient(self._settings)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> AgmarknetSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def fetch(
        self,
        commodities: Sequence[str],
        state: str,
        district: str | None,
    ) -> AgmarknetFetch:
        filters: dict[str, str] = {"state.keyword": state}
        if district:
            filters["district"] = district

        all_raw: list[dict] = []
        for commodity in commodities:
            per_commodity_filters = {**filters, "commodity": commodity}
            raw_records = self._client.run(
                per_commodity_filters, limit=self._settings.agmarknet_result_limit
            )
            all_raw.extend(raw_records)

        parsed: list[RawAgmarknetRecord] = []
        for item in all_raw:
            try:
                parsed.append(RawAgmarknetRecord.model_validate({**item, "raw": item}))
            except Exception:  # noqa: BLE001 - a malformed row is dropped, never fatal
                logger.warning("dropped an unparseable AGMARKNET record")
                continue

        return AgmarknetFetch(
            records=parsed,
            raw_count=len(all_raw),
            commodities_queried=tuple(commodities),
        )
