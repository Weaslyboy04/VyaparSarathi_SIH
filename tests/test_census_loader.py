"""Census 2011 village extract loader (Phase 2C). Offline: a small fixture CSV."""

from __future__ import annotations

import gzip
from pathlib import Path

from vyaparsarathi.sources.census.loader import CensusVillageSource
from vyaparsarathi.utils.geo import haversine_m

from .conftest import FIXTURES

_CSV = FIXTURES / "census" / "demo_villages.csv"

# Jadua's fixture coordinate; a 5 km radius covers the Test District cluster
# (the matched rows) but not Otherland.
_LAT, _LON = 25.7000, 85.2300


def _gz_copy(tmp_path: Path) -> Path:
    out = tmp_path / "villages.csv.gz"
    with gzip.open(out, "wt", encoding="utf-8", newline="") as handle:
        handle.write(_CSV.read_text(encoding="utf-8"))
    return out


def test_bbox_and_haversine_filter_to_radius() -> None:
    src = CensusVillageSource(path=_CSV)
    near = src.near(_LAT, _LON, 5_000, state="Testland")
    codes = [r.census_code for r in near.rows]
    assert "PC11-OTHER-0001" not in codes  # Otherland is ~1000 km away
    assert near.rows_in_radius == len(near.rows)
    assert near.covers_query_area is True
    # only geolocated (coordinate_status == "matched") rows enter near.rows
    assert all(r.has_usable_coordinate for r in near.rows)
    dists = [haversine_m(_LAT, _LON, r.latitude or 0.0, r.longitude or 0.0) for r in near.rows]
    assert dists == sorted(dists)  # rows come back nearest-first
    assert all(d <= 5_000 for d in dists)


def test_ungeolocated_population_in_area_is_counted_not_dropped() -> None:
    # PC11-TEST-0010 (ambiguous) and PC11-TEST-0011 (unmatched) are in Testland
    # with population but no coordinate: available-but-not-geo-matchable.
    src = CensusVillageSource(path=_CSV)
    near = src.near(_LAT, _LON, 5_000, state="Testland")
    assert near.population_rows_ungeolocated_in_area == 2
    assert all(r.coordinate_status == "matched" for r in near.rows)  # not mixed in


def test_tiny_radius_excludes_neighbours() -> None:
    src = CensusVillageSource(path=_CSV)
    near = src.near(_LAT, _LON, 50, state="Testland")
    # only rows essentially on top of the query point (the two PC11-TEST-0001 rows)
    assert {r.census_code for r in near.rows} == {"PC11-TEST-0001"}
    # but the state is still "covered" — ungeolocated population exists here
    assert near.covers_query_area is True


def test_duplicate_census_code_rows_are_both_returned() -> None:
    # the loader does not dedupe; the engine does (population-key uniqueness).
    src = CensusVillageSource(path=_CSV)
    near = src.near(_LAT, _LON, 5_000)
    dupes = [r for r in near.rows if r.census_code == "PC11-TEST-0001"]
    assert len(dupes) == 2


def test_households_only_row_parses_with_persons_none() -> None:
    src = CensusVillageSource(path=_CSV)
    near = src.near(_LAT, _LON, 5_000)
    row = next(r for r in near.rows if r.census_code == "PC11-TEST-0009")
    assert row.persons is None
    assert row.households == 250


def test_state_outside_extract_is_not_covered() -> None:
    src = CensusVillageSource(path=_CSV)
    near = src.near(19.0, 73.0, 5_000, state="Maharashtra")
    assert near.rows == []
    assert near.population_rows_ungeolocated_in_area == 0
    assert near.covers_query_area is False
    assert set(near.states_in_extract) == {"Testland", "Otherland", "ANDHRA PRADESH"}


def test_post_2011_state_alias_resolves_to_census_state() -> None:
    # Telangana was carved out of Andhra Pradesh in 2014; a modern geocode still
    # finds the 2011 census rows. The AP fixture rows have population, no coords.
    src = CensusVillageSource(path=_CSV)
    near = src.near(18.0, 78.0, 8_000, state="Telangana", district="Sangareddy")
    assert near.rows == []  # no coordinates for AP villages
    assert near.population_rows_ungeolocated_in_area == 2  # the two Medak rows
    assert near.covers_query_area is True


def test_missing_file_degrades_without_raising(tmp_path: Path) -> None:
    src = CensusVillageSource(path=tmp_path / "nope.csv.gz")
    near = src.near(_LAT, _LON, 5_000, state="Testland")
    assert near.file_present is False
    assert near.rows == []
    assert near.covers_query_area is False


def test_gzip_and_plain_csv_read_identically(tmp_path: Path) -> None:
    plain = CensusVillageSource(path=_CSV).near(_LAT, _LON, 5_000)
    gz = CensusVillageSource(path=_gz_copy(tmp_path)).near(_LAT, _LON, 5_000)
    assert [r.census_code for r in plain.rows] == [r.census_code for r in gz.rows]
    assert plain.population_rows_ungeolocated_in_area == gz.population_rows_ungeolocated_in_area


def test_header_only_file_yields_no_rows(tmp_path: Path) -> None:
    header = _CSV.read_text(encoding="utf-8").splitlines()[0] + "\n"
    path = tmp_path / "empty.csv"
    path.write_text(header, encoding="utf-8")
    near = CensusVillageSource(path=path).near(_LAT, _LON, 5_000, state="Testland")
    assert near.file_present is True
    assert near.rows == []
    assert near.rows_in_extract == 0
    assert near.covers_query_area is False


def test_wrong_schema_file_is_flagged_not_crashed(tmp_path: Path) -> None:
    path = tmp_path / "bad.csv"
    path.write_text("foo,bar\n1,2\n", encoding="utf-8")
    near = CensusVillageSource(path=path).near(_LAT, _LON, 5_000)
    assert near.file_present is True
    assert near.rows == []
    assert near.parse_errors >= 1


def test_invalid_numeric_population_row_is_skipped(tmp_path: Path) -> None:
    header = _CSV.read_text(encoding="utf-8").splitlines()[0]
    bad = (
        "PC11-BAD-1,Bad Pop,Testland,Test District,Test Block,not-a-number,10,"
        "25.70,85.23,matched,src,method,census_2011_pca_village,2011"
    )
    neg = (
        "PC11-BAD-2,Neg HH,Testland,Test District,Test Block,100,-5,"
        "25.70,85.23,matched,src,method,census_2011_pca_village,2011"
    )
    path = tmp_path / "mixed.csv"
    path.write_text(f"{header}\n{bad}\n{neg}\n", encoding="utf-8")
    near = CensusVillageSource(path=path).near(_LAT, _LON, 5_000)
    assert near.parse_errors == 2
    assert near.rows == []
