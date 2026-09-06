"""Geographic math (CLAUDE.md §4.2, §10).

WGS84 decimal degrees, ``latitude`` then ``longitude``. Distances are metres.
MVP uses a haversine great-circle distance with the WGS84 mean Earth radius.
A projected / geodesic method or PostGIS ``ST_Distance`` can replace this later
without changing call sites.
"""

from __future__ import annotations

import math

# WGS84 mean radius, metres (CLAUDE.md §4.2).
EARTH_RADIUS_M = 6_371_008.8


def haversine_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    """Great-circle distance in metres between two WGS84 points."""
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = math.sin(d_phi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2.0) ** 2
    c = 2.0 * math.asin(min(1.0, math.sqrt(a)))
    return EARTH_RADIUS_M * c


def is_valid_lat_lon(latitude: float | None, longitude: float | None) -> bool:
    """True when both values are present and within valid WGS84 ranges."""
    if latitude is None or longitude is None:
        return False
    if math.isnan(latitude) or math.isnan(longitude):
        return False
    return -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0
