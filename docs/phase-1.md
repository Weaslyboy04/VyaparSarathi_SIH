# Phase 1 — Hyper-Local Business Data Acquisition and Storage

Design notes for what is built. Authoritative rules live in `CLAUDE.md`
(§4–§10, §26); this file records *how* Phase 1 realises them and the decisions a
future contributor needs.

## Pipeline

`DiscoveryService.discover(location_text, category, radius_m)` runs
(`src/vyaparsarathi/discovery/service.py`):

1. **Geocode** — `NominatimGeocoder.geocode` returns up to 6 `PlaceCandidate`s.
2. **Disambiguate** — `geocoding/resolve.py::resolve_place`:
   - 0 candidates → `GeocodingError` → result status `location_not_found`;
   - 1 candidate → resolved;
   - N candidates all within `DEFAULT_CLUSTER_M` (2 km) of the top-ranked one →
     resolved to the top, the rest kept as `alternates`;
   - otherwise → `LocationAmbiguousError` → result status `location_ambiguous`
     with the candidate list. The service never guesses.
   - **explicit override:** `discover(..., candidate=N)` (CLI `--candidate N`,
     1-based) skips the ambiguity check and resolves to the N-th candidate in
     `rank_candidates` order — the same order the `location_ambiguous` listing
     shows. `geocoding/resolve.py::select_candidate` does this with no extra
     geocoding; an out-of-range `N` raises `ValueError` (candidate 1 is never
     assumed) and an empty candidate list still yields `location_not_found`.
3. **Select OSM tags** — `categories/osm_query_tags.py::selectors_for(category)`.
   Empty list → status `no_results` (category not queryable yet).
4. **Fetch** — `sources/osm/adapter.py::OverpassSource.fetch` builds Overpass QL
   and calls `OverpassClient.run`. Failure of all endpoints → status
   `source_unavailable`.
5. **Normalize** — `normalization/business.py::normalize_osm_element` per element.
   `NormalizationError` (e.g. no coordinate) increments `dropped_other`.
   Unknown categories are counted in `coverage.per_source[*].unmapped_tags`.
6. **Deduplicate** — `dedup/deduplicator.py::Deduplicator.dedupe`.
7. **Persist** — `repository.save_businesses`. A persistence exception is logged
   and added to `warnings`; it does **not** fail the run.
8. **Distances + clamp** — haversine from the resolved point; drop anything
   beyond `radius_m`; sort ascending onto `BusinessHit.distance_m`.
9. **Coverage + confidence** — see below.

`discover` raises `ValueError` only for a caller mistake (empty location, radius
≤ 0 or > `max_radius_m`). Every operational problem is a `DiscoveryResult` with a
`status` and `warnings`.

## Overpass (`sources/osm/`)

- `build_overpass_ql` emits `[out:json][timeout:N]; ( nwr["k"="v"](around:r,lat,lon); ... ); out center tags;`
  — `nwr` covers node/way/relation; `out center` gives ways/relations a point.
- `OverpassClient.run` tries `settings.overpass_endpoints` (primary then mirrors)
  in order. Per endpoint, `utils/http.py::request_with_retry` retries **only**
  transient failures (429, 500/502/503/504, connect/read timeouts) with
  exponential backoff (`http_backoff_base_s * 2**attempt`), honouring
  `Retry-After`. A non-retryable 4xx or an unparseable body moves straight to the
  next endpoint. All endpoints exhausted → `SourceUnavailableError`.
- `mirror_fallback_used` / `endpoint_used` are surfaced in the result and as a
  warning.
- Raw responses are cached (`utils/cache.py`, keyed by the full QL string, TTL
  `VYAPAR_CACHE_TTL_S`) so repeated runs and demos don't re-hit the API. The
  test suite disables the cache.
- `_parse_element` drops elements with no usable coordinate and counts them in
  `dropped_no_coordinates`. `source_id` is `"<type>/<id>"` (e.g. `node/123`).
- The adapter returns **raw `RawOsmElement`s only** — no `NormalizedBusiness`
  crosses this boundary.

## Geocoding (`geocoding/nominatim.py`)

- `GET {VYAPAR_NOMINATIM_URL}/search?format=jsonv2&addressdetails=1`.
- Politeness: descriptive `User-Agent` (from config), a process-wide throttle of
  `nominatim_min_interval_s` (default 1/s), and response caching.
- Admin hierarchy is read from the `address` object: `district` ←
  `state_district|district|county`, `block` ←
  `county|subdistrict|municipality|city_district|region`, `village` ←
  `village|hamlet|town|city|suburb|municipality` (first present wins). Any may be
  `None`.
- Transient failures retried and wrapped as `GeocodingError`; a non-JSON or
  non-list body is `SourcePayloadError`.
- Behind the `Geocoder` protocol so a self-hosted Nominatim / Photon / commercial
  geocoder can replace it.

## Category taxonomy (`models/taxonomy.py`, `categories/`)

- `BusinessCategory` (StrEnum) is the stable internal vocabulary; values are
  stored as strings so new categories don't break persisted data.
- `categories/osm_map.py::map_osm_tags(tags)` → `(category, matched_or_hint_tag)`.
  Keys are checked in priority order `shop, amenity, craft, healthcare, office,
  man_made`. No positive match → `UNKNOWN` plus the most informative raw tag for
  logging (never silently dropped). Non-one-to-one choices (e.g. `amenity=fuel` →
  `automobile_repair`, `shop=chemist` → `pharmacy`) are documented in that file.
