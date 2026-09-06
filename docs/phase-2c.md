# Phase 2C — Local Demand Signals

Design notes for what is built. Authoritative rules live in `CLAUDE.md` (§5, §11,
§22, §23); this file records how Phase 2C realises the "read local demand" step —
**evidence of customer potential only**, no viability judgement.

## Scope

Phase 2B answers *how concentrated is the observed competition?* Phase 2C answers:

> **Is there evidence of sufficient local demand or customer potential here?**

It produces raw, transparent measurements — catchment population, settlement
counts, activity anchors — each carrying its provenance and coverage caveats.
It does **not** estimate income, agricultural demand, road access, opportunity,
viability, EMI/DSCR, or produce a "demand score". Those are Phase 2D and later.

`zero competitors is not an opportunity` — it is frequently a village too small to
sustain the business. Phase 2C exists so that gap is visible before capital moves.

## Structural decisions

### Split by purity, not by topic

There is **no `demand/` package**. `market/` carries a documented, enforced
invariant (it imports only `market.*`, `models.*`, `normalization.text`,
`utils.*` — no adapter, geocoder, HTTP client, or DB). That invariant *is* the
acquisition/calculation seam.

| concern | home |
|---|---|
| pure engine | `market/demand.py`, `market/demand_models.py`, `market/demand_config.py` |
| impure acquisition (network + disk) | `discovery/demand_acquisition.py` |
| shared domain + evidence models | `models/demand.py` |

`tests/test_market_purity.py` walks the AST of every `market/` module and fails
if any imports `sources`, `geocoding`, `database`, `discovery`, or `httpx`.

### No runtime census↔OSM name matching

Census PCA rows have names + admin codes but **no coordinates**. Matching OSM
settlement names to census names at request time is unsafe:

- an Overpass `place=village` node returns a name and **no admin hierarchy**, so
  there is no cheap way to scope a census lookup to the right district per
  settlement;
- OSM rural India often tags names in Devanagari; `unidecode("रामपुर") →
  "ramapura"` vs Census `"Rampur"` scores below any safe threshold;
- a *wrong* match attributes a real population to the wrong village and stamps it
  "SOURCE FACT: Census 2011" — fabrication with a provenance stamp (§3.1, §30).

Instead `scripts/build_census_dataset.py` joins Census PCA to village centroids
**by census code**, once, offline. Runtime is a bounding-box + haversine filter
over rows. No name matching anywhere in the running system.

### OSM settlements are the coverage denominator, never a population source

OSM enumerates *how many settlements exist*; the census extract supplies *the
population of the ones we have data for*. The gap between them is the headline:
"OSM shows 7 settlements; we have Census 2011 population for 4 (4,180 people); the
catchment population is **at least** 4,180." A mis-association here only moves a
reported ratio, never a summed total.

## Data flow

```
Phase 1 DiscoveryResult ─────────────► discovery/demand_acquisition.py  (IMPURE)
  resolved point, radius, admin names      1 Overpass union query: place=* + 4
                                            activity kinds
                                           parse -> normalize -> Settlement[] /
                                            ActivityPoint[]
                                           CensusVillageSource.near() slice
                                           distances computed HERE
                                                    │
                                                    ▼
                                            DemandEvidence   (JSON round-trippable,
                                             no OSM tags / census columns inside)
                                                    │
Phase 2B CompetitionMetricsResult ── optional ──►   ▼
                              market/demand.py::compute_demand_signals()  (PURE)
                                                    │
                                                    ▼
                                            DemandSignalsResult
```

The evidence carries `acquired_at` from the injected clock — the **only**
wall-clock read in the phase. The engine never reads a clock.

## Modules

| module | contents |
|---|---|
| `models/demand.py` | `Settlement`, `SettlementHit`, `PopulationRecord`, `ActivityPoint`, `ActivityHit`, `DemandEvidence`, `DemandAcquisitionReport`; `SettlementType`, `ActivityKind` (four kinds), `GeographyLevel`. Reuses `ProvenanceEntry` from `models/business.py` |
| `categories/osm_place_tags.py` | `place=*` → `SettlementType`, `amenity/railway/highway=*` → `ActivityKind`, and the single `DEMAND_QUERY_SELECTORS` union list |
| `sources/osm/parse.py` | `parse_element` — extracted from the business adapter so both share the node-vs-`center` handling |
| `sources/osm/places.py` | `fetch_demand_elements()` — reuses `OverpassClient`, `build_overpass_ql`, `RawOsmElement`, `OverpassFetch` verbatim |
| `sources/census/loader.py` | `CensusVillageRow`, `CensusVillageSource.near(lat, lon, radius_m, *, state=None)` — streams the CSV(.gz), bbox prefilter (`_METRES_PER_DEG_LAT`) then haversine; degrades to an empty result on a missing/short/wrong file, never raises |
| `normalization/settlement.py` | `RawOsmElement` → `Settlement` / `ActivityPoint`; `CensusVillageRow` → `Settlement` |
| `discovery/demand_acquisition.py` | `acquire_demand_evidence(discovery, *, client, census=None, settings=None, clock=utcnow)` — catches `SourceUnavailable/SourcePayload/Http` and records them as data; never raises |
| `market/demand_config.py` | frozen `DemandConfig` + `DEFAULT_DEMAND_CONFIG` |
| `market/demand_models.py` | `DemandStatus`, `CatchmentPopulation`, `ActivitySummary`, `DemandCoverage`, `DemandSignalsResult` |
| `market/demand.py` | `compute_demand_signals(evidence, *, competition=None, config=None)` — pure |

