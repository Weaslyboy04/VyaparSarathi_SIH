# Phase 2B — Competition Metrics

Design notes for what is built. Authoritative rules live in `CLAUDE.md` (§11,
§22); this file records how Phase 2B realises the "measure the market —
competitor count, nearest-competitor distances, density per catchment,
saturation signal" part of the step, **counts and distances only**.

## Scope

Phase 2A answers *which* discovered businesses compete with the proposed
business. Phase 2B answers exactly one further question:

> **How concentrated is that competition around the proposed location?**

It computes counts, distances, distance bands, a raw spatial density, and a
transparent competition signal. It does **not** estimate demand, population,
income, accessibility, opportunity, viability, EMI/DSCR, or anything requiring a
population denominator — those are Phase 2C and later.

## Data flow

```
Phase 1  DiscoveryResult ─────────────┐  (radius, query point, data confidence)
              │                       │
              ▼                       ▼
Phase 2A  CompetitorAnalysisResult ─► Phase 2B  compute_competition_metrics()
          (direct / adjacent buckets)          └─► CompetitionMetricsResult
```

`compute_competition_metrics(analysis, discovery, *, config=None)` consumes the
Phase 2A result (the classifications) plus the originating Phase 1 result (for
the analysis radius, the query point, and the pass-through data-coverage
confidence). `metrics_from_discovery(discovery, subtypes=())` is a convenience
that runs Phase 2A then Phase 2B in one call.

`market/` still imports only `market.*`, `models.*`, `normalization.text`,
`utils.*` — no source adapter, geocoder, HTTP client, or repository. Phase 2B
adds no new dependency and makes no external call. It never re-classifies a
business and never recomputes a geographic distance — it uses the
query-relative `distance_m` carried on each `ClassifiedCompetitor` from the
Phase 1 `BusinessHit`.

## Modules (`src/vyaparsarathi/market/`)

| module | contents |
|---|---|
| `metrics_config.py` | **the single config layer for 2B** — `CompetitionMetricsConfig` (frozen Pydantic model): `distance_bands_m`, competition-signal count/density thresholds; `DEFAULT_METRICS_CONFIG` |
| `metrics_models.py` | `CompetitionSignal` (`none`/`low`/`moderate`/`high`), `CompetitionMetricsStatus` (`ok`/`unknown_category`/`invalid_radius`), `DistanceStats`, `DistanceBand`, `CompetitionDensity`, `CompetitionMetricsResult` — Pydantic v2, JSON-serializable, units carried explicitly |
| `metrics.py` | `compute_competition_metrics()`, `metrics_from_discovery()`, deterministic helpers |

## Direct vs adjacent (STEP 3)

The buckets come straight from Phase 2A; Phase 2B does not touch the
classification. **Direct competitors drive the primary metrics.** Adjacent
competitors are reported separately and also rolled into a `relevant`
(= direct + adjacent) view for every distance metric and band. The two are
never silently mixed:

```
direct_count           5
adjacent_count         1
total_relevant_count   6
```

## Metrics and exact formulas

Let `D` = direct competitors within the analysis radius that carry a usable
`distance_m`, sorted ascending; `R` = the same for direct + adjacent;
`radius_m` = `discovery.query.radius_m` (else `requested_radius_m`).

| metric | formula |
|---|---|
| `direct_count` / `adjacent_count` | number of classified competitors in each bucket whose `distance_m` is `None` **or** `≤ radius_m` (competitors with a known distance beyond the radius are dropped and a warning is added) |
| `total_relevant_count` | `direct_count + adjacent_count` |
| `direct_distance.count` | `len(D)` — competitors with a usable distance |
| `direct_distance.missing_distance` | count of direct competitors with `distance_m is None` (excluded from every stat below) |
| `nearest_m` | `D[0]` (`None` if `D` empty) |
| `farthest_m` | `D[-1]` |
| `mean_m` | `sum(D) / len(D)` |
| `median_m` | `statistics.median(D)` — mean of the two middle values for an even count |
| `distance_bands[i].direct_count` | `count(d in D where d ≤ distance_bands_m[i])` — cumulative |
| `distance_bands[i].relevant_count` | same over `R` |
| `distance_bands[i].exceeds_radius` | `distance_bands_m[i] > radius_m` |
| `density.catchment_area_km2` | `π · (radius_m / 1000)²` (`None` when `radius_m ≤ 0`) |
| `density.direct_per_km2` | `direct_count / catchment_area_km2` — `0.0` when there are no direct competitors and the radius is valid; `None` when the radius is invalid |
| `density.relevant_per_km2` | `total_relevant_count / catchment_area_km2` |
| `signal` | `max(count_level, density_level)` by rank `none < low < moderate < high` |
| `data_confidence` | `discovery.confidence`, unchanged (CLAUDE.md §22) |

`count_level`: `0 → none`; `≥ signal_count_high → high`; `≥ signal_count_moderate
→ moderate`; else `low`.
`density_level`: `None`/`≤ 0 → none`; `≥ signal_density_high_per_km2 → high`;
`≥ signal_density_moderate_per_km2 → moderate`; else `low`.

