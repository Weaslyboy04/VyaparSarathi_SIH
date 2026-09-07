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
claim of impossibility. Replacing it with Phase 4's real project-cost-based
screen is deferred (see [`docs/phase-4.md`](docs/phase-4.md), "Phase 4 →
Phase 5") to avoid re-opening approved Phase 3 behaviour.

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

## Phase 4 — Deterministic Financial Engine

**Input:** a `FinancialPlanInput` — project cost lines, working-capital
assumptions, a revenue driver, operating costs, and a financing structure
(promoter cash, other funds, an optional loan). Every figure is a typed
`FinancialInput` tagged `user_provided` / `sourced` / `assumed` /
`calculated`, with a rationale required for `assumed` values. There is no
default anywhere for revenue, price, margin, a cost line, a rate, a tenure,
or a moratorium treatment — omitting one yields
`INSUFFICIENT_FINANCIAL_EVIDENCE` naming it, never a fabricated number.
**Output:** a `FinancialAssessmentResult` — project cost, working capital, a
revenue schedule, break-even, a full EMI/amortisation/moratorium schedule,
monthly cash flow, DSCR (cash-basis, no depreciation or tax), seven stress
scenarios, and a `FinancialFeasibilityStatus` (`feasible` /
`feasible_with_stretch` / `financing_gap` / `cash_flow_stress` /
`unserviceable` / `insufficient_financial_evidence`) with the exact rung and
finding that decided it.

`assess_financials()` is pure (`tests/test_finance_purity.py`); money is
`Decimal`, quantised to paise, never `float`. It computes **no** market or
opportunity information — no OSM data, no market label, no Phase 3 score —
and Phase 3 is unmodified: `finance/fit.py::to_financial_fit()` populates the
already-declared `FinancialFitInput` seam
(`score_opportunities(..., financial_fit=...)`), and a business can be
`underserved` at a high Phase 3 score and `UNSERVICEABLE` here — the two
measurements are independent and neither implies the other. See
[`docs/phase-4.md`](docs/phase-4.md); tunables (contingency %, DSCR
thresholds, stress-test deltas, fixed caveats) live in
`finance/finance_config.py`.

## Phase 5 — Knowledge / Evidence Layer

**Input:** a `ParameterQuery` naming the parameters wanted (interest rate,
tenure, moratorium, a statutory fee, a sector benchmark, ...) plus category /
state / district / scheme / loan amount / an explicit `as_of` date (never
`date.today()`). **Output:** a `FinanceKnowledgeEvidence` — one
`ParameterResolution` per requested name (`resolved` / `no_evidence` /
`conflicting` / `stale_only` / `conditions_unresolved`, each with a
confidence and full citation), plus any retrieved passages for display.

Every committed `SourcedParameter` row must survive three checks before it
can reach a plan: its `value_token` must occur in its own `evidence_quote`,
`normalize_value(value_token, normalization)` must equal `value`, and (at
load time) `evidence_quote` must occur verbatim in the chunk it cites — a row
failing any of the three is dropped and counted, never kept "just in case".
The resolver (`knowledge/resolver.py`) never consults a retriever: a value is
chosen purely by a fixed precedence ladder (source tier, then applicability
specificity, then recency) over the registry, so a retrieval ranking can
degrade what's shown *beside* a number, never the number itself.

`knowledge/plan_binding.py::bind_sourced_inputs()` /
`build_loan_terms()` are the **only** functions that construct a
`FinancialInput` or import Phase 4's plan models
(`tests/test_knowledge_purity.py`) — the seam `docs/phase-4.md` declared:
resolved rate/tenure/moratorium bind into `LoanTerms`, a statutory fee
becomes a new `CostLine`, a benchmark binds into `OperatingCostInput` /
`WorkingCapitalInput` when the field isn't already set — and four parameters
(`promoter_margin_pct`, `subsidy_pct`, `loan_ceiling_inr`,
`security_deposit_months`) bind **nowhere**, on purpose: converting them
would need Phase 4 arithmetic this phase must never perform. A field that
already carries any value (`user_provided`/`assumed`/an earlier `sourced`
one) is never overwritten. With an empty corpus, binding changes nothing —
`assess_financials(bind_sourced_inputs(plan, evidence).plan)` equals
`assess_financials(plan)` byte-for-byte
(`tests/test_knowledge_to_finance.py`).

The corpus ships **empty** — `data/knowledge/` holds zero real documents; the
machinery is exercised end to end against a synthetic, visibly-fictional
fixture corpus (`tests/fixtures/knowledge/`, see its README) and the shipped
demo (`scripts/phase5_demo.py`). Ingesting real government documents is a
separate, ongoing operator task (`data/knowledge/SOURCES.md`,
`scripts/build_knowledge_corpus.py` + `scripts/build_parameter_registry.py`),
not a code change. See [`docs/phase-5.md`](docs/phase-5.md), including its
unsupported-parameter register; tunables (tier weights, freshness decay,
applicability penalties) live in `knowledge/knowledge_config.py`.

## Phase 6 — Application Backend + LLM Orchestration

**Input:** a channel-neutral `MessageRequest` (`app/dto.py`) — structured slot
updates today, free text tomorrow when an LLM provider is configured.
**Output:** an `AdvisoryReply` — plain-text lines, numbered choices, and a
`Narrative` that records, per section, whether it was rendered deterministically
or by an LLM.

`app/service.py::AdvisoryService` is the one entry point a channel calls; it owns
session lifecycle and persistence (`database/session_repository.py`, in-memory or
SQLite) and drives one turn of `llm/orchestrator.py::run_turn` per message. That
function applies the turn's deltas, invalidates any step whose declared inputs
changed (`conversation/artifacts.py`, SHA-256 over inputs, never outputs), and
runs the 15-node step DAG (`conversation/workflow.py`) as far as it structurally
can — `llm/tools.py::STEP_RUNNERS` is the **only** place in the repository that
calls a Phase 1-5 engine, using every real signature verbatim.

The LLM is confined to two checked jobs: it may point at a substring of the
user's own message as a number (`SourcedParameter.value_token`, reused from
Phase 5) and it may supply a raw business phrase (never a category —
`market/proposed.py::resolve_proposed_business` owns that mapping). Neither the
recommendation, a market metric, nor a financial figure is ever produced by the
model. `llm_enabled=False` is a fully supported mode: with no LLM provider
configured at all, `AdvisoryService` still runs the whole pipeline to a
recommendation from structured input (`tests/test_app_service.py` never
constructs an `LlmProvider`).