## Signals

| # | signal | source | it is a proxy for | it is NOT |
|---|---|---|---|---|
| S1 | village population | Census 2011 PCA (village) | resident customer base | current population (15 yrs stale); purchasing power |
| S2 | households | Census 2011 PCA (village) | purchasing units | reported blended with persons |
| S3 | settlement count / composition | OSM `place=*` | isolated village vs cluster; the coverage denominator | a complete settlement inventory |
| S4 | `population=*` tag | OSM | a fallback town figure | part of the catchment total — quarantined as `osm_tagged_population_total` |
| S5 | activity anchors (school, marketplace, bank, transport stop) | OSM amenities | settled population, daily/transient footfall | a measure of purchasing demand |

District/block population, agriculture, livestock, income: **not used** — they are
district-resolution and would be a village-level fabrication (§30). Deferred to
Phase 2D+ where a district-level indicator can be labelled as such.

## Calculations and exact formulas

Let `S` = OSM settlement hits in radius; `C` = census rows in radius made unique
by `census_code` (nearest kept, rest → `duplicates_suppressed`); `P` = rows in
`C` carrying a `PopulationRecord`.

| metric | formula |
|---|---|
| `settlements_found` | `max(len(S), len(C))` — OSM enumerates; census is the denominator only when OSM saw fewer (e.g. OSM down) |
| `settlements_with_population` | `len(P)` |
| `population_coverage` | `len(P) / settlements_found` (`None` when `settlements_found == 0`); `0.0 ≤ x ≤ 1.0` by construction |
| `catchment_population` (`persons`) | `Σ p.population.persons` over `P` where present; **`None`, never `0`**, when none |
| `catchment_households` | `Σ p.population.households` over `P` where present — summed independently of persons |
| `population_is_floor` | `population_coverage < 1.0` (when `persons` is known) |
| `catchment_area_km2` | `π · (radius_m / 1000)²` — identical to Phase 2B |
| `density_persons_per_km2` | `persons / catchment_area_km2` |
| `boundary_settlements` | `{ p ∈ P : p.distance_m ≥ boundary_fraction · radius_m }` (default `0.8`) |
| `boundary_persons` / `boundary_share` | `Σ` their persons; `boundary_persons / persons` |
| `activity_counts[k]` | `|{ a : a.kind == k, distance_m ≤ radius_m }|` |
| `nearest_m_by_kind[k]` | `min(distance_m)` over that set |
| `competitors_per_1000_people` | `1000 · competition.direct_count / persons` — only when a Phase 2B `OK` result **and** a known `persons` are supplied |

### Inclusion rule

