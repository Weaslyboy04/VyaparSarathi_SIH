"""In-memory repository — default for the CLI and unit tests."""

from __future__ import annotations

from uuid import UUID

from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.utils.geo import haversine_m


class InMemoryBusinessRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, NormalizedBusiness] = {}
        # (source, source_id) -> internal_id, mirrors the SQL source_records table.
        self._source_index: dict[tuple[str, str], UUID] = {}

    def save_business(self, business: NormalizedBusiness) -> None:
        self._by_id[business.internal_id] = business.model_copy(deep=True)
        for entry in business.provenance or []:
            self._source_index[(entry.source.value, entry.source_id)] = business.internal_id
        self._source_index[(business.source.value, business.source_id)] = business.internal_id

    def save_businesses(self, businesses: list[NormalizedBusiness]) -> None:
        for business in businesses:
            self.save_business(business)

    def get_by_internal_id(self, internal_id: UUID) -> NormalizedBusiness | None:
        found = self._by_id.get(internal_id)
        return found.model_copy(deep=True) if found else None

    def businesses_by_category(self, category: BusinessCategory) -> list[NormalizedBusiness]:
        return [b.model_copy(deep=True) for b in self._by_id.values() if b.category == category]

    def businesses_near(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        category: BusinessCategory | None = None,
    ) -> list[BusinessHit]:
        hits: list[BusinessHit] = []
        for business in self._by_id.values():
            if category is not None and business.category != category:
                continue
            distance = haversine_m(latitude, longitude, business.latitude, business.longitude)
            if distance <= radius_m:
                hits.append(
                    BusinessHit(business=business.model_copy(deep=True), distance_m=distance)
                )
        hits.sort(key=lambda h: h.distance_m)
        return hits

    def count(self) -> int:
        return len(self._by_id)

    def resolve_source(self, source: str, source_id: str) -> UUID | None:
        return self._source_index.get((source, source_id))
