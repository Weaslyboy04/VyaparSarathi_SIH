"""Place disambiguation (CLAUDE.md §2 step 2, §26.1)."""

from __future__ import annotations

import pytest

from vyaparsarathi.errors import GeocodingError, LocationAmbiguousError
from vyaparsarathi.geocoding.resolve import resolve_place
from vyaparsarathi.models.place import PlaceCandidate


def _cand(lat: float, lon: float, importance: float = 0.3, name: str = "Place") -> PlaceCandidate:
    return PlaceCandidate(display_name=name, latitude=lat, longitude=lon, importance=importance)


def test_no_candidates_raises_geocoding_error() -> None:
    with pytest.raises(GeocodingError):
        resolve_place("nowhere", [])


def test_single_candidate_resolves() -> None:
    resolved = resolve_place("Bhagwanpur", [_cand(25.75, 84.55)])
    assert (resolved.latitude, resolved.longitude) == (25.75, 84.55)
    assert resolved.alternates == []


def test_tightly_clustered_candidates_resolve_to_top_rank() -> None:
    candidates = [
        _cand(25.7500, 84.5500, importance=0.30, name="A"),
        _cand(25.7505, 84.5505, importance=0.42, name="B"),  # ~70 m away, higher importance
    ]
    resolved = resolve_place("Bhagwanpur", candidates)
    assert resolved.display_name == "B"  # ranked by importance
    assert len(resolved.alternates) == 1
    assert resolved.alternates[0].display_name == "A"


def test_widely_separated_candidates_are_ambiguous() -> None:
    candidates = [
        _cand(25.7500, 84.5500, name="Bhagwanpur, Vaishali"),
        _cand(25.2100, 84.9800, name="Bhagwanpur, Buxar"),  # ~75 km away
    ]
    with pytest.raises(LocationAmbiguousError) as exc:
        resolve_place("Bhagwanpur, Bihar", candidates)
    assert len(exc.value.candidates) == 2


def test_ambiguity_if_any_candidate_is_far_even_with_a_cluster() -> None:
    candidates = [
        _cand(25.7500, 84.5500, importance=0.5),
        _cand(25.7505, 84.5505, importance=0.4),
        _cand(25.2100, 84.9800, importance=0.3),  # the outlier forces ambiguity
    ]
    with pytest.raises(LocationAmbiguousError):
        resolve_place("Bhagwanpur", candidates)
