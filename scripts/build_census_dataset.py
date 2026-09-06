"""One-off ETL: official Census of India 2011 village data -> the normalized
extract Phase 2C reads (``data/demand/census2011_villages.csv.gz``).

Not imported by the package; runs no part of the request path. It exists so the
reference data is reproducible and auditable (CLAUDE.md §23, §33).

Inputs (download once; see ``data/demand/SOURCES.md`` for exact provenance):

* ``--pca-xlsx``  the official RGI workbook
  "2011-IndiaStateDistSbDistVill-0000.xlsx" (Basic Population Figures,
  India/State/District/Sub-District/Village). Streamed with the stdlib — no
  pandas/openpyxl. Alternatively pass ``--villages-csv`` + ``--hierarchy-csv``
  if you have already extracted them (this script can also emit them, see
  ``--dump-intermediate``).
* ``--boundaries``  a directory of datameet ``indian_village_boundaries``
  ``<state>.geojson`` files. Optional: without it, no coordinates are attached
  and every row is written with ``coordinate_status=unmatched``.

Geographic linkage: an **offline** join of two census-derived datasets on
``(state, district, sub-district, normalized village name)``, keeping only
buckets with exactly one boundary match. Zero matches -> ``unmatched``; two or
more -> ``ambiguous``. Coordinates are never guessed and never taken from a
name match that is not unique within its sub-district.

    python scripts/build_census_dataset.py \\
        --pca-xlsx 2011-IndiaStateDistSbDistVill-0000.xlsx \\
        --boundaries datameet_indian_village_boundaries/ \\
        --out data/demand/census2011_villages.csv.gz
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from pathlib import Path

OUT_COLUMNS = [
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
]

DATASET = "census_2011_pca_village"
REFERENCE_YEAR = "2011"
COORD_SOURCE = "datameet_indian_village_boundaries"
COORD_METHOD = (
    "offline join on (state, district, sub-district, normalized village name), "
    "unique-within-sub-district only; point = mean of polygon exterior ring"
)

_XLSX_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_PCA_HEADER = [
    "State",
    "District",
    "Subdistt",
    "Town_Village",
    "Ward",
    "EB",
    "Level",
    "Name",
    "TRU",
    "No_HH",
    "TOT_P",
]
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def norm(text: str | None) -> str:
    """Lowercase, strip punctuation, collapse whitespace. The census files are
    Latin-script, so no transliteration is needed here."""
    if not text:
        return ""
    t = _PUNCT_RE.sub(" ", text.lower())
    return _WS_RE.sub(" ", t).strip()


# --- read the official xlsx (streaming, stdlib only) ---------------------


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n - 1


def stream_pca_rows(xlsx_path: Path) -> Iterator[list[str]]:
    """Yield every row of sheet1 as a list aligned to ``_PCA_HEADER``."""
    zf = zipfile.ZipFile(xlsx_path)
    shared: list[str] = []
    with zf.open("xl/sharedStrings.xml") as f:
        for _ev, el in ET.iterparse(f):
            if el.tag == _XLSX_NS + "si":
                shared.append("".join(t.text or "" for t in el.iter(_XLSX_NS + "t")))
                el.clear()
    with zf.open("xl/worksheets/sheet1.xml") as f:
        for _ev, el in ET.iterparse(f):
            if el.tag != _XLSX_NS + "row":
                continue
            cells = [""] * len(_PCA_HEADER)
            for c in el.findall(_XLSX_NS + "c"):
                i = _col_index(c.get("r", ""))
                if i >= len(_PCA_HEADER):
                    continue
                v = c.find(_XLSX_NS + "v")
                if v is not None and v.text is not None:
                    cells[i] = shared[int(v.text)] if c.get("t") == "s" else v.text
                else:
                    isel = c.find(_XLSX_NS + "is")
                    if isel is not None:
                        cells[i] = "".join(t.text or "" for t in isel.iter(_XLSX_NS + "t"))
            el.clear()
            yield cells


def _csv_rows(path: Path) -> Iterator[dict[str, str]]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, mode="rt", encoding="utf-8-sig", newline="") as handle:  # type: ignore[operator]
        yield from csv.DictReader(handle)


# --- village + hierarchy extraction ------------------------------------


def load_villages_and_hierarchy(
    *,
    xlsx: Path | None,
    villages_csv: Path | None,
    hierarchy_csv: Path | None,
) -> tuple[list[dict[str, str]], dict[tuple[str, str, str], str]]:
    """Return (village rows, {(state_code, district_code, subdistt_code): name}).

    ``(state_code, '000', '00000')`` -> state name; ``(sc, dc, '00000')`` ->
    district name; full triple -> sub-district name.
    """
    villages: list[dict[str, str]] = []
    hierarchy: dict[tuple[str, str, str], str] = {}

    if xlsx is not None:
        for cells in stream_pca_rows(xlsx):
            row = dict(zip(_PCA_HEADER, cells, strict=False))
            level, tru = row["Level"], row["TRU"]
            key = (row["State"], row["District"], row["Subdistt"])
            if level in {"STATE", "DISTRICT", "SUB-DISTRICT"} and tru == "Total":
                hierarchy[key] = row["Name"]
            elif level == "VILLAGE":
                villages.append(row)
        return villages, hierarchy

    assert villages_csv is not None and hierarchy_csv is not None
    for r in _csv_rows(hierarchy_csv):
        hierarchy[(r["State"], r["District"], r["Subdistt"])] = r["Name"]
    for r in _csv_rows(villages_csv):
        villages.append(r)
    return villages, hierarchy


def dump_intermediate(villages: list[dict[str, str]], hierarchy: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "pca_villages.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_PCA_HEADER)
        w.writeheader()
        for v in villages:
            w.writerow({k: v.get(k, "") for k in _PCA_HEADER})
    with (out_dir / "pca_hierarchy.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["State", "District", "Subdistt", "Name"])
        for (s, d, sd), name in sorted(hierarchy.items()):
            w.writerow([s, d, sd, name])


# --- boundary centroids ----------------------------------------------

_AMBIGUOUS = object()


def _representative_point(geom: dict) -> tuple[float, float] | None:
    if geom.get("type") == "Polygon":
        rings = geom.get("coordinates") or []
    elif geom.get("type") == "MultiPolygon":
        polys = geom.get("coordinates") or []
        rings = max(polys, key=lambda p: len(p[0]) if p and p[0] else 0, default=None)
        rings = rings or []
    else:
        return None
    if not rings or not rings[0]:
        return None
    ring = rings[0]
    xs = [c[0] for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
    ys = [c[1] for c in ring if isinstance(c, (list, tuple)) and len(c) >= 2]
    if not xs or not ys:
        return None
    lon, lat = sum(xs) / len(xs), sum(ys) / len(ys)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        return None
    return round(lat, 6), round(lon, 6)


def load_boundary_points(
    boundaries_dir: Path,
) -> tuple[dict[tuple[str, str, str, str], object], dict[str, int]]:
    """{(state, district, subdist, name) normalized -> (lat, lon) | _AMBIGUOUS}."""
    index: dict[tuple[str, str, str, str], object] = {}
    stats = {"features": 0, "usable_points": 0, "ambiguous_keys": 0}
    for gj in sorted(boundaries_dir.glob("*.geojson")):
        data = json.loads(gj.read_text(encoding="utf-8"))
        for feat in data.get("features", []):
            stats["features"] += 1
            props = feat.get("properties") or {}
            if str(props.get("TYPE", "Village")).lower() not in {"village", ""}:
                continue
            pt = _representative_point(feat.get("geometry") or {})
            if pt is None:
                continue
            key = (
                norm(props.get("STATE")),
                norm(props.get("DISTRICT")),
                norm(props.get("SUB_DIST")),
                norm(props.get("NAME")),
            )
            if key in index:
                if index[key] is not _AMBIGUOUS:
                    stats["ambiguous_keys"] += 1
                index[key] = _AMBIGUOUS
            else:
                index[key] = pt
                stats["usable_points"] += 1
    return index, stats


# --- validation ----------------------------------------------------


def _clean_int(value: str | None) -> str:
    """Return a non-negative integer string, or '' for missing/invalid."""
    if value is None:
        return ""
    s = str(value).strip().replace(",", "")
    if s == "" or not s.lstrip("-").isdigit():
        return ""
    n = int(s)
    return str(n) if n >= 0 else ""


# --- main --------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build_census_dataset", description=__doc__)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--pca-xlsx", type=Path, help="official RGI village workbook (.xlsx)")
    src.add_argument("--villages-csv", type=Path, help="pre-extracted VILLAGE rows CSV")
    p.add_argument(
        "--hierarchy-csv", type=Path, help="pre-extracted STATE/DISTRICT/SUB-DISTRICT names"
    )
    p.add_argument("--boundaries", type=Path, help="dir of datameet <state>.geojson files")
    p.add_argument("--out", required=True, type=Path, help="output .csv.gz")
    p.add_argument(
        "--dump-intermediate",
        type=Path,
        help="also write pca_villages.csv / pca_hierarchy.csv here",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    if args.villages_csv and not args.hierarchy_csv:
        print("error: --villages-csv requires --hierarchy-csv", file=sys.stderr)
        return 2

    villages, hierarchy = load_villages_and_hierarchy(
        xlsx=args.pca_xlsx, villages_csv=args.villages_csv, hierarchy_csv=args.hierarchy_csv
    )
    print(f"villages: {len(villages)}   hierarchy entries: {len(hierarchy)}", file=sys.stderr)
    if args.dump_intermediate:
        dump_intermediate(villages, hierarchy, args.dump_intermediate)

    points: dict[tuple[str, str, str, str], object] = {}
    if args.boundaries:
        points, gstats = load_boundary_points(args.boundaries)
        print(
            f"boundary features: {gstats['features']}   unique points: {gstats['usable_points']}   "
            f"ambiguous keys: {gstats['ambiguous_keys']}",
            file=sys.stderr,
        )

    seen_codes: set[str] = set()
    out_rows: list[dict[str, str]] = []
    counts = {
        "written": 0,
        "dropped_no_code": 0,
        "dropped_dup_code": 0,
        "matched": 0,
        "unmatched": 0,
        "ambiguous": 0,
        "invalid_persons": 0,
        "invalid_households": 0,
    }

    for v in villages:
        sc, dc, sdc, tvc = v["State"], v["District"], v["Subdistt"], v["Town_Village"]
        code = f"{sc}{dc}{sdc}{tvc}"
        if not sc or not tvc or tvc.strip("0") == "":
            counts["dropped_no_code"] += 1
            continue
        if code in seen_codes:
            counts["dropped_dup_code"] += 1
            continue
        seen_codes.add(code)

        state_name = hierarchy.get((sc, "000", "00000"), "")
        district_name = hierarchy.get((sc, dc, "00000"), "")
        subdist_name = hierarchy.get((sc, dc, sdc), "")
        name = (v.get("Name") or "").strip()

        persons = _clean_int(v.get("TOT_P"))
        households = _clean_int(v.get("No_HH"))
        if v.get("TOT_P") not in (None, "", "0") and persons == "":
            counts["invalid_persons"] += 1
        if v.get("No_HH") not in (None, "", "0") and households == "":
            counts["invalid_households"] += 1

        lat = lon = ""
        status = "unmatched"
        if points:
            hit = points.get(
                (norm(state_name), norm(district_name), norm(subdist_name), norm(name))
            )
            if hit is _AMBIGUOUS:
                status = "ambiguous"
                counts["ambiguous"] += 1
            elif isinstance(hit, tuple):
                lat, lon = f"{hit[0]:.6f}", f"{hit[1]:.6f}"
                status = "matched"
                counts["matched"] += 1
            else:
                counts["unmatched"] += 1
        else:
            counts["unmatched"] += 1

        out_rows.append(
            {
                "census_code": code,
                "name": name,
                "state": state_name,
                "district": district_name,
                "subdistrict": subdist_name,
                "persons": persons,
                "households": households,
                "latitude": lat,
                "longitude": lon,
                "coordinate_status": status,
                "coordinate_source": COORD_SOURCE if status == "matched" else "",
                "coordinate_method": COORD_METHOD if status == "matched" else "",
                "dataset": DATASET,
                "reference_year": REFERENCE_YEAR,
            }
        )

    out_rows.sort(key=lambda r: r["census_code"])  # deterministic
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(args.out, mode="wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=OUT_COLUMNS)
        w.writeheader()
        for r in out_rows:
            w.writerow(r)
            counts["written"] += 1

    print(f"wrote {counts['written']} rows to {args.out}", file=sys.stderr)
    for k, val in counts.items():
        print(f"  {k}: {val}", file=sys.stderr)
    print("Update data/demand/SOURCES.md with the coverage you just built.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
