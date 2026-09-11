"""AGMARKNET-specific raw shapes. These stay inside the adapter package
(CLAUDE.md §7)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RawAgmarknetRecord(BaseModel):
    """One data.gov.in AGMARKNET record, fields exactly as returned — plain
    strings, `arrival_date` as `DD/MM/YYYY`, prices as numeric-as-string.
    Parsing into real types (date, Decimal) happens downstream, never here
    (CLAUDE.md §7: source-specific shape must not leak past the adapter)."""

    model_config = ConfigDict(extra="forbid")

    state: str = ""
    district: str = ""
    market: str = ""
    commodity: str = ""
    variety: str = ""
    grade: str = ""
    arrival_date: str = ""
    min_price: str = ""
    max_price: str = ""
    modal_price: str = ""
    raw: dict = Field(default_factory=dict, repr=False)
