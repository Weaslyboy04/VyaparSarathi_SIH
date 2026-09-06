# `data/demand/` — provenance

The offline reference data for Phase 2C (local demand signals). It is the first
checked-in non-code data asset in the repository; treat every file here as
**sourced fact** and keep this file in step with it (CLAUDE.md §23, §33).

Raw downloads live under `data/demand/raw/` (git-ignored, multi-GB). Only the
normalized output is committed. Regenerate with `scripts/build_census_dataset.py`
(a one-off ETL; not imported by the package, runs no part of the request path).

---

## `census2011_villages.csv.gz`

**One row per Census 2011 village: official population + households, plus a
representative coordinate where an offline geographic join succeeded.**

- Rows: **640,949** (every rural VILLAGE row in the official file).
- Every row has a `persons` and `households` value (0 is a real Census value —
  an uninhabited or fully-relocated village — and is kept as `0`, not blanked).
- Size: ~9.3 MB gzipped.
- Built: 2025 (see the ETL run log). Data year: **2011**.

### Columns

| column | meaning | source |
|---|---|---|
| `census_code` | `State(2)+District(3)+Subdistt(5)+Town_Village(6)` concatenation — unique per village, carries the full hierarchy | Census 2011 |
| `name` | village name as published | Census 2011 |
| `state`, `district`, `subdistrict` | administrative names (state UPPERCASE, district/sub-district Title Case, as published) | Census 2011 |
| `persons` | total population (`TOT_P`) | Census 2011 |
| `households` | number of households (`No_HH`) | Census 2011 |
| `latitude`, `longitude` | representative point, WGS84 decimal degrees — **empty unless `coordinate_status == matched`** | datameet boundaries, see below |
| `coordinate_status` | `matched` \| `unmatched` \| `ambiguous` | ETL |
| `coordinate_source` | `datameet_indian_village_boundaries` when matched, else empty | — |
| `coordinate_method` | the offline join method, when matched | — |
| `dataset` | always `census_2011_pca_village` | — |
| `reference_year` | always `2011` | — |

### 1. Population source (official)

- **Census of India 2011 — "Basic Population Figures of India / State / District /
  Sub-District / Village"**, table workbook
  `2011-IndiaStateDistSbDistVill-0000.xlsx`.
- Publisher: **Office of the Registrar General & Census Commissioner, India**
  (Ministry of Home Affairs).
- Retrieved from the official **NADA catalogue** on `censusindia.gov.in`:
  `https://censusindia.gov.in/nada/index.php/catalog/42554`
  → data file `.../catalog/42554/download/46180/2011-IndiaStateDistSbDistVill-0000.xlsx`
  (~334 MB `.xlsx`; `sheet1` is a single ~640k-row table, columns
  `State, District, Subdistt, Town/Village, Ward, EB, Level, Name, TRU, No_HH,
  TOT_P, TOT_M, TOT_F, P_06, M_06, F_06`).
- The ETL keeps only `Level == VILLAGE` rows and uses `No_HH` and `TOT_P`
  verbatim. District / sub-district *names* are taken from the same file's
  `Level == DISTRICT` / `SUB-DISTRICT` rows (keyed by code).
- Sanity check performed: village-row sums per district match the official
  district RURAL totals to within Census-Town reclassification (e.g. Vaishali,
  Bihar: village sum 3,261,942 vs official district rural ≈ 3.26 M).
- **Not** used: the `data.gov.in` per-state "Primary Census Abstract 2011"
  resources — those are **district-level only** (117 rows for Bihar), and
  district population must never stand in for a village demand estimate
  (CLAUDE.md §11 STEP 10).

### 2. Coordinate source (representative points)

- **datameet `indian_village_boundaries`** — village polygon GeoJSON per state,
  `https://github.com/datameet/indian_village_boundaries`
  (raw: `.../master/<state>/<state>.geojson`).
- Feature properties carry `STATE`, `DISTRICT`, `SUB_DIST`, `NAME` (and a *2001*
  census code, `CEN_2001` — no 2011 code), so the join to the population rows is
  an **offline name join scoped to sub-district**:
  match on `(state, district, sub-district, normalized village name)`, keep the
  point **only when exactly one boundary feature matches** that 4-tuple.
  - 0 matches → `coordinate_status = unmatched`, no lat/lon.
  - 2+ matches → `coordinate_status = ambiguous`, no lat/lon (never guessed).
- The representative point is the **mean of the polygon's exterior ring**
  coordinates — an interior-ish point adequate for the "representative point in
  the catchment radius" rule; not a true centroid.
- datameet publishes boundaries for **9 states only**: `br ga gj ka kl mh or rj
  sk`. This build attached coordinates for **Bihar (`br`)** — the demo state.
  Re-run with more `<state>.geojson` files in `--boundaries` to widen coverage.

### State coverage

| dimension | coverage |
|---|---|
| **Population + households** | **all 35 states/UTs**, 640,949 villages (India-wide, official) |
| **Coordinates (geo-usable in a catchment)** | **Bihar only** — 39,263 villages `matched`, 4,654 `ambiguous` |
| everywhere else | population present, `coordinate_status = unmatched` — usable as "population data exists in this state", not for catchment geometry |

Post-2011 state names are reconciled at read time by
`sources/census/loader.py`: **Telangana → Andhra Pradesh** (carved out 2014),
**Ladakh → Jammu & Kashmir** (split 2019). These are administrative facts, not
guesses.

### Regeneration

```bash
# 1. download the official population workbook into data/demand/raw/
curl -L -o data/demand/raw/2011-IndiaStateDistSbDistVill-0000.xlsx \
  "https://censusindia.gov.in/nada/index.php/catalog/42554/download/46180/2011-IndiaStateDistSbDistVill-0000.xlsx"

# 2. download datameet village boundaries for the state(s) you want coordinates for
mkdir -p data/demand/raw/boundaries
curl -L -o data/demand/raw/boundaries/br.geojson \
  "https://raw.githubusercontent.com/datameet/indian_village_boundaries/master/br/br.geojson"

# 3. build the normalized extract
python scripts/build_census_dataset.py \
    --pca-xlsx data/demand/raw/2011-IndiaStateDistSbDistVill-0000.xlsx \
    --boundaries data/demand/raw/boundaries \
    --out data/demand/census2011_villages.csv.gz

# then update the "State coverage" table above with what you actually built.
```

The ETL is deterministic: rows are sorted by `census_code`, and it reads no
wall clock. `--dump-intermediate DIR` also writes the extracted
`pca_villages.csv` / `pca_hierarchy.csv` for inspection.