A settlement contributes its **full** population if its centre lies within the
radius, and nothing otherwise (user's choice). Plain sum of source facts, no
modelling assumption. `boundary_settlements` exposes the sensitivity: the
all-or-nothing rule is **bidirectional** — a centre just outside contributes
nothing though most of the village is inside, and vice-versa — so its bias
direction is *indeterminate*. `population_is_floor` (from coverage `< 1.0`) is
one-directional and is a genuine lower bound; the two must not be conflated.
`competitors_per_1000_people` **inverts** on a floor denominator — it becomes an
*upper* bound on saturation. The field's `note` says so.

### Double-counting guard — population-key uniqueness

Each `census_code` contributes to the total **exactly once** (nearest hit kept);
a settlement with no `census_code` contributes **zero** and only counts toward
`settlements_found`. Deterministic, provably non-double-counting, and inspectable
via `duplicates_suppressed`. No fuzzy clustering — it cannot detect subset
relationships (a `place=village` node vs its child hamlets) and would risk
merging two genuinely distinct same-named villages.

## Confidence

```
freshness       = max(freshness_floor, 1 - decay_per_year · (reference_year - data_year))
                  defaults 0.45 / 0.03  →  Census 2011 at reference_year 2026 ≈ 0.55
population_conf  = census_tier(0.85) · freshness · population_coverage · geography_in_extract
                  (None when persons is unknown; geography_in_extract ∈ {0.0, 1.0})
settlement_conf = min(osm_single_source_ceiling(0.75), 0.3 + 0.05 · settlements_found)
activity_conf   = min(osm_single_source_ceiling, 0.2 + 0.1 · distinct_kinds)
demand_data_confidence = weighted mean of the non-None confidences (config weights,
                         renormalised); 0.0 when none.
```

- `reference_year` is a **config value, not `datetime.now()`** — staleness is a
  stated assumption echoed into the result, so pinned assertions never drift on
  1 January (§28).
- `geography_in_curated_extract` is a hard multiplicative gate (`0.0` outside the
  extract's state coverage), echoed in `demand_data_confidence_basis` — it
  pre-empts "what about a district you didn't cover?".
- Confidence measures how well the **evidence** is observed, never how much
  demand exists. High confidence + low population is a valid, informative result.

## Statuses (`DemandStatus`)

The task's three population-coverage states are kept **distinct** — they never
collapse to zero:

| state | status | `catchment.persons` |
|---|---|---|
| population available **and** placeable | `ok` | the summed figure |
| population available for the area but **no row is geolocatable** | `population_not_geolocated` | `None` |
| no Census 2011 record for the area at all | `no_population_data` | `None` |

Full list:

| status | when |
|---|---|
| `ok` | ≥1 settlement in the catchment carries a geolocated `PopulationRecord` |
| `population_not_geolocated` | Census 2011 population exists for the area (`coverage.population_records_ungeolocated_in_area > 0`) but every such row is `unmatched` / `ambiguous` — no coordinate. `coverage.population_available_not_geolocated = True` |
| `no_population_data` | no Census record for the area — outside the extract's coverage, or genuinely absent (`persons = None`, never `0`) |
| `no_settlements_found` | no known settlement at all from any source |
| `location_unresolved` | Phase 1 gave no catchment centre |
| `source_unavailable` | OSM failed **and** no census extract present |
| `invalid_radius` | radius ≤ 0 |

**Overpass down but the census file present is not `source_unavailable`** — the
census layer is offline and unaffected, so a real population number still comes
out, with a warning.

## Configuration (`market/demand_config.py`)

| field | default | meaning |
|---|---|---|
| `reference_year` | `2026` | "as of" year for freshness — replaces a clock read |
| `census_reference_year` | `2011` | the year Census PCA describes |
| `freshness_floor` / `freshness_decay_per_year` | `0.45` / `0.03` | staleness curve for a stale-but-authoritative source |
| `census_tier` | `0.85` | source tier for a complete 2011 enumeration |
| `osm_single_source_ceiling` | `0.75` | Phase 1's single-source cap, carried over |
| `boundary_fraction` | `0.8` | a settlement centre at ≥ this fraction of the radius is boundary-proximate |
| `weight_population` / `weight_settlement` / `weight_activity` | `0.6` / `0.2` / `0.2` | overall-confidence weights |
| `interpretation_guidance` | 4 fixed strings | human-authored caveats copied verbatim into every result — **not** generated interpretation |

All `# [tunable]` MVP heuristics; echoed into `DemandSignalsResult.config`.

## The reference data

`data/demand/census2011_villages.csv.gz` — **640,949 rows, one per Census 2011
village**, every row carrying an official `persons` and `households` value.
Columns: `census_code, name, state, district, subdistrict, persons, households,
latitude, longitude, coordinate_status, coordinate_source, coordinate_method,
dataset, reference_year`. `data/demand/SOURCES.md` has the full provenance.

- **Population + households — India-wide, official.** Source: Census of India
  2011 "Basic Population Figures … / Village" workbook
  (`2011-IndiaStateDistSbDistVill-0000.xlsx`) from the RGI NADA catalogue
  (`censusindia.gov.in/nada/index.php/catalog/42554`). The ETL streams the
  334 MB xlsx with the stdlib (no pandas/openpyxl), keeps `Level == VILLAGE`
  rows, and uses `TOT_P` / `No_HH` verbatim. `0` is a real Census value and is
  kept as `0`, never blanked.
- **Coordinates — Bihar only in this build.** `coordinate_status`:
  `matched` (39,263 Bihar villages) → `latitude`/`longitude` set;
  `ambiguous` (4,654) → name not unique within its sub-district, no coordinate;
  `unmatched` (597,032 — every non-Bihar state) → no boundary data.
  Source: datameet `indian_village_boundaries` GeoJSON, joined **offline** on
  `(state, district, sub-district, normalized village name)`, unique-match-only;
  representative point = mean of the polygon's exterior ring. Coordinates are
  **never guessed** and never taken from a non-unique name match.
- **Post-2011 state names** are reconciled at read time
  (`sources/census/loader.py`): Telangana → Andhra Pradesh (2014 split),
  Ladakh → Jammu & Kashmir (2019 split).

Regenerate with `scripts/build_census_dataset.py` (one-off ETL, not imported by
the package, deterministic — rows sorted by `census_code`, no wall-clock). Raw
downloads go under `data/demand/raw/` (git-ignored). To widen coordinate
coverage, add more `<state>.geojson` files to `--boundaries` (datameet publishes
9 states: `br ga gj ka kl mh or rj sk`).

The census loader reads `.csv` and `.csv.gz`. It does not cache (a local file
read, unlike the network sources, needs no `JsonFileCache`).

### Geographic linkage summary

| input | value | note |
|---|---|---|
| population rows | 640,949 | all India, official |
| geolocated (`matched`) | 39,263 | Bihar; ≈ 89 % of Bihar villages |
| `ambiguous` (name not unique in sub-district) | 4,654 | Bihar; coordinate withheld |
| `unmatched` (no boundary data) | 597,032 | non-Bihar states |

## CLI

`scripts/discover_businesses.py --demand` runs Phase 1 → Phase 2C. With
`--metrics --demand` the Phase 2B result feeds `competitors_per_1000_people`.
`--demand --json` emits the `DemandSignalsResult` (JSON precedence:
demand → metrics → analysis → discovery). Exit code `2` on
`location_unresolved` / `invalid_radius` / `source_unavailable`;
`no_population_data` and `no_settlements_found` are not errors.

## Testing

- `tests/test_demand_engine.py` — pure, hand-built `DemandEvidence`: population
  present / absent / households-only, partial coverage floor, census-code
  double-count, boundary sensitivity, activity counts, per-1,000 ratio (and its
  upper-bound note), degradation statuses, `reference_year`-from-config
  determinism, "no score field exists", JSON round-trip.
- `tests/test_demand_acquisition.py` — fake Overpass client + fixture census CSV:
  one union call, injected-clock provenance, OSM-down-with-census-intact,
  missing-file warning, location-unresolved, outside-coverage warning.
- `tests/test_osm_places.py` — respx: parse, drop coordinate-less, mirror
  fallback, retry, bad payload → `SourceUnavailableError`; place/activity mapping.
- `tests/test_census_loader.py` — bbox + haversine, radius boundary, duplicate
  codes both returned (engine dedupes, not the loader), households-only row,
  ungeolocated-population-in-area counted not dropped, post-2011 state alias
  (Telangana → Andhra Pradesh), invalid-numeric row skipped, outside-coverage,
  missing/short/wrong file, gzip == plain.
- `tests/test_market_purity.py` — the `market/` no-I/O AST guard.

## Known limitations

- **Coordinates cover Bihar only** in the committed build. Everywhere else,
  population is present but `coordinate_status = unmatched`, so a catchment
  outside Bihar reports `population_not_geolocated`, not a number. datameet
  publishes boundaries for 9 states; the ETL takes as many as you give it.
- `population_records_ungeolocated_in_area` is **state-scoped** when the query's
  district name does not match a 2011 district name (common — many districts
  were created after 2011). So for Sangareddy it reports the whole Andhra
  Pradesh village count, not just the local mandals. It is a coarse
  "population exists for this state" signal, not a catchment figure.
- The coordinate join is an offline **name** join within sub-district. datameet
  district/sub-district spellings mostly but not always match the RGI file, so a
  few thousand Bihar villages land `unmatched` on a spelling mismatch rather
  than a true gap.
- Representative point = mean of a polygon's exterior ring — an interior-ish
  point, not a true centroid; fine for the radius test, not for fine geometry.
- Population density is per km² of the **circular search area**, not per capita
  or per road-network catchment (`CLAUDE.md` §10).
- `settlements_found = max(len(S), len(C))` cannot dedupe OSM against census
  (no shared key) — a deliberately conservative denominator that can never
  produce coverage > 1.0.
- The boundary rule's bias direction is indeterminate (see above).
- Confidence weights and freshness constants are hand-picked MVP heuristics.

## What Phase 2D should do next (not built)

Combine Phase 2B competition with Phase 2C demand into an **interpretation** —
is this catchment under- or over-served, and for which categories — and only then
an opportunity comparison across candidate businesses (`CLAUDE.md` §12). Phase 2D
may also add district-level economic / agricultural indicators, explicitly
labelled as district-level context, and generated natural-language explanation
belongs later still (Phase 6, gated by §3.3).
