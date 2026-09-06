# VyaparSarathi

**SIH26091 — AI-Driven Hyper-Local Business Advisory and Financial Structuring
Assistant for Rural Micro-Entrepreneurs.**

See [`CLAUDE.md`](CLAUDE.md) for the full project constitution, architecture, and
phase roadmap. This README covers only what is currently built.

## Phase 1 — Hyper-Local Business Data Acquisition and Storage

**Input:** location text + business category + radius.
**Output:** a clean set of normalized, deduplicated, source-backed nearby business
records (a `DiscoveryResult`), or an explicit *ambiguous / not-found /
no-coverage / source-unavailable* outcome — never fabricated data.

Pipeline: `geocode → disambiguate → Overpass fetch → normalize → conservative
dedup → persist → DiscoveryResult`.

## Phase 2A — Competitor Identification

**Input:** a Phase 1 `DiscoveryResult` + a proposed business.
**Output:** a `CompetitorAnalysisResult` sorting every discovered business into
**direct** / **adjacent** / **irrelevant**, each with a deterministic reason.

Phase 2A consumes the Phase 1 result object only — no OSM, Nominatim, HTTP, or
database access. It computes **no** market metrics — those are Phase 2B+. See
[`docs/phase-2a.md`](docs/phase-2a.md).

## Phase 2B — Competition Metrics

**Input:** a Phase 2A `CompetitorAnalysisResult` + the originating Phase 1
`DiscoveryResult`.
**Output:** a `CompetitionMetricsResult` — competitor counts (direct vs
adjacent vs total relevant), nearest / farthest / mean / median direct-competitor
distances, cumulative distance-band counts, a raw spatial density
(competitors per km² of search area), and a transparent `low` / `moderate` /
`high` competition signal with the thresholds that produced it.

Deterministic, offline, no re-classification, no distance recomputation. The
Phase 1 data-coverage confidence is passed through unchanged and is explicitly
**not** a viability judgement. Distance bands and signal thresholds live in
`market/metrics_config.py` as documented MVP tunables. See
[`docs/phase-2b.md`](docs/phase-2b.md).

## Phase 2C — Local Demand Signals

**Input:** a Phase 1 `DiscoveryResult` (+ optionally the Phase 2B result).
**Output:** a `DemandSignalsResult` — settlements and public-service activity
anchors (school / marketplace / bank / transport stop) in the catchment from
OpenStreetMap, the **catchment population** summed from a pre-joined Census 2011
village extract (each census code counted once), reported as a **floor** when
coverage is partial, plus a boundary-sensitivity list, a raw population density,
and — with a Phase 2B result — `competitors_per_1000_people`.

Split by purity: `discovery/demand_acquisition.py` is the only layer that touches
the network / disk; `market/demand.py` is a pure, deterministic engine (no
network, disk, clock or RNG — enforced by `tests/test_market_purity.py`). A
missing population figure is `None`, never `0`; district-level population is never
substituted for a village estimate. `demand_data_confidence` measures how well
the *evidence* is observed, not viability. Tunables live in
`market/demand_config.py`; `reference_year` there replaces `datetime.now()` so
staleness maths stay deterministic. See [`docs/phase-2c.md`](docs/phase-2c.md).