`conversation/recommendation.py::combine()` is a fully enumerated 5×6 = 30-cell
table over Phase 3's `Stance` and Phase 4's `FinancialFeasibilityStatus`
(`tests/test_recommendation_combiner.py` asserts every cell). See
[`docs/phase-6.md`](docs/phase-6.md) for the DAG's dependency subtleties (why
`DEMAND_EVIDENCE` survives a category change, why `FINANCIAL_FIT ↔ OPPORTUNITY`
is not a cycle), the grounding check's guarantees and stated limits, and what is
deliberately deferred (LLM-authored narrative is implemented and tested standalone
but not yet wired into `send_message`; every reply today is the deterministic
renderer). Run the scripted, offline, two-transcript demo:
`python scripts/phase6_demo.py`.

Nothing beyond Phase 6 is implemented (no WhatsApp transport, no DPR rendering,
no Google Places, no SensiBook).

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

Phase 4 has no flag on this CLI — a real-location run has no financial
inputs, and inventing them to fill the gap is exactly what Phase 4 forbids.
Its own demo script runs six fixture-based scenarios end to end instead:

```bash
python scripts/phase4_demo.py          # all six
python scripts/phase4_demo.py 3        # one (short tenure + high rate -> unserviceable)
```

Every figure in every demo is an `assumed` `FinancialInput` whose rationale
says so explicitly — none is a market survey, a quotation, or a benchmark.

## Tests, lint, types

```bash
pytest            # unit tests, fully offline (all HTTP mocked with respx)
ruff check src tests scripts
ruff format --check src tests scripts
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
                  profile.py (EntrepreneurProfile), opportunity.py (seam);
                  finance.py (FinancialInput/InputKind, FinancialPlanInput);
                  knowledge.py (DocumentRecord/DocumentChunk), parameters.py
                  (SourcedParameter, ParameterQuery, FinanceKnowledgeEvidence)
  categories/     OSM tag <-> internal vocabularies (business + place/activity)
  geocoding/      Nominatim geocoder + disambiguation
  sources/osm/    Overpass QL builder, HTTP client (mirror fallback), parse,
                  business adapter + demand (places.py) fetch
  sources/census/ Census 2011 village extract loader (offline reference data)
  sources/knowledge/ FileCorpusStore — reads the committed knowledge corpus
  normalization/  raw OSM element -> NormalizedBusiness / Settlement; text
  dedup/          conservative, provenance-preserving deduplication
  database/       BusinessRepository + in-memory and SQLAlchemy implementations;
                  session_repository.py (Phase 6 conversation persistence:
                  in-memory + SQLite)
  discovery/      Phase 1 orchestrator; demand_acquisition.py (Phase 2C, impure);
                  opportunity_acquisition.py (Phase 3, impure);
                  knowledge_acquisition.py (Phase 5, impure)
  market/         Phase 2A classifier, 2B metrics, 2C demand engine, 2D market
                  assessment, Phase 3 opportunity/pivot engine (all pure — no
                  I/O; see tests/test_market_purity.py)
  finance/        Phase 4 financial engine — costs, operations, debt, cashflow,
                  dscr, pipeline, stress, assessment (all pure except fit.py,
                  the only module importing market/; see
                  tests/test_finance_purity.py)
  knowledge/      Phase 5 evidence layer — parameter_spec, resolver,
                  confidence, tokenize/lexical/retrieval (all pure except
                  plan_binding.py, the only module importing models/finance's
                  plan models; see tests/test_knowledge_purity.py)
  conversation/   Phase 6 — PURE: slots/provenance, the 15-node step DAG,
                  cascade invalidation, the recommendation combiner, the
                  evidence bundle, deterministic rendering, grounding (see
                  tests/test_conversation_purity.py)
  llm/            Phase 6 — the only package allowed to execute a StepId
                  (tools.py); provider edge, prompts, structured-output
                  parsing, turn orchestration (see tests/test_llm_leaf.py)
  app/            Phase 6 — channel-neutral backend: AdvisoryService, DTOs,
                  runtime wiring (no HTTP server; Phase 7 adds transport)
  utils/          haversine, HTTP retry/backoff, file cache, logging, time
data/demand/      Census 2011 village extract (csv.gz) + SOURCES.md
data/knowledge/   knowledge corpus (documents/chunks/parameters, ships empty)
                  + SOURCES.md
scripts/          discover_businesses.py (CLI, Phase 1 + 2A + 2B + 2C + 2D + 3);
                  build_census_dataset.py (one-off census ETL);
                  phase4_demo.py (six fixture-based financial scenarios);
                  build_knowledge_corpus.py / build_parameter_registry.py
                  (Phase 5 ETL: chunk, propose, verify);
                  phase5_demo.py (fixture-corpus evidence -> Phase 4 demo);
                  phase6_demo.py (two offline transcripts through
                  AdvisoryService: empty corpus, then the fixture corpus)
tests/            unit tests + tests/fixtures/{osm,nominatim,census,knowledge}
docs/             design notes
```
