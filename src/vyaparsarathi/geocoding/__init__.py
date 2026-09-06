"""Geocoding: location text -> place candidates (CLAUDE.md §4, §10).

The :class:`Geocoder` protocol keeps the rest of the system independent of
Nominatim so another provider can replace it later.
"""

from vyaparsarathi.geocoding.base import Geocoder
from vyaparsarathi.geocoding.nominatim import NominatimGeocoder
from vyaparsarathi.geocoding.resolve import rank_candidates, resolve_place, select_candidate

__all__ = ["Geocoder", "NominatimGeocoder", "resolve_place", "select_candidate", "rank_candidates"]