`data/demand/census2011_villages.csv.gz` holds **640,949 rows — every Census 2011
village in India** with official population + households (RGI "Basic Population
Figures … / Village" workbook). Representative coordinates are attached where an
offline `(state, district, sub-district, name)` join to datameet village
boundaries is unique: **Bihar** in this build (39,263 villages), elsewhere the
row is kept as `unmatched`. A catchment outside Bihar therefore reports
`population_not_geolocated` — *population exists for this area but no row could be
placed on the map* — a distinct state from `no_population_data`, never zero.
Rebuild / widen coverage with `scripts/build_census_dataset.py`; see
[`data/demand/SOURCES.md`](data/demand/SOURCES.md).

## Phase 2D — Overall Market Assessment

**Input:** the Phase 2B `CompetitionMetricsResult` + the Phase 2C
`DemandSignalsResult` (+ optionally the Phase 2A result, for competitor names in
findings).
**Output:** a `MarketAssessmentResult` — a market **label**
(`underserved` / `served` / `crowded` / `thin_market` / `mixed` /
`insufficient_evidence`), with `positive_signals`, `concerns` and structurally
separate `data_caveats` (each a typed `Finding` with a machine-readable `code`
and `EvidenceRef`s back to the upstream fields).

The two axes: a **`CatchmentScale`** (`small` / `moderate` / `large`) derived
from Census population, or — where population is absent, the common case outside
Bihar — from an activity/settlement proxy **capped at `moderate`**, with the
tier always shown in `scale_basis`; and `competition_signal`, taken verbatim
from Phase 2B. An explicit precedence ladder runs before the label matrix: a
non-OK upstream result, inconsistent inputs, an unknown scale, nothing observed,
or a zero-competitor claim under low coverage each yield `insufficient_evidence`
rather than a misleading label — so *"zero competitors + sparse coverage"* is
never read as *"underserved"*.

`assess_market()` is pure (no I/O, clock, RNG or LLM — `tests/test_market_purity.py`).
`assessment_data_confidence` is `min(2B, 2C) × tier-penalty`, is separate from the
label, and **can never change it**. There is **no numeric market/opportunity
score** — that is Phase 3 (`CLAUDE.md` §12). See
[`docs/phase-2d.md`](docs/phase-2d.md). Tunables (thresholds, the label matrix,
message templates) live in `market/assessment_config.py`.

## Phase 3 — Business Opportunity & Pivot Engine

**Input:** a location + radius + the entrepreneur's proposed business, and an
`EntrepreneurProfile` (liquid cash, owned assets, trade experience — all
user-provided and unverified).
**Output:** an `OpportunityAnalysisResult` — a curated shortlist of candidate
businesses (plus the proposed one) each scored **0–100** and ranked, with a
**stance**: `proposed_is_best` / `alternative_materially_better` (with a named
`recommended_pivot`) / `alternatives_comparable` / `no_proposal_to_compare` /
`no_recommendation`.

Phase 3 calls the Phase 2A→2B→2C→2D pipeline once per candidate over **one**
union Overpass fetch — it re-queries nothing. The score decomposes into named
`ScoreComponent`s: `market_opportunity` (0.60, the Phase 2D label **alone**),
`asset_fit` (0.25, owned assets vs a curated per-category relevance table) and
`experience_fit` (0.15, reusing `relationship_for`); missing components
renormalise visibly. Ranking and the pivot recommendation are gated on the 2D
label lattice, `evidence_sufficient`, `capability_incomplete` and a
**capital-fit screen** — never on the number alone. The capital screen
(`CapitalFit`: unknown / affordable / stretch / out_of_reach) is an indicative
ordinal check, clearly captioned as *not* a financial assessment and *not* a
claim of impossibility — Phase 4 replaces it.

`score_opportunities()` is pure (`tests/test_market_purity.py`). It computes **no**
finance — project cost, EMI, DSCR, cash flow, loan structure, moratorium and
stress tests are Phase 4. Confidence (`market_data_confidence`,
`profile_completeness`) is reported once, unmultiplied, and changes no score. The
per-candidate data-coverage confidence is recomputed in
`discovery/opportunity_acquisition.py` so a union fetch does not inflate a
zero-competitor candidate into `underserved`. See
[`docs/phase-3.md`](docs/phase-3.md); tunables (the candidate universe, weights,
`asset_relevance`, `capital_bands`, caveats) live in
`market/opportunity_config.py`.

Nothing beyond Phase 3 is implemented (no deterministic financial engine, RAG,
scheme routing, LLM orchestration, WhatsApp, DPR, Google Places, SensiBook).

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows:  .venv\Scripts\activate       Linux/macOS:  source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env        # optional; every setting has a working default
```

Set a real contact address in `VYAPAR_USER_AGENT` before running against the
public OSM services (their usage policy expects it).

## CLI

```bash
python scripts/discover_businesses.py \
    --location "Bhagwanpur, Bihar" --category grocery --radius 8
```

Options: `--json` (full `DiscoveryResult` as JSON), `--db` (persist to
`VYAPAR_DB_URL` instead of memory), `--max-display N`, `--list-categories`,
`--log-level`. Radius is in **kilometres** on the CLI; metres everywhere inside.

Exit code is `0` only when the status is `ok`.

If the location is ambiguous (there are several "Bhagwanpur"s in Bihar), the CLI
prints the numbered candidates and exits without guessing. Either re-run with a
more specific string (e.g. `"Bhagwanpur, Vaishali, Bihar"`) or add
`--candidate N` to pick one of those candidates by its listed number — its
coordinates are then used directly, with no second geocoding request:

```bash
python scripts/discover_businesses.py --location "Sangareddy, Telangana" \
    --candidate 1 --category grocery --radius 8
```

An out-of-range `--candidate` is a clear error (exit `2`); candidate 1 is never
assumed.

Add `--competitors` to also run Phase 2A on the results:

```bash
python scripts/discover_businesses.py \
    --location "Hajipur, Vaishali, Bihar, 844101" --category grocery --radius 5 \
    --competitors --subtype pulses
```

`--subtype KEYWORD` (repeatable) and `--proposed "pulses grocery store"` (free
text, overrides `--category` for the classification only) refine the proposed
business. With `--competitors --json` the CLI emits the `CompetitorAnalysisResult`.
Exit code is `2` if the proposed business could not be mapped to a category.

Add `--metrics` (implies `--competitors`) to also compute Phase 2B competition
metrics — counts, nearest / average / median distances, distance-band counts, a
spatial density, and a competition signal:

```bash
python scripts/discover_businesses.py \
    --location "Hajipur, Vaishali, Bihar, 844101" --category grocery --radius 5 \
    --metrics
```

With `--metrics --json` the CLI emits the `CompetitionMetricsResult` (JSON
precedence: metrics → analysis → discovery).

Add `--demand` to also gather Phase 2C local-demand signals; combine with
`--metrics` for the competitors-per-1,000-residents ratio:

```bash
python scripts/discover_businesses.py \
    --location "Hajipur, Vaishali, Bihar, 844101" --category grocery --radius 5 \
    --metrics --demand
```

With `--demand --json` the CLI emits the `DemandSignalsResult` (JSON precedence:
demand → metrics → analysis → discovery).

Add `--assess` (implies `--metrics --demand`) to also run Phase 2D — fuse the
competition and demand evidence into an overall market label:

```bash
python scripts/discover_businesses.py \
    --location "Bhagwanpur, Vaishali, Bihar" --category grocery --radius 8 --assess
```

With `--assess --json` the CLI emits the `MarketAssessmentResult` (JSON
precedence: assessment → demand → metrics → analysis → discovery).

Add `--opportunity` to run Phase 3 — score & rank a curated shortlist of
candidate businesses (plus the proposed one) for this location and the
entrepreneur's resources. It widens the Overpass fetch to one union query
covering every candidate. `--cash INR`, `--asset KIND` (repeatable) and
`--experience CATEGORY` (repeatable) supply the profile:

```bash
python scripts/discover_businesses.py \
    --location "Bhagwanpur, Vaishali, Bihar" --category grocery --radius 8 \
    --opportunity --cash 650000 --asset storefront --asset vehicle \
    --experience dairy
```

With `--opportunity --json` the CLI emits the `OpportunityAnalysisResult` (JSON
precedence: opportunity → assessment → demand → metrics → analysis → discovery).
Phase 3 outcomes are all valid analyses, so `ok` and `no_evidence` both exit `0`.

## Tests, lint, types

```bash
pytest            # 369 unit tests, fully offline (all HTTP mocked with respx)
ruff check .
ruff format --check .
mypy              # checks src/vyaparsarathi
```

## PostgreSQL / PostGIS (optional)

Phase 1 runs on SQLite (or pure in-memory) with radius filtering done in Python.
To use Postgres instead:

```bash
pip install -e ".[postgis]"
export VYAPAR_DB_URL="postgresql+psycopg://user:pass@localhost:5432/vyaparsarathi"
python scripts/discover_businesses.py --location "..." --category grocery --radius 8 --db
```

The schema (`businesses`, `source_records`) is created automatically. Swapping the
plain lat/lon columns for a PostGIS `geography` column + GiST index is a
contained follow-up behind the same `BusinessRepository` interface — see
[`docs/phase-1.md`](docs/phase-1.md).

## Layout

```
src/vyaparsarathi/
  config/         settings (env, prefix VYAPAR_)
  models/         Pydantic contracts + BusinessCategory / SourceName taxonomy;
                  demand.py (Settlement, PopulationRecord, DemandEvidence);
                  profile.py (EntrepreneurProfile), opportunity.py (seam)
  categories/     OSM tag <-> internal vocabularies (business + place/activity)
  geocoding/      Nominatim geocoder + disambiguation
  sources/osm/    Overpass QL builder, HTTP client (mirror fallback), parse,
                  business adapter + demand (places.py) fetch
  sources/census/ Census 2011 village extract loader (offline reference data)
  normalization/  raw OSM element -> NormalizedBusiness / Settlement; text
  dedup/          conservative, provenance-preserving deduplication
  database/       BusinessRepository + in-memory and SQLAlchemy implementations
  discovery/      Phase 1 orchestrator; demand_acquisition.py (Phase 2C, impure);
                  opportunity_acquisition.py (Phase 3, impure)
  market/         Phase 2A classifier, 2B metrics, 2C demand engine, 2D market
                  assessment, Phase 3 opportunity/pivot engine (all pure — no
                  I/O; see tests/test_market_purity.py)
  utils/          haversine, HTTP retry/backoff, file cache, logging, time
data/demand/      Census 2011 village extract (csv.gz) + SOURCES.md
scripts/          discover_businesses.py (CLI, Phase 1 + 2A + 2B + 2C + 2D + 3);
                  build_census_dataset.py (one-off census ETL)
tests/            unit tests + tests/fixtures/{osm,nominatim,census}
docs/             design notes
```
