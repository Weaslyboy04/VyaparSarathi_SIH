"""The repository contract the discovery service depends on (CLAUDE.md §10)."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness
from vyaparsarathi.models.taxonomy import BusinessCategory


class BusinessRepository(Protocol):
    def save_business(self, business: NormalizedBusiness) -> None:
        """Insert or update a normalized business (keyed by ``internal_id``).

        Implementations also persist each ``provenance`` entry as its own source
        record so merges stay reversible and multi-source observations are kept.
        """
        ...

    def save_businesses(self, businesses: list[NormalizedBusiness]) -> None: ...

    def get_by_internal_id(self, internal_id: UUID) -> NormalizedBusiness | None: ...

    def businesses_by_category(self, category: BusinessCategory) -> list[NormalizedBusiness]: ...

    def businesses_near(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        category: BusinessCategory | None = None,
    ) -> list[BusinessHit]:
        """Businesses within ``radius_m`` of the point, nearest first."""
        ...

    def count(self) -> int: ...
