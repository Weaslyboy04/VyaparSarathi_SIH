"""Read the normalized Census 2011 village extract and slice it by catchment.

The extract is a CSV (optionally gzipped) with one row per census village,
carrying official population + households and — where an offline geographic join
succeeded — a representative coordinate with its own provenance columns
(``coordinate_status`` / ``coordinate_source`` / ``coordinate_method``). Rows
whose coordinate could not be established reliably are **kept** with
``coordinate_status`` ``unmatched`` / ``ambiguous`` and no lat/lon; they are
usable for "population data exists in this area" but not for catchment geometry
(Phase 2C design; task requirement: never guess a coordinate).

Missing / short / malformed files degrade to an empty result with
``file_present`` set accordingly; they never raise.
"""

from __future__ import annotations

import csv
import gzip
import math
from collections.abc import Iterator
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.utils.geo import haversine_m, is_valid_lat_lon
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

# Same flat-earth approximation the SQL repository uses for its bbox prefilter.
_METRES_PER_DEG_LAT = 111_320.0

_REQUIRED_COLUMNS = frozenset(
    {
        "census_code",
        "name",
        "state",
        "district",
        "subdistrict",
        "persons",
        "households",
        "latitude",
        "longitude",
        "coordinate_status",
        "coordinate_source",
        "coordinate_method",
        "dataset",
        "reference_year",
    }
)

_GEOLOCATED = "matched"

# Columns where an empty string means "absent" and must become ``None`` before
# validation; every other column is a plain string and stays as-is.
_NULLABLE_COLUMNS = frozenset({"persons", "households", "latitude", "longitude"})


class CensusVillageRow(BaseModel):
    """One row of the extract. Population and coordinate are two different facts
    from two sources with two provenance trails (CLAUDE.md §23). ``latitude`` /
    ``longitude`` are ``None`` unless ``coordinate_status == "matched"``."""

    model_config = ConfigDict(extra="ignore")

    census_code: str
    name: str
    state: str
    district: str | None = None
    subdistrict: str | None = None

    persons: int | None = Field(default=None, ge=0)
    households: int | None = Field(default=None, ge=0)

    latitude: float | None = Field(default=None, ge=-90.0, le=90.0)
    longitude: float | None = Field(default=None, ge=-180.0, le=180.0)
    # "matched" | "unmatched" | "ambiguous"
    coordinate_status: str = "unmatched"
    coordinate_source: str = ""  # e.g. "datameet_indian_village_boundaries"
    coordinate_method: str = ""  # how the coordinate was attached, for audit

    dataset: str = "census_2011_pca_village"
    reference_year: int = 2011

    @property
    def has_usable_coordinate(self) -> bool:
        return (
            self.coordinate_status == _GEOLOCATED
            and self.latitude is not None
            and self.longitude is not None
            and is_valid_lat_lon(self.latitude, self.longitude)
        )


class CensusNearResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Geolocated rows whose representative point lies within the radius.
    rows: list[CensusVillageRow] = Field(default_factory=list)

    file_present: bool = False
    rows_in_extract: int = 0
    rows_in_radius: int = 0
    # Rows with population for the query's state/district that carry NO usable
    # coordinate — "population data available but not geographically matchable".
    population_rows_ungeolocated_in_area: int = 0
    states_in_extract: list[str] = Field(default_factory=list)
    covers_query_area: bool = False
    parse_errors: int = 0


def _open_text(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, mode="rt", encoding="utf-8", newline="") as handle:
            yield from handle
    else:
        with path.open(encoding="utf-8", newline="") as handle:
            yield from handle


def _norm(value: str | None) -> str:
    return (value or "").strip().lower()


# States created *after* the 2011 census, mapped to the 2011 census state name
# so a modern geocode still resolves to the right census rows. These are
# documented administrative facts, not guesses:
#   Telangana  -> carved out of Andhra Pradesh in 2014
#   Ladakh     -> split from Jammu & Kashmir in 2019
_STATE_ALIASES_2011: dict[str, str] = {
    "telangana": "andhra pradesh",
    "ladakh": "jammu & kashmir",
}


def _census_state_name(state_2011_norm: str) -> str:
    """Normalized query state -> the normalized 2011 census state name."""
    return _STATE_ALIASES_2011.get(state_2011_norm, state_2011_norm)


class CensusVillageSource:
    """Loads and slices the village extract. One instance per process is fine;
    the file is read once per run."""

    def __init__(self, path: str | Path | None = None, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self._path = Path(path if path is not None else s.census_villages_path)

    @property
    def path(self) -> Path:
        return self._path

    def near(
        self,
        latitude: float,
        longitude: float,
        radius_m: int,
        *,
        state: str | None = None,
        district: str | None = None,
    ) -> CensusNearResult:
        """Geolocated rows within ``radius_m`` of the point, plus a count of
        population rows in the same state/district that have no usable
        coordinate."""
        if not self._path.exists():
            logger.info("census extract not found at %s", self._path)
            return CensusNearResult(file_present=False)

        d_lat = radius_m / _METRES_PER_DEG_LAT
        cos_lat = max(math.cos(math.radians(latitude)), 1e-6)
        d_lon = radius_m / (_METRES_PER_DEG_LAT * cos_lat)

        state_n = _census_state_name(_norm(state))
        district_n = _norm(district)

        rows: list[CensusVillageRow] = []
        states: set[str] = set()
        total = 0
        parse_errors = 0
        ungeolocated_in_area = 0

        reader = csv.DictReader(_open_text(self._path))
        header = set(reader.fieldnames or [])
        if not _REQUIRED_COLUMNS.issubset(header):
            missing = ", ".join(sorted(_REQUIRED_COLUMNS - header))
            logger.warning("census extract %s missing columns: %s", self._path, missing)
            return CensusNearResult(file_present=True, parse_errors=1)

        for raw in reader:
            total += 1
            try:
                row = CensusVillageRow.model_validate(
                    {
                        k: (None if (k in _NULLABLE_COLUMNS and v == "") else v)
                        for k, v in raw.items()
                    }
                )
            except (ValueError, TypeError):
                parse_errors += 1
                continue
            if row.state:
                states.add(row.state.strip())

            if row.has_usable_coordinate:
                assert row.latitude is not None and row.longitude is not None
                if (
                    abs(row.latitude - latitude) <= d_lat
                    and abs(row.longitude - longitude) <= d_lon
                    and haversine_m(latitude, longitude, row.latitude, row.longitude) <= radius_m
                ):
                    rows.append(row)
                continue

            # No usable coordinate: does it belong to the queried area and carry
            # population? If so it is "available but not geo-matchable".
            in_area = (state_n and _norm(row.state) == state_n) or (
                district_n and _norm(row.district) == district_n
            )
            if in_area and row.persons is not None:
                ungeolocated_in_area += 1

        rows.sort(
            key=lambda r: haversine_m(latitude, longitude, r.latitude or 0.0, r.longitude or 0.0)
        )
        state_norms = {s.strip().lower() for s in states}
        covers = (
            bool(rows) or ungeolocated_in_area > 0 or (bool(state_n) and state_n in state_norms)
        )

        return CensusNearResult(
            rows=rows,
            file_present=True,
            rows_in_extract=total,
            rows_in_radius=len(rows),
            population_rows_ungeolocated_in_area=ungeolocated_in_area,
            states_in_extract=sorted(states),
            covers_query_area=covers,
            parse_errors=parse_errors,
        )
