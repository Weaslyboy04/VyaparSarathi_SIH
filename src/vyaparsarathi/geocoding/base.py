"""Geocoder protocol (CLAUDE.md §4 — "another geocoder can replace it later")."""

from __future__ import annotations

from typing import Protocol

from vyaparsarathi.models.place import PlaceCandidate


class Geocoder(Protocol):
    """Resolve free-text location to zero or more ranked candidates.

    Implementations must not pick a winner or raise on "no match" — they return
    what the provider gave, best-ranked first. Disambiguation is a separate,
    testable step (:func:`vyaparsarathi.geocoding.resolve.resolve_place`).
    """

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]: ...
