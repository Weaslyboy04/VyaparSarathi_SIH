"""OSM-specific raw shapes. These stay inside the adapter package (CLAUDE.md §7)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RawOsmElement(BaseModel):
    """One Overpass element, coordinate already resolved.

    For ``node`` the coordinate is the node's own ``lat``/``lon``; for ``way`` /
    ``relation`` it is the ``center`` produced by ``out center`` (CLAUDE.md §6.1).
    """

    model_config = ConfigDict(extra="forbid")

    element_type: str  # "node" | "way" | "relation"
    element_id: int
    latitude: float | None
    longitude: float | None
    tags: dict[str, str] = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict, repr=False)

    @property
    def source_id(self) -> str:
        return f"{self.element_type}/{self.element_id}"

    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None
