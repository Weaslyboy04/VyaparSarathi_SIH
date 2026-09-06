"""Haversine + coordinate validation (CLAUDE.md §4.2, §10)."""

from __future__ import annotations

import math

import pytest

from vyaparsarathi.utils.geo import EARTH_RADIUS_M, haversine_m, is_valid_lat_lon


def test_zero_distance_to_self() -> None:
    assert haversine_m(25.75, 84.55, 25.75, 84.55) == pytest.approx(0.0, abs=1e-6)


def test_one_degree_of_latitude_is_about_111km() -> None:
    # A degree of latitude is ~111.2 km everywhere.
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert d == pytest.approx(111_195.0, rel=0.001)


def test_known_city_pair_delhi_to_mumbai() -> None:
    # Straight-line Delhi (28.6139, 77.2090) -> Mumbai (19.0760, 72.8777) ~ 1150 km.
    d = haversine_m(28.6139, 77.2090, 19.0760, 72.8777)
    assert d == pytest.approx(1_150_000.0, rel=0.02)


def test_symmetry() -> None:
    a = haversine_m(25.75, 84.55, 25.20, 84.98)
    b = haversine_m(25.20, 84.98, 25.75, 84.55)
    assert a == pytest.approx(b, rel=1e-12)


def test_antipodal_is_half_circumference() -> None:
    d = haversine_m(0.0, 0.0, 0.0, 180.0)
    assert d == pytest.approx(math.pi * EARTH_RADIUS_M, rel=1e-9)


def test_small_offset_matches_local_planar_estimate() -> None:
    # ~30 m north of the query point.
    d = haversine_m(25.75000, 84.55000, 25.75027, 84.55000)
    assert d == pytest.approx(30.0, abs=1.0)


@pytest.mark.parametrize(
    ("lat", "lon", "ok"),
    [
        (25.75, 84.55, True),
        (-90.0, 180.0, True),
        (90.0, -180.0, True),
        (90.1, 0.0, False),
        (0.0, 181.0, False),
        (None, 10.0, False),
        (10.0, None, False),
        (float("nan"), 10.0, False),
    ],
)
def test_is_valid_lat_lon(lat: float | None, lon: float | None, ok: bool) -> None:
    assert is_valid_lat_lon(lat, lon) is ok