`signal_reason` is a plain-language sentence naming both levels and which one
won; `signal_basis` is a dict of the raw numbers and every threshold used, so
the label is fully reconstructible from the result object.

## Configuration values introduced (`metrics_config.py`)

| field | default | meaning |
|---|---|---|
| `distance_bands_m` | `(500, 1000, 2000, 5000)` | cumulative "competitors within X m" bands |
| `signal_count_moderate` | `3` | `≥` this many direct competitors → at least `moderate` |
| `signal_count_high` | `6` | `≥` this many → `high` |
| `signal_density_moderate_per_km2` | `0.05` | direct density `≥` this → at least `moderate` |
| `signal_density_high_per_km2` | `0.15` | direct density `≥` this → `high` |

**All five are MVP heuristics, not empirically validated.** They exist to turn
classifications into comparable numbers for Phase 2D; `signal` is deliberately a
transparent label over two measured inputs, not a "market saturation score"
(CLAUDE.md §11, STEP 6). Pass a custom `CompetitionMetricsConfig` to override;
it is echoed into `CompetitionMetricsResult.config`.

## Data confidence vs viability (STEP 8)

`data_confidence` is the Phase 1 data-coverage number carried through untouched,
with `data_confidence_note` stating it measures *how well the market is
observed*, not whether the business will succeed. There is no viability,
recommendation, or opportunity field anywhere on the result — those are later
phases.

## Edge cases (STEP 5)

- **No direct competitors** → counts `0`, all distance stats `None`, density
  `0.0` at a valid radius (never a fake `0 m`), `signal = none`.
- **One competitor** → `nearest = farthest = mean = median` = its distance.
- **Ties** → handled naturally (sorted list; `statistics.median`).
- **Competitor beyond the radius** → excluded from every metric; a warning
  records how many were dropped.
- **Missing `distance_m`** → the competitor still counts toward
  `direct_count`/`adjacent_count` and the density numerator (it was identified
  within the search), but is excluded from distance stats and bands and tallied
  in `missing_distance`.
- **Invalid radius (`≤ 0`)** → `status = invalid_radius`, density and area
  `None`, bands still returned (all `exceeds_radius = True` is possible), warning
  added. Radius is otherwise validated upstream by `DiscoveryQuery` (`> 0`).
- **Unknown proposed category** (Phase 2A `unknown_category`) → passed straight
  through as `status = unknown_category` with zeroed metrics and the Phase 2A
  clarification warning preserved.

## CLI

`scripts/discover_businesses.py --metrics` runs Phase 1 → Phase 2A → Phase 2B
(`--metrics` implies `--competitors`). `--subtype` / `--proposed` refine the
proposal exactly as in Phase 2A. `--metrics --json` emits the
`CompetitionMetricsResult` (JSON payload precedence: metrics → analysis →
discovery). Exit code `2` when the metrics status is not `ok`.

## Example — Hajipur grocery (illustrative shape)

For a `("Hajipur, Vaishali, Bihar", grocery, 5 km)` discovery that returns 5
grocery / general stores and 1 dairy at distances
`132 / 480 / 1500 / 1900 / 3200 m` (direct) and `900 m` (adjacent):

```
Direct competitors:        5
Adjacent competitors:      1
Total relevant:            6
Nearest direct competitor: 132 m
Average direct distance:   1.44 km
Median direct distance:    1.50 km
Direct within   500 m:     2
Direct within 1.00 km:     2
Direct within 2.00 km:     4
Direct within 5.00 km:     5
Search area:               78.54 km²
Direct competitor density: 0.064 / km²
Competition signal:        MODERATE
Data confidence:           0.70
```

Exact values come from the live OSM data, not from anything hard-coded — the
tests build their own fixtures rather than pinning names or counts from a live
run.

## Known limitations

- Density is competitors per km² of the **circular search area**, not per capita
  or per road-network catchment. A straight-line radius overstates reach where
  roads are sparse (`CLAUDE.md` §10). This is explicitly labelled in
  `CompetitionDensity.note`.
- The competition signal is a two-input heuristic (count, density) with
  hand-picked thresholds. It is a preparatory input for Phase 2D, not a verdict.
- Distance bands wider than the analysis radius are still reported (flagged
  `exceeds_radius`) so a fixed band set stays comparable across runs; their
  counts simply saturate at the total.
- `distance_m` precision is whatever Phase 1's haversine produced; no
  road-distance correction.
- On a Phase 1 result that was category-filtered (the normal case), almost every
  discovered business is grocery-ish, so `adjacent_count` is usually small; a
  wider discovery net would populate the adjacent metrics more.

## What Phase 2C should do next (not built)

Introduce **local demand signals**: population and households in the catchment
(including surrounding villages), market centres, road access, and
agricultural / livestock indicators as demand proxies. Only then does a
population-normalized figure (competitors per 1,000 people) become meaningful —
Phase 2B deliberately omits it. Phase 2C does not score opportunity or judge
viability; that is Phase 2D onward.
