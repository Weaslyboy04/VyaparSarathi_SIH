"""The discovery query contract (CLAUDE.md §26.1).

Radius is stored in **metres** (CLAUDE.md §4.2). The CLI accepts kilometres and
converts at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.taxonomy import BusinessCategory


class DiscoveryQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_text: str = Field(min_length=1)
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    radius_m: int = Field(gt=0)
    category: BusinessCategory
