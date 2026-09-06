"""Geocoding result models (CLAUDE.md §2 step 2, §10, §26.1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.utils.geo import is_valid_lat_lon


class _PlaceCore(BaseModel):
    """Fields shared by a candidate and the resolved place."""

    model_config = ConfigDict(extra="forbid")

    display_name: str
    latitude: float
    longitude: float

    # Administrative hierarchy, best-effort from the geocoder. Any may be None.
    country: str | None = None
    state: str | None = None
    district: str | None = None
    block: str | None = None  # sub-district / tehsil / taluk / block
    village: str | None = None  # village / town / suburb / hamlet

    place_rank: int | None = None
    importance: float | None = None
    osm_type: str | None = None  # "node" | "way" | "relation"
    osm_id: int | None = None
    source: str = "nominatim"

    def has_valid_coordinates(self) -> bool:
        return is_valid_lat_lon(self.latitude, self.longitude)


class PlaceCandidate(_PlaceCore):
    """One plausible match for a location query."""

    raw: dict = Field(default_factory=dict, repr=False)


class ResolvedPlace(_PlaceCore):
    """The place chosen for a discovery run, plus any rejected alternates.

    ``alternates`` is non-empty when the query was ambiguous but the caller (or a
    disambiguation rule) still selected one; it preserves what was set aside so
    the choice stays inspectable.
    """

    query: str
    alternates: list[PlaceCandidate] = Field(default_factory=list)

    @classmethod
    def from_candidate(
        cls, query: str, candidate: PlaceCandidate, alternates: list[PlaceCandidate] | None = None
    ) -> ResolvedPlace:
        return cls(
            query=query,
            alternates=alternates or [],
            **candidate.model_dump(exclude={"raw"}),
        )