- `categories/osm_query_tags.py::OSM_QUERY_SELECTORS` is the reverse direction:
  which `(key, value)` selectors to query for a given internal category. It is
  intentionally conservative — a `grocery` query asks for
  `shop=convenience|supermarket|grocer|greengrocer|general`, **not** every
  food-related tag. Adjacent-but-distinct tags (e.g. `shop=general`) are included
  on purpose; category-compatibility handles the overlap in dedup and Phase 2.

## Normalization (`normalization/`)

- Name is taken from `name, name:en, int_name, official_name, brand, operator`
  (first non-empty); may be `None`.
- `normalized_name` (`text.py::normalize_name`) — transliterate (`unidecode`),
  lowercase, `&`→`and`, strip punctuation, collapse whitespace, drop only leading
  articles (`the/a/an`). Conservative: it must not erase distinguishing tokens.
  `name` stays human-readable.
- Address assembled from `addr:full` or the `addr:*` components in order.
- `data_quality` ∈ [0,1]: 0.15 base (coordinate) + 0.40 name + 0.30 known
  category + 0.15 address − 0.05 if the point is a way/relation centroid.
- `provenance` starts with one `ProvenanceEntry(source, source_id, retrieved_at)`.
- `raw` keeps the original OSM payload verbatim.

## Deduplication (`dedup/deduplicator.py`)

Thresholds are config (`VYAPAR_DEDUP_*`), not literals:

| | name similarity (rapidfuzz `token_set_ratio`) | distance | category |
|---|---|---|---|
| **merge** | ≥ 87 | ≤ 120 m | equal or compatible |
| **uncertain** (flagged, not merged) | ≥ 75 | ≤ 200 m | equal or compatible |
| **distinct** | otherwise | | |

- Either name missing → similarity 0 → never merged on geometry alone.
- **Non-transitive:** a record joins a cluster only if it merges pairwise with
  *every* member, so A~B and B~C but A≁C yields `{A,B}` and `{C}`.
- Merged record: best (highest-quality, else longest) name; first non-`UNKNOWN`
  category; union of provenance (dedup by `(source, source_id)`); `first_seen` =
  min, `last_updated` = max; `data_quality` = max + 0.05 if corroborated by more
  than one distinct source.
- Every merge is recorded as a `MergeDecision` (kept/absorbed ids, scores,
  reason) and logged — merges are reversible, not just their result.
- `uncertain_pairs` are returned for later review; Phase 1 does not act on them.

## Persistence (`database/`)

- `BusinessRepository` protocol: `save_business(es)`, `get_by_internal_id`,
  `businesses_by_category`, `businesses_near(lat, lon, radius_m, category=None)`,
  `count`.
- `InMemoryBusinessRepository` — default for the CLI and tests.
- `SqlBusinessRepository` — SQLAlchemy 2.x, any URL (`VYAPAR_DB_URL`), SQLite by
  default. Two tables:
  - `businesses` — the normalized (possibly merged) entity; lat/lon are `Float`
    with a btree index `ix_businesses_lat_lon`; `category` indexed.
  - `source_records` — one row per `(source, source_id)` observation, FK to its
    business, `UNIQUE(source, source_id)`. A merge **reassigns** a source record
    to the surviving business; a business row left with zero observations is
    deleted. This keeps merges reversible and multi-source observations intact.
- `businesses_near` prefilters with a lat/lon bounding box in SQL, then applies
  the exact haversine filter in Python. **Follow-up:** replace with a PostGIS
  `geography` column + `ST_DWithin` / GiST index — contained to `database/`.

## Coverage confidence (`discovery/confidence.py`)

A **data-coverage** signal only (CLAUDE.md §22), never viability. `0.0` when
nothing was found. Otherwise `0.35 + min(0.30, 0.03·N) + 0.20·mapped_share −
0.05·fallback`, capped at **0.75** — one uneven, contributor-dependent source
cannot justify more. Recompute properly once a second source exists.

## Known limitations (Phase 1)

- **OSM only.** Rural coverage is thin; many villages return `no_results` (this
  is reported honestly, with the standard disclaimer, not hidden).
- **No cross-run identity.** `internal_id` is a fresh UUID each run; re-running
  `--db` for the same area writes new rows (source records are reassigned/cleaned
  on merge within a run, but there is no upsert-by-source_id across runs yet).
- **Straight-line radius.** No travel-time / road-network catchment (by design;
  later phase).
- **Disambiguation is geometric.** Candidates within 2 km collapse to one; wider
  spreads are reported ambiguous. It does not use population or feature class to
  auto-pick a "main" place.
- `businesses_near` / `by_category` on the repository are provided for Phase 2 to
  consume; the Phase 1 CLI does not use them.

## Adding another business-data source later

1. New package `sources/<name>/` with a `models.py` for its raw shape (stays
   inside the package), a client, and an adapter exposing
   `fetch(query, selectors) -> <Name>Fetch`.
2. Add `<NAME>` to `SourceName`.
3. Add `categories/<name>_map.py` (tags → `BusinessCategory`) and, if it needs
   targeted queries, `categories/<name>_query_tags.py`.
4. A `normalization/<name>.py` mapping its raw record → `NormalizedBusiness`
   (set `source`, `source_id`, `provenance`, `raw`).
5. Wire it into `DiscoveryService` alongside Overpass and extend
   `CoverageSummary.per_source`. Dedup already merges across sources and gives a
   corroboration bonus.
