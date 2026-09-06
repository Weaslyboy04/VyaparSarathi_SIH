# CLAUDE.md — VyaparSarathi

Permanent project context and engineering constitution for Claude Code.
**Read this file first, every session**, then inspect the actual repository before
acting. This document describes the *long-term target system*; only a small part of it
is built at any given time. **Section 26 states the current phase — that is what you
work on.** Everything past the current phase is background, not a to-do list.

How to read this file:
- Sections 1–3 are *why* the project exists and the rules that never bend.
- Sections 4–23 are the *target design*, engine by engine. Most of it is unbuilt.
- Sections 24–33 are *how we work*: security, phases, repo layout, testing,
  conventions.
- Concrete, implementation-influencing decisions (libraries, thresholds, endpoints,
  units) are marked **[decision]**; values you may tune later are marked
  **[tunable]**.

---

## 1. Project identity

- **Project name:** VyaparSarathi ("Vyapar" = business/trade, "Sarathi" = charioteer /
  guide).
- **Smart India Hackathon problem statement:** **SIH26091**.
- **Theme:** AI-Driven Hyper-Local Business Advisory and Financial Structuring
  Assistant for Rural Micro-Entrepreneurs.

**In simple terms:** a WhatsApp assistant that a rural entrepreneur can message in
plain language ("I have ₹6.5 lakh and want to open a pulses grocery store in
Bhagwanpur, Bihar") and get back honest, local, evidence-backed advice on whether that
business will work there, what it really costs, how much loan is sensible, whether the
loan can be repaid, and what a better option might be — ending in a bank-ready project
report.

**In technical terms:** a multi-engine decision-support system. A conversational LLM
layer handles language and orchestration; specialized deterministic services own
geospatial business discovery, market/competition analysis, opportunity scoring,
financial modelling, and scheme/knowledge retrieval. The LLM composes explanations
*from* these structured results and retrieved evidence — it does not generate the
facts.

### The knowledge-to-capital gap

Rural youth can access concessional, government-backed micro-loans (MUDRA, PMEGP,
PM-FME, state schemes, SHG/bank credit, etc.). Capital is not the bottleneck —
*informed deployment of capital* is. Businesses fail because entrepreneurs:

- choose saturated trades based on anecdote ("my cousin's shop does well");
- underestimate working-capital needs and run out of cash in month 3;
- misunderstand debt: EMI size, moratorium meaning, repayment timing;
- never test whether the enterprise survives weak sales, cost spikes, or a lean season;
- cannot tell a viable local opportunity from a crowded one.

VyaparSarathi is a **hyper-local enterprise advisor that runs before capital is
deployed**, so that unsuitable businesses are caught before they are funded and viable
local opportunities are surfaced.

### Target users

- **Primary:** rural micro-entrepreneurs and first-time founders (often youth),
  typically applying for or considering a scheme-backed loan up to ~₹10–25 lakh, with
  limited formal financial literacy and patchy connectivity (hence WhatsApp, hence
  voice and code-mixed language).
- **Secondary (intermediaries who operate the tool on their behalf):** SHG members,
  bank business-correspondent (BC) agents, block-level officials, CSC operators,
  NGO / State Rural Livelihood Mission staff.

### Primary user problem

The user must decide *what* enterprise to start and *how much* to borrow, and must
convince a bank it is viable — with almost no local market data, no financial model,
and no way to test the plan against a bad season. VyaparSarathi supplies all three.

### What VyaparSarathi is

- A pre-capital advisory + financial-structuring tool inside WhatsApp.
- A generator of an explainable recommendation and a structured **Detailed Project
  Report (DPR)** suitable for bank / government handoff.

### What VyaparSarathi is NOT

- Not a lender, loan marketplace, or guarantor. It does not disburse or approve loans.
- Not a generic chatbot; not a "chat with a PDF" demo.
- Not a national business directory. Its data is a partial, sourced sample — never
  presented as the complete set of businesses in a village.
- Not a replacement for official scheme sanction rules or a bank's credit appraisal —
  it prepares and pressure-tests the case.
- Not a financial advisor of record; outputs are decision support, clearly captioned
  with assumptions and confidence.

---

## 2. Core product vision — the full user journey

Long-term target. Trigger message (WhatsApp, may be voice or mixed language):

> "I live in Bhagwanpur, Bihar, I have ₹6.5 lakh and I want to open a pulses grocery
> store."

1. **Understand & extract** — LLM parses intent and structured fields: location text,
   available capital, proposed business, any assets/experience mentioned. Missing
   fields are asked for conversationally.
2. **Resolve location** — geocode "Bhagwanpur, Bihar" to a specific place +
   lat/lon + admin hierarchy (district, block, village). Disambiguate if multiple
   matches (there are several Bhagwanpurs).
3. **Discover nearby businesses** — query business-data sources within a radius
   (MVP: fixed radius, e.g. 5–10 km) around the resolved point.
4. **Normalize & deduplicate** — map every source record into one internal business
   model; merge the same real shop appearing in multiple sources (conservatively).
5. **Identify competitors** — filter discovered businesses to those relevant to the
   proposed category (direct + adjacent substitutes).
6. **Measure the market** — competitor count, nearest-competitor distances, density
   per catchment, saturation signal, confidence given data coverage.
7. **Read local demand** — population/households, surrounding villages and market
   centres, road access, agricultural/livestock/economic indicators as demand proxies.
8. **Evaluate the proposed business** — combine demand, competition, access, capital
   fit and assets into an explainable opportunity score with reasons.
9. **Generate alternatives / pivots** — score a handful of adjacent businesses for the
   same location and capital; surface those that score materially higher and say why.
10. **Capture entrepreneur assets** — cash, storefront, land, livestock, vehicle,
    equipment, existing business, experience, skills, risk tolerance.
11. **Build financial projections** — startup cost, fixed assets, opening inventory,
    working capital, revenue, COGS, operating expenses, monthly cash flow.
12. **Structure financing** — required loan, applicable scheme(s), interest, tenure,
    moratorium, EMI, promoter contribution vs margin rules.
13. **Stress test & moratorium analysis** — base / low-sales / high-cost / seasonal /
    slow-ramp scenarios; simulate the moratorium and the first post-moratorium EMIs;
    compute DSCR and the liquidity buffer; name the exact condition that breaks it.
14. **Retrieve knowledge** — pull scheme guidelines, eligibility, licensing/compliance
    (FSSAI, shops & establishment, GST thresholds, weights & measures, etc.) relevant
    to this business, state and scheme, with citations.
15. **Produce an explainable recommendation** — proceed / adjust / pivot, every claim
    traceable to structured data, a deterministic calculation, or a retrieved document.
16. **Generate the DPR** — a structured report (promoter, business, market, technical,
    financials, scheme, risks, annexures with sources) for bank / government handoff.

Throughout, the user receives plain-language explanations, not jargon. Each numbered
step maps to one logical component in §4 and (roughly) one development phase in §25.

---

## 3. Core design philosophy (non-negotiable)

### 3.1 The LLM is not the source of truth

The LLM **may**: understand natural language (including code-mixed Hindi/English and
transcribed voice); extract structured fields from user messages; choose which
tool/function/engine to call and with what arguments; sequence the conversation; turn
structured engine outputs + retrieved evidence into clear explanations; draft
narrative sections of the DPR from supplied numbers.

The LLM **must not** independently invent: nearby businesses or their attributes;
population, household, or economic figures; market statistics or competitor counts;
scheme rules, interest rates, margin requirements, or eligibility; EMI, DSCR,
break-even, or any financial projection; distances, densities, or catchment metrics;
any local fact it was not given.

If a required fact is not available from an engine or a retrieved document, the correct
behaviour is to say so and, if useful, state it as an explicit assumption — never to
fill the gap from the model's parametric memory.

### 3.2 Deterministic systems own facts and calculations

Structured databases, geospatial queries, and plain Python services own all factual
retrieval and all arithmetic. Given the same inputs they must produce the same
outputs. They are unit-tested. The financial engine in particular is pure, deterministic
Python with no LLM in the loop.

### 3.3 Evidence before explanation

The pipeline is: gather structured results → retrieve supporting documents → *then*
have the LLM explain. Never explanation first with facts reverse-justified.

### 3.4 Provenance everywhere

Every externally sourced fact carries where it came from (source, source ID, retrieval
time, and ideally the raw payload). Merges preserve all contributing provenance. See
§7, §23.

### 3.5 Uncertainty is explicit

Rural data is incomplete. Outputs carry confidence / data-coverage indicators. Never
convert an estimate into false precision ("exactly 1,240 households"; "there are 3
competitors" when the data only covers part of the area). Ranges and confidence bands
are preferred where the data is thin.

### 3.6 Contracts, not internals

Components talk only through documented structured objects (§4). One component never
imports another's private helpers, reads its database tables directly, or depends on a
source-specific payload shape. Any component may be swapped for a different
implementation as long as its input/output objects are unchanged.

---

## 4. System architecture

Logical components (loosely coupled; they communicate only through structured,
documented inputs/outputs — Pydantic v2 models, or plain dataclasses / dicts where a
model is overkill — never by sharing internal state or source-specific formats):

| Component | Responsibility |
|---|---|
| **WhatsApp / channel interface** | Send & receive messages, media, voice notes; map a phone number to a session; provider-specific code isolated here. |
| **Conversation / session layer** | Track dialogue state, collected fields, pending questions, consent; persist session; language handling. |
| **LLM orchestration layer** | Field extraction, tool/function selection & argument construction, response composition. Holds prompts and the tool schema. Stateless w.r.t. facts. |
| **Business data acquisition layer** | Fetch candidate businesses from each source (OSM/Overpass first). One adapter per source; adapters return raw source objects only. |
| **Geocoding / location layer** | Text → place → lat/lon + admin hierarchy; disambiguation; reverse geocoding. |
| **Business normalization layer** | Map any source's raw object into the internal business model; map source tags to the internal taxonomy. |
| **Entity resolution / deduplication layer** | Detect and merge records that refer to the same real business; conservative; provenance-preserving. |
| **Geospatial database** | Persist normalized businesses, places, population/road/market layers; radius, nearest-neighbour, density, and (later) catchment queries. PostgreSQL + PostGIS. |
| **Market analysis engine** | Competitors, counts, distances, density, saturation, demand proxies, market confidence. |
| **Opportunity / pivot engine** | Score the proposed business and a set of alternatives for this location + capital + assets; produce reasons. |
| **Financial engine** | Deterministic project cost, working capital, projections, financing, EMI, DSCR, cash flow, break-even, stress tests, moratorium simulation. |
| **Scheme / credit routing engine** | Given business, state, capital, promoter profile → applicable scheme(s) and their parameters (rates, margin, tenure, moratorium, ceilings), sourced from the knowledge base. |
| **Knowledge / RAG system** | Retrieve passages from official scheme docs, regulations, industry/compliance material with metadata + citations. |
| **Recommendation engine** | Combine market + opportunity + financial + scheme outputs into a proceed/adjust/pivot decision with a traceable rationale. |
| **DPR generator** | Assemble the structured report from all engine outputs + evidence; render to PDF/doc. |
| **Provenance / confidence layer** | Cross-cutting: attach and propagate source, calculation origin, and confidence through every result and into the final output. |

Rule: a component may be replaced or reimplemented as long as its structured contract
holds. No component reaches into another's internals.

### 4.1 Technology stack (current project decisions)

These are **[decision]s** for the project unless a later session changes them here.
Keep dependencies minimal; justify every new one.

- **Language:** Python **3.11+**. Type hints everywhere; code targets `mypy`
  (non-strict initially, tightening over time).
- **Models & config:** `pydantic` v2 for domain models and result objects;
  `pydantic-settings` for environment-driven configuration.
- **HTTP:** `httpx` (sync client is fine for MVP) with explicit timeouts and a small
  retry/backoff helper in `utils/`. No bespoke per-source HTTP stacks.
- **Business discovery (Phase 1):** OpenStreetMap **Overpass API** over plain HTTP
  (see §6.1). No heavyweight OSM wrapper library.
- **Geocoding:** OSM **Nominatim** for MVP (public instance: honour the usage policy —
  max ~1 request/second, a descriptive `User-Agent`, cache results). Interface is
  abstract so a self-hosted Nominatim / Photon / commercial geocoder can replace it.
- **Geospatial math (MVP):** a haversine implementation in `utils/geo.py`
  (see §4.2). `shapely` only if/when polygon work appears.
- **Geospatial store (target):** PostgreSQL **15+** with **PostGIS 3+**, accessed via
  `SQLAlchemy` 2.x + `GeoAlchemy2` + `psycopg` (v3). For very early local dev an
  in-memory / SQLite repository is allowed **behind the same repository interface**.
- **Text matching / dedup:** `rapidfuzz` for name similarity; `unidecode` (plus,
  optionally, `indic-transliteration`) for `normalized_name`.
- **Testing:** `pytest`, `pytest-mock`; `respx` or `responses` to mock HTTP; fixtures
  under `tests/fixtures/<source>/`.
- **Tooling:** `ruff` (lint + format), `mypy`. Stdlib `logging` (structured/JSON
  formatter) — no print-debugging left in committed code.
- **Later phases:** vector store for RAG (choice deferred to Phase 5 — evaluate
  `pgvector` first to avoid a second datastore); an LLM client library and a WhatsApp
  provider SDK are chosen in Phases 6–7, not before.

### 4.2 Coordinate and unit conventions

- Coordinates are **WGS84 decimal degrees**, stored as `latitude` then `longitude`
  (in that order in field names and function signatures). Never "lng"; use `longitude`.
- **Internal distances and radii are metres.** The user may say "5 km"; convert at the
  boundary. Store the query radius in metres.
- Money is **Indian rupees**, integer paise avoided for MVP — use `float`/`Decimal`
  rupees but round for display; the financial engine (§15) decides precisely and
  records units on every result object. Lakh/crore only in user-facing text.
- Haversine uses Earth radius **6 371 008.8 m** (mean radius); document any switch to
  a projected/geodesic method.
- Timestamps are **UTC**, timezone-aware ISO-8601.

---

## 5. Data architecture — four distinct kinds of information

Keep these separated in code, storage, and in how results are labelled to the user.

1. **Structured operational data** — businesses, coordinates, addresses, roads, market
   centres, villages, population/households, business categories, competitor counts,
   computed distances. Lives in a relational DB (**PostgreSQL / PostGIS** where spatial
   operations are involved). Queried, not retrieved by similarity.
2. **Unstructured knowledge** — scheme guidelines, government PDFs, regulations,
   circulars, industry reports, sector business guides, licensing/compliance
   documents. Lives in the **knowledge / RAG system** (vector store + metadata).
3. **Calculated information** — competitor density, opportunity score, EMI, DSCR,
   cash-flow series, break-even, stress-test and moratorium results. Produced by
   application logic (deterministic Python). Never produced by the LLM.
4. **AI-generated explanation** — the final natural-language narrative. Derived from
   (1)–(3) + retrieved evidence. Always distinguishable from the evidence it rests on
   (e.g. explanations reference the underlying result objects / citations).

Never let one kind leak into another's store: business rows do not go in the vector
DB; scheme PDFs are not parsed into "facts" the LLM recites without a citation;
calculated numbers are not persisted as if they were source facts.

---

## 6. Business data strategy

**There is no single reliable national database of every village shop in India.** The
system combines sources, each partial, behind one normalized model.

Preferred order and characteristics:

1. **OpenStreetMap / Overpass** — free, global, no API key, rich tags, good in many
   towns; **coverage is uneven and often sparse in small villages**; data can be
   stale; contributor-dependent. *This is the Phase 1 source.*
2. **Google Places (or another appropriately licensed place/business API)** — better
   coverage of active commercial POIs and names/ratings in many areas; **paid, quota
   and licensing constraints, terms restrict storage/redisplay**; still incomplete for
   informal rural micro-businesses.
3. **Government / open-data sources** — census & economic-census aggregates, district
   statistics, GST/Udyam where accessible, state datasets. Strong for
   population/economic context; **not a shop-level directory** of informal businesses.
4. **Licensed / approved external datasets** — sector or regional datasets obtained
   with permission; integrate as additional adapters.
5. **Web crawling** — only where APIs/datasets do not exist and it is legally and
   technically permitted (see §21). Secondary, last resort.

Explicit truths to encode everywhere:

- OSM can be incomplete; Google/other place DBs can also be incomplete.
- Government company/registration datasets (Udyam, MCA) do **not** represent informal
  rural micro-businesses — most kirana/dairy/tailoring shops are unregistered.
- **SensiBook** is useful for *registered-company* financials and benchmarking, **not**
  as a rural-shop directory (see §20).
- **Absence from the data does not mean the business does not exist.** Always report
  coverage/confidence alongside counts.

### 6.1 Overpass adapter specifics (Phase 1) **[decision]**

- **Endpoint:** configurable (`VYAPAR_OVERPASS_URL`), default
  `https://overpass-api.de/api/interpreter`. Keep a short **mirror list** (e.g.
  `https://overpass.kumi.systems/api/interpreter`) and fail over on 429/504/timeout.
- **Request:** HTTP `POST`, body is Overpass QL beginning
  `[out:json][timeout:60];`. Query candidate POIs with `nwr(around:<radius_m>,<lat>,<lon>)`
  filtered by the tag keys that matter for retail/services — at minimum `shop=*`,
  and selected `amenity` / `craft` / `office` values (pharmacy, fuel, restaurant,
  cafe, marketplace, veterinary, bank, etc.). End with `out center tags;` so ways and
  relations carry a representative `center` lat/lon.
- **Result handling:** elements come back as `node` / `way` / `relation`; take
  `lat`/`lon` for nodes and `center.lat`/`center.lon` for ways/relations. Drop
  elements with no usable coordinate (log the count). `source_id` is
  `"<type>/<id>"` (e.g. `node/123456789`).
- **Politeness:** one in-flight query at a time; exponential backoff
  (e.g. 2s, 4s, 8s, cap ~3 retries) on 429 / 504 / connection errors; honour any
  `Retry-After`. Descriptive `User-Agent` from config.
- **Caching:** cache raw responses keyed by the normalized query (rounded
  lat/lon + radius + tag set) with a TTL **[tunable]** (e.g. 7 days) so repeated demo
  runs and tests don't re-hit the API.
- **Limits:** cap the accepted radius (e.g. ≤ 25 km) and reject absurd inputs at the
  boundary with a clear error, not a silent clamp.
- **Failure:** on total failure (all mirrors down, malformed JSON) the discovery call
  returns an empty business list **with an explicit warning + zero coverage**, never a
  crash and never a fabricated result.

---

## 7. Business normalization

Every source maps into **one internal normalized business model**. Conceptual fields:

- `internal_id` — our stable identifier (UUID4).
- `name` — best human-readable name (may be `None` if the source has none).
- `normalized_name` — lowercased, transliterated (`unidecode`),
  punctuation/stopword-stripped, whitespace-collapsed form for matching.
- `category` — value from the internal taxonomy (§8); `unknown` if unmappable.
- `latitude`, `longitude` — WGS84 decimal degrees.
- `address` — free-text address if available.
- `source` — enum: `osm`, `google_places`, `govt`, `licensed:<name>`, `crawl:<name>`.
- `source_id` — the source's own ID (e.g. `node/123456789`, Places `place_id`).
- `raw` — the original source payload (JSON), retained for reprocessing and audit.
- `first_seen`, `last_updated` — timestamps (UTC).
- `data_quality` / `confidence` — completeness & reliability signal for this record
  (e.g. has name? has category? coordinate precision? single vs multi-source).
- `provenance` — list of contributing `(source, source_id, retrieved_at)` entries;
  length > 1 after a merge.
- `distance_m` — **not stored on the entity**; computed per query relative to the
  query point and attached to the *result* object (§11 / §26 output), because it is
  query-relative, not a property of the business.

Illustrative shape (not final):

```python
class NormalizedBusiness(BaseModel):
    internal_id: UUID
    name: str | None
    normalized_name: str
    category: BusinessCategory          # enum / Literal from the internal taxonomy
    latitude: float
    longitude: float
    address: str | None = None
    source: SourceName
    source_id: str
    raw: dict                            # original payload, kept verbatim
    first_seen: datetime
    last_updated: datetime
    data_quality: float                  # 0..1
    provenance: list[ProvenanceEntry]
```

Source-specific fields (OSM tag maps, Google `types`, opening-hours formats, etc.)
**must not leak** past the normalization layer. Downstream code sees only the model
above. This keeps engines source-agnostic and lets us add/remove sources freely.

---

## 8. Business category system

External sources use incompatible tagging (OSM `shop=convenience`, Google
`grocery_or_supermarket`, government NIC codes). VyaparSarathi maintains **its own
stable, extensible taxonomy**. Starter categories:

`grocery`, `dairy`, `pharmacy`, `restaurant`/`food_stall`, `hardware`,
`clothing`/`textiles`, `mobile_electronics`, `automobile_repair`, `tailoring`,
`agri_input` (seeds/fertiliser/pesticide), `food_processing`,
`livestock_services` (cattle feed, veterinary, poultry), `general_store`, `salon`,
`stationery`, `furniture`, `building_materials`, plus `unknown`.

Rules:

- Each source has its **own mapping table/module** (`categories/osm_map.py`,
  `categories/google_map.py`, …) that translates source tags → internal category.
  These mappings are the *only* place that knows source vocabularies.
- Unmapped source tags fall back to a coarse category or `unknown` and are **logged**
  (with the raw tag) for taxonomy expansion — never dropped silently.
- The taxonomy is a single enum/`Literal` set in `models/` (or `config/`); mapping
  modules import it, never redefine it.
- The taxonomy supports adding categories and (later) sub-categories without breaking
  stored data (store the string value, not an ordinal).

Illustrative OSM mapping fragment:

```python
OSM_TAG_TO_CATEGORY = {
    ("shop", "convenience"): "grocery",
    ("shop", "general"): "general_store",
    ("shop", "kiosk"): "general_store",
    ("shop", "dairy"): "dairy",
    ("amenity", "pharmacy"): "pharmacy",
    ("shop", "chemist"): "pharmacy",
    ("amenity", "fuel"): "automobile_repair",   # coarse; refine later
    ("shop", "hardware"): "hardware",
    ("shop", "agrarian"): "agri_input",
    ("craft", "tailor"): "tailoring",
    ("shop", "tailor"): "tailoring",
    # ... unmapped -> "unknown", logged
}
```

---

## 9. Entity resolution / deduplication

**Problem:** the same real business appears in multiple sources (and sometimes twice in
one). E.g. OSM "Maa Vaishno General Store" and Google "Maa Vaishno General Store"
≈120 m apart should become one logical business with two provenance entries.

Signals to combine:

- `normalized_name` similarity — `rapidfuzz` token-set ratio (0–100).
- Geographic proximity — haversine distance in metres (GPS noise is real).
- Category agreement (or compatible categories, e.g. `grocery`/`general_store`).
- Address similarity where present.
- Shared or cross-referenced source IDs.

Proposed decision rule **[tunable]** (single config block, documented, not magic
numbers in code):

- **Merge** when `name_similarity ≥ 87` **and** `distance_m ≤ 120` **and** category is
  same or compatible.
- **Uncertain bucket** (store the pair, do not merge) when
  `75 ≤ name_similarity < 87` **or** `120 < distance_m ≤ 200` with otherwise strong
  signals.
- **Distinct** otherwise.

Principles:

- **Conservative.** Merge only on strong combined evidence. A false merge destroys a
  real competitor; a missed merge only slightly inflates counts. When unsure, do not
  merge.
- No aggressive transitive merging; prefer pairwise decisions with a clear threshold
  and a review/uncertain bucket. If A~B and B~C but not A~C, do not silently form
  {A,B,C}.
- **Provenance is preserved** — the merged record keeps every contributing
  `(source, source_id, retrieved_at)`; `name` picks the best candidate (prefer the
  source with higher `data_quality`, then longer non-empty name) but originals stay
  in `raw` / `provenance`.
- Merges are **reversible** — store the merge decision (which records, which rule
  fired, when), not just the merged result.

---

## 10. Geospatial design

Location is the backbone of the product.

Target capabilities:

- Village/town-level **geocoding** with admin hierarchy and disambiguation
  (return *all* candidate matches with district/state so the LLM or user can choose).
- Storage of lat/lon for every business and place.
- **Radius search** (MVP), **nearest-business** queries, competitor distances.
- **Density** calculation (businesses of a category per km² and per 1,000 people).
- **Catchment areas** — MVP: fixed-radius; later: **travel-time / road-network
  isochrones** (roads are sparse and winding in rural areas, so straight-line radius
  overstates reach).

Persistence: **PostgreSQL + PostGIS** is the preferred geospatial store (`geography`
columns, `ST_DWithin`, `ST_Distance`, KNN `<->`). For very early local development an
in-memory / SQLite + haversine fallback is acceptable **but must sit behind the same
repository interface** (`BusinessRepository`, `PlaceRepository`) so the switch to
PostGIS changes one implementation class and nothing else.

Fixed-radius analysis is explicitly the MVP; travel-time catchment is a later
enhancement, not a Phase 1–2 concern.

---

## 11. Market intelligence engine

**Inputs:** resolved location; proposed business + category; discovered/normalized
businesses; population & households; surrounding villages and market centres; road /
accessibility data; local economic indicators; agricultural / livestock / resource
indicators.

**Outputs:** list of competitors (direct + adjacent); competitor count; nearest
competitors with distances; competitor density; market-saturation signal; demand
proxies; local opportunity indicators; **market-data confidence** (from source
coverage).

Key rule: **absence of competition does not prove demand.** A village with no pharmacy
may be too small to sustain one. Demand must be triangulated from multiple signals
(population, catchment including nearby villages, purchasing indicators, distance to
the nearest existing option, seasonality) — never from "no competitors found" alone.
Every metric is reported with the confidence implied by data coverage.

---

## 12. Opportunity / pivot engine

Purpose: not a yes/no verdict on the user's idea, but a **comparison across candidate
businesses** for the same location, capital and assets.

Example output:

```
Pulses grocery ................ 74/100
Cattle feed distribution ...... 82/100
Agri-produce trading .......... 79/100
```

Contributing factors (each an explicit, inspectable input): demand strength;
competition / saturation; accessibility & catchment; local resource availability;
required startup capital vs available; working-capital intensity; fit with the
entrepreneur's existing assets; fit with experience/skills; risk; resilience under
stress.

Requirements:

- Scores are **explainable** — every score decomposes into named factor contributions
  with the data behind each.
- Weights and thresholds are **configuration** in `config/` (documented, tunable), not
  magic numbers buried in code.
- The engine states *why* an alternative beats the proposed business in concrete terms
  ("competition is lower and local livestock activity is stronger"), and links to the
  underlying market metrics.

---

## 13. Entrepreneur profile

Information collected over the conversation (ask only what is needed, when needed):

- location; available **liquid cash**; existing **shop/storefront**; **land** (with
  rough area / ownership); **livestock** (type, count); **vehicle**; **equipment /
  machinery**; existing business or income; relevant **experience**; **skills**;
  intended business; expected scale; risk constraints / obligations.

Rules:

- **Liquid money and physical assets are tracked separately** and never silently
  summed.
- A physical asset does **not** automatically count as legally eligible promoter margin
  for a scheme — that is decided by scheme rules (§14, §18).
- Store the profile with the session; treat it as **user-provided (unverified)** data
  and label it as such in outputs and the DPR.

---

## 14. Asset-aware project model

Distinguish, and keep as separate quantities:

- **Cash contribution** — liquid money the promoter puts in.
- **Regulatory / scheme-required margin** — the promoter contribution a scheme
  *demands*, on its own terms (often must be cash / bank balance).
- **Existing assets** — storefront, land, equipment, vehicle, livestock already owned.
- **Project resources** — assets actually usable by this project (reducing spend).
- **Working-capital reserve** — cash deliberately held back to fund early operations.

Existing assets can **reduce actual startup expenditure** (no need to buy a shed you
own) **without necessarily satisfying the formal margin requirement**. The financial
engine models both the real cash need and the scheme-eligibility view, and reports them
distinctly. Scheme rules — retrieved (§18), not assumed — determine eligibility.

---

## 15. Financial engine

Deterministic, pure Python, fully unit-tested. No LLM performs authoritative financial
arithmetic — ever.

Eventual scope:

- **Project cost** — fixed assets, civil work, initial inventory, pre-operative
  expenses, contingency.
- **Working capital** — inventory holding, receivables, payables, operating-expense
  cushion for the ramp-up period.
- **Revenue model** — units × price × footfall/turnover, with a ramp-up curve and
  seasonality.
- **Costs** — COGS / gross margin by line; operating expenses (rent, power, salaries,
  transport, spoilage, misc).
- **Cash flow** — monthly inflows/outflows, opening/closing balance, minimum balance.
- **Financing structure** — promoter contribution vs loan; scheme parameters.
- **Loan mechanics** — amount, interest rate, tenure, **moratorium**, EMI (reducing
  balance), repayment schedule.
- **Coverage & viability** — **DSCR** (per year and average), repayment capacity,
  **break-even** (units & months), liquidity buffer.
- **Scenarios** — base + stress set (§16) and moratorium simulation (§17).

All rates, tenures, margins, and scheme ceilings are **inputs** (from the scheme engine
/ knowledge base or explicit assumptions), never hard-coded constants pretending to be
facts. Every result object records its inputs and assumptions. No `datetime.now()`, no
unseeded RNG, no network — see §28.

---

## 16. Stress testing

Every major recommendation is tested under scenarios, at minimum:

- **Base case** — expected assumptions.
- **Lower sales** — e.g. −20% / −30% volume.
- **Higher input costs** — e.g. +10% / +20% COGS or key expenses.
- **Seasonal demand decline** — a sustained low-season stretch.
- **Delayed revenue ramp-up** — break-even reached months later than planned.

For each scenario the engine reports whether the business keeps **positive liquidity**
and **adequate DSCR**, and — importantly — **names the exact condition that first
breaks it** ("cash goes negative in month 7 if sales are 25% below plan and the ramp is
2 months slower"). Combined/compound scenarios are supported.

---

## 17. Moratorium analysis

A moratorium defers **principal (and sometimes interest)** — it does **not** mean the
business has no costs during that period. The engine simulates the full timeline:

startup outlay → initial operating months → revenue ramp-up → ongoing operating
expenses → working-capital drawdown → the moratorium window → **EMI start date** →
post-moratorium debt service.

Central question: **"Will the business have enough cash flow when repayment begins?"**
Output includes the cash position at EMI start, the first-year post-moratorium DSCR,
and whether interest accrued during the moratorium is serviced or capitalised.

---

## 18. Knowledge base / RAG

**RAG is for unstructured official knowledge only.** It retrieves from: government
scheme documents and guidelines; policy documents and circulars; official eligibility
rules; industry / sector reports; sector business guidance; licensing & compliance
documents (FSSAI, Shops & Establishments, GST thresholds, weights & measures, trade
licences, pollution consent where relevant); other trusted reference material.

**RAG must NOT be the mechanism for:** counting nearby businesses; computing distances,
density, or catchment; calculating EMI or DSCR; determining competitor saturation;
resolving locations. Those come from structured systems and the financial engine.

The scheme/credit routing engine consumes RAG output to obtain scheme parameters; it
does not guess them. If a needed parameter is not retrievable, it is surfaced as a
missing input or an explicit assumption — never invented.

---

## 19. RAG requirements

- Chunk documents sensibly (section-aware); store rich **metadata** per chunk:
  `source`, `publisher`, `document_title`, `date`, `state`/`jurisdiction`, `scheme`,
  `business_sector`, `topic`, `url`/reference, `version` where applicable.
- **Prefer authoritative sources** (ministry / official bank / regulator sites) and
  rank them above secondary commentary; record which tier a chunk came from.
- Retrieval results carry citations back to document + section, and the final answer
  **retains enough provenance to show where each important claim came from**.
- Prefer hybrid retrieval (keyword + vector) and filtering by metadata (state, scheme,
  sector) over pure nearest-neighbour.
- **No generic vector-database dumping** — do not ingest arbitrary web text or the
  business-location data into the vector store and call it RAG.

---

## 20. SensiBook

Intended role: a source of **registered-company** information — company records,
financial statements, financial ratios, and **industry / peer benchmarking** — used to
sanity-check margins, cost ratios, and turnover assumptions in the financial engine.

Not: a rural-shop directory. Do **not** assume a given village kirana/dairy/tailoring
shop exists in SensiBook, and do not design discovery around it. Access it through its
**official / API mechanism** where legally and technically appropriate; avoid
indiscriminate scraping. Treat its data as one benchmarking input among several, with
provenance recorded like any other source.

---

## 21. Crawling policy

Crawling is a **secondary** acquisition method. Where an API or dataset exists, use
that instead.

Any crawler must:

- respect `robots.txt`, site terms, and usage restrictions where applicable;
- rate-limit and back off; identify itself honestly via `User-Agent`;
- cache responsibly and avoid re-fetching unchanged pages;
- retain **source URLs** and **retrieval timestamps**;
- normalize its output into the internal business model (§7);
- isolate all site-specific parsing in its own adapter module;
- **fail gracefully** when a site's structure changes (log, skip, alert — never crash
  the pipeline or emit garbage records).

No crawler-specific structure becomes part of the core domain model.

---

## 22. Confidence and data quality

Every market recommendation carries a **confidence / data-coverage** measure:

```
Market-data confidence: 82%
Sources: OpenStreetMap, Google Places, District statistics
```

- Confidence reflects *how well we can see the local market* (source coverage,
  freshness, agreement between sources, sample size) — **not** business viability.
- A recommendation can be **financially strong with low market-data confidence**, or
  financially weak with high confidence. Report the two independently; never let one
  mask the other.
- Low confidence is surfaced to the user with what would raise it (e.g. "verify with a
  local visit; OSM coverage here is sparse").

---

## 23. Provenance

Every important external fact is traceable end to end:

| Fact | Provenance recorded |
|---|---|
| A competitor business | `source = osm`, `source_id = node/12345`, `retrieved_at` |
| Population / households | `source = <official dataset name + year>` |
| Scheme margin / rate | `source = <official scheme document, section, version>` |
| EMI / DSCR / cash flow | `calculated_by = financial_engine`, inputs + assumptions attached |
| Recommendation narrative | `generated_by = LLM` from the above result objects + citations |

This four-way distinction — **source fact / retrieved rule / our calculation / AI
explanation** — must stay visible in internal data structures and be reflected in the
DPR's annexures.

---

## 24. Security and secrets

- API keys and credentials **only** in environment variables; loaded via
  `config/` (`pydantic-settings`). Never read `os.environ` ad hoc across the codebase.
- `.env` local and git-ignored; **`.env.example` committed** with placeholder keys and
  every required variable documented.
- **Never commit real credentials.** Never print secrets in logs, errors, or user
  messages.
- No credentials in source code, notebooks, fixtures, or test data.
- Sanitize and validate external/user input (WhatsApp text, location strings, uploaded
  files) before use; treat it as untrusted.
- Store user profile / conversation data with least privilege; do not expose one user's
  data to another.

Environment variables (grow this list as phases land; keep `.env.example` in sync):

```
VYAPAR_OVERPASS_URL=https://overpass-api.de/api/interpreter
VYAPAR_OVERPASS_MIRRORS=https://overpass.kumi.systems/api/interpreter
VYAPAR_NOMINATIM_URL=https://nominatim.openstreetmap.org
VYAPAR_HTTP_TIMEOUT_S=60
VYAPAR_USER_AGENT=VyaparSarathi/0.1 (contact: <email>)
VYAPAR_CACHE_DIR=.cache
VYAPAR_DB_URL=postgresql+psycopg://user:pass@localhost:5432/vyaparsarathi   # later phases
# GOOGLE_PLACES_API_KEY=...    # Phase 2+
# LLM / WhatsApp provider keys # Phase 6/7
```

---

## 25. Development phases

Staged roadmap. Each phase has an objective and an explicit "not yet".

| Phase | Objective | Must NOT prematurely implement |
|---|---|---|
| **0 — Foundation** | Repo skeleton, config, `CLAUDE.md`, base models, tooling, CI-friendly test setup. | Any engine logic. |
| **1 — Business data acquisition & storage** | location + category + radius → clean normalized nearby businesses; OSM/Overpass adapter; normalization; storage; basic dedup. | Market scoring, opportunity scoring, finance, RAG, LLM orchestration, WhatsApp, DPR. |
| **2 — Market / competitor analysis** | Competitor filtering, counts, distances, density, saturation, demand proxies, market confidence. | Opportunity scoring across alternatives, finance, RAG, channel. |
| **3 — Opportunity / pivot engine** | Explainable opportunity score for proposed + alternative businesses. | Financial modelling depth, DPR. |
| **4 — Deterministic financial engine** | Project cost, working capital, projections, financing, EMI, DSCR, cash flow, stress tests, moratorium. | Scheme auto-routing via RAG (use explicit inputs first), DPR rendering. |
| **5 — Knowledge base / RAG** | Ingestion, metadata, hybrid retrieval, citations; scheme parameter lookup. | Putting structured/geospatial facts into the vector store. |
| **6 — LLM orchestration / tool calling** | Field extraction, tool selection, evidence-grounded response composition. | Bypassing engines; letting the LLM compute or invent facts. |
| **7 — WhatsApp interface** | Inbound/outbound messaging, sessions, media/voice, provider adapter. | Business logic inside the channel layer. |
| **8 — DPR generation** | Structured report assembly + PDF/doc rendering with sourced annexures. | New analysis; DPR only composes existing engine outputs. |
| **9 — End-to-end integration & demo** | Wire the full loop; seed demo locations; scripted hackathon walkthrough; polish. | Rewrites of working modules; scope creep. |

Work only on the phase the user explicitly asks for.

---

## 26. Current phase

**Phase 1 — Hyper-Local Business Data Acquisition and Storage.**

First milestone:

- **Input:** location (text) + business category + radius.
- **Output:** a clean set of **normalized nearby business records**, each with: business
  name, standardized category, latitude, longitude, distance from the query point,
  source, source ID, and provenance / data-quality information.
- **First source:** OpenStreetMap / Overpass.
- **Later sources** (same normalized model, separate adapters): Google Places,
  government datasets, approved external data, crawler.

### 26.1 Phase 1 pipeline

`discovery/` orchestrates, in order:

1. **Geocode** the location text → one or more candidate places (lat/lon + admin
   hierarchy). If ambiguous, return the candidates; do not silently pick the first.
2. **Fetch** raw POIs from each enabled source adapter within the radius (Phase 1: OSM
   only). Adapters return raw source objects, nothing more.
3. **Normalize** each raw object → `NormalizedBusiness` (§7), mapping tags → internal
   category (§8). Log unmapped tags.
4. **Deduplicate** conservatively (§9), preserving provenance.
5. **Store** via the repository interface (in-memory/SQLite now, PostGIS later, §10).
6. **Assemble the result**: businesses with query-relative `distance_m`, sorted by
   distance, plus a coverage/confidence summary and any warnings.

Illustrative result container:

```python
class DiscoveryResult(BaseModel):
    query: DiscoveryQuery                 # echoed: location text, resolved point, radius_m, category filter
    resolved_place: ResolvedPlace         # what the geocoder chose (+ alternates)
    businesses: list[BusinessHit]         # NormalizedBusiness + distance_m
    sources_queried: list[SourceName]
    coverage: CoverageSummary             # counts per source, dedup merges, unmapped-tag count
    confidence: float                     # 0..1, data-coverage signal (NOT viability)
    warnings: list[str]                   # e.g. "Overpass mirror fallback used", "geocode ambiguous"
```

### 26.2 Phase 1 definition of done

- Given `("Bhagwanpur, Bihar", "grocery", 8 km)` the pipeline returns normalized
  businesses with correct categories, coordinates, per-result distances, source,
  `source_id`, and provenance — or an explicit "location ambiguous / no coverage"
  result. It never crashes and never fabricates a business.
- OSM adapter is fully unit-tested against **fixtures** (`tests/fixtures/osm/`);
  no test touches the network.
- Category mapping, `normalized_name` generation, haversine distance, and the dedup
  rule each have direct unit tests, including edge cases (empty result, single result,
  all-duplicates, missing coordinates, ambiguous location) and failure cases (timeout,
  4xx/5xx, malformed JSON, rate-limit).
- Repository interface has one working implementation; swapping it does not touch
  discovery/normalization code.
- `.env.example` lists every variable Phase 1 reads; `ruff` and `mypy` pass.

### 26.3 Out of scope for Phase 1

Do **not** implement RAG, the financial engine, WhatsApp, LLM orchestration,
opportunity/market scoring, or DPR generation unless the user explicitly asks. Basic
normalization and conservative deduplication are in scope; sophisticated market metrics
(density, saturation, demand proxies) are **Phase 2**.

---

## 27. Target repository structure

```
vyaparsarathi/
  CLAUDE.md
  README.md
  .env.example
  .gitignore
  requirements.txt          # or pyproject.toml
  src/vyaparsarathi/
    config/        # settings, env loading, constants, tunable weights & thresholds
    models/        # normalized domain models (business, place, profile, results), taxonomy enum
    sources/       # one adapter per data source (osm/, google/, govt/, crawl/); raw fetch only
    categories/    # per-source tag -> internal taxonomy maps (osm_map.py, google_map.py, ...)
    discovery/     # orchestrates geocode -> fetch -> normalize -> dedup -> store -> result
    database/      # repository interfaces + implementations, migrations (PostGIS)
    market/        # market intelligence engine            (Phase 2)
    finance/       # deterministic financial engine         (Phase 4)
    knowledge/     # knowledge base / RAG                    (Phase 5)
    llm/           # prompts, tool schema, orchestration     (Phase 6)
    channels/      # whatsapp and other interfaces           (Phase 7)
    dpr/           # DPR assembly + rendering                (Phase 8)
    utils/         # shared helpers (geo math, text normalization, http retry, logging)
  scripts/         # CLI entry points, data seeding, one-off tasks
  tests/           # unit + integration tests
    fixtures/      # real-shaped source responses (osm/, google/, ...)
  docs/            # design notes, data-source notes, decision log
```

Create a directory/module **only when the current phase needs it.** Do not scaffold the
whole tree up front. Phase 1 needs roughly: `config/`, `models/`, `sources/osm/`,
`categories/`, `discovery/`, `database/`, `utils/`, `tests/`.

---

## 28. Testing philosophy

- **Unit tests for all core logic**; the financial engine is tested to the number
  (fixed inputs → asserted EMI/DSCR/cash-flow values).
- **External APIs are mocked** in unit tests; store real-shaped responses as
  **fixtures** (`tests/fixtures/osm/…`, etc.). Capture fixtures once from a real call,
  then never hit the network in the unit suite.
- **Integration tests** for database/PostGIS operations, run against a real (possibly
  containerised) Postgres, kept separate from the fast unit suite and clearly marked.
- **No dependence on live services** for ordinary unit tests — they must pass offline.
- Test **edge cases** (empty results, single result, all-duplicates, missing
  coordinates, ambiguous location) and **failure conditions** (API timeout, 4xx/5xx,
  malformed payload, rate-limit) — the pipeline degrades, it does not crash.
- Deterministic engines must be reproducible: no wall-clock, no RNG without a fixed
  seed, no network. Inject clocks and identifiers where needed.

---

## 29. Claude Code working style

At the start of every session:

1. **Read this CLAUDE.md.**
2. **Inspect the current repository** — files, structure, existing modules, tests,
   `requirements.txt` / `pyproject.toml`. Never assume it is empty or unchanged from
   last session.
3. Identify **which phase** the user is asking about (default: §26).
4. **Work only on that phase.** Reuse existing code and contracts; do not rewrite
   working modules to restyle them.
5. Before a large implementation, **state the proposed files and changes** briefly and
   get alignment.
6. Implement **incrementally**; keep modules small and focused; use **type hints**.
7. **Run tests after meaningful changes**; add tests for new logic.
8. Do **not** build future phases or add speculative abstractions.
9. **Never fabricate data**; if something is unknown, say so and mark it an assumption.
10. Clearly state assumptions and limitations in code comments and in replies.
11. Keep the four-way distinction visible: **source fact / retrieved rule / our
    calculation / AI explanation.**
12. Preserve **provenance** through every transformation.

Code quality baseline: simple readable Python; validate inputs; handle external
failures gracefully and never swallow errors silently; secrets via env only; isolate
source-specific code behind the normalized model; avoid unnecessary dependencies; do
not over-engineer the MVP; document important assumptions.

---

## 30. Important "DO NOT" rules

- Do **not** use the LLM as a database or as a fact source.
- Do **not** use RAG to compute geospatial facts, counts, or distances.
- Do **not** use the LLM for authoritative financial calculations.
- Do **not** assume every rural business is registered or discoverable.
- Do **not** assume SensiBook (or any single source) contains local shops.
- Do **not** treat incomplete business data as complete ground truth, or report counts
  without coverage/confidence.
- Do **not** invent missing data — surface the gap.
- Do **not** silently substitute assumptions for official scheme rules; retrieve them,
  or mark the assumption explicitly.
- Do **not** hard-code credentials, or real API keys, anywhere.
- Do **not** hard-code scheme rates, margins, tenures, or ceilings as if they were
  facts — they are inputs.
- Do **not** over-engineer or pre-build future phases.
- Do **not** rewrite working modules unnecessarily.
- Do **not** create giant monolithic files; keep modules small and single-purpose.
- Do **not** couple source-specific API structures to the core domain models.
- Do **not** merge business records on weak evidence.
- Do **not** let `distance_m` (query-relative) be stored as a property of a business.

---

## 31. Example final user experience (target state)

> **User:** "I have ₹6.5 lakh and want to open a pulses grocery store in Bhagwanpur."

Conceptual system output (illustrative — not a promise of current functionality):

- **Local competition:** 6 grocery / general stores within 3 km; nearest 400 m; 2 also
  sell pulses in bulk. Density above the district norm for this population.
- **Demand assessment:** ~1,900 households in the catchment incl. 3 adjacent villages;
  weekly haat 6 km away; staple demand steady, price-sensitive.
- **Opportunity score:** Pulses grocery **61/100** — adequate demand, but crowded and
  low-margin.
- **Alternatives:** Cattle-feed & agri-input **82/100** (only 1 supplier in 8 km, high
  local livestock count); bulk pulses + light processing (dal milling) **74/100**.
- **Capital structure:** project cost ≈ ₹5.4 L; promoter margin required (scheme X)
  ≈ ₹0.8 L cash; loan ≈ ₹4.6 L @ ~11% / 5 yr / 6-month moratorium; working-capital
  reserve ₹0.9 L retained from the ₹6.5 L.
- **Financial viability:** base-case avg **DSCR 1.6**, break-even month 5.
- **Stress test:** cash turns negative in month 8 if sales are 25% below plan *and* the
  ramp is 2 months slower; otherwise survives.
- **Risks:** thin margins, price competition, inventory spoilage, seasonal cash dips.
- **Confidence:** market-data confidence 76% (OSM sparse here; Google + district stats
  used).
- **Recommendation:** proposed store is survivable but marginal; **pivot to
  cattle-feed / agri-input** or add dal milling for materially better returns at
  similar capital.
- **DPR:** generated, with all figures, assumptions, and sources in annexures.

---

## 32. SIH demonstration principle

The hackathon demo must show the **core loop end to end**:

**user idea → hyper-local evidence → market & opportunity analysis → deterministic
financial feasibility → explainable recommendation → DPR.**

It must visibly demonstrate that the system can **catch a poor business decision before
capital is committed**, and can **point to a better local opportunity**, with every
important claim traceable to a source, a calculation, or a retrieved rule.

The differentiator is **not** "we built a chatbot." It is:

> **VyaparSarathi combines hyper-local business intelligence, evidence-grounded
> official knowledge, and deterministic financial analysis to help rural entrepreneurs
> — and the schemes that back them — make better borrowing decisions.**

---

## 33. Engineering conventions

- **Naming:** `snake_case` for functions/variables/modules, `PascalCase` for classes,
  `UPPER_SNAKE` for constants. Coordinates are always `latitude` / `longitude`
  (never `lat`/`lng` in public signatures). Source names use the `SourceName` enum.
- **Module size:** keep files single-purpose and roughly under ~300 lines; split an
  adapter into `client.py` (HTTP) + `parser.py` (raw → intermediate) + `normalize.py`
  when it grows.
- **Errors:** raise typed exceptions (`GeocodingError`, `SourceUnavailableError`,
  `NormalizationError`); the discovery orchestrator catches them, records a warning,
  and returns a degraded result. Never `except: pass`.
- **Logging:** stdlib `logging` with a structured formatter; one logger per module
  (`logging.getLogger(__name__)`). Log unmapped category tags, mirror fallbacks, dedup
  merges, dropped coordinate-less records. Never log secrets or full user messages at
  INFO.
- **Config:** all thresholds, weights, radii caps, cache TTLs live in `config/` and
  are imported, not redefined. Mark each `# [tunable]`.
- **Determinism:** inject a clock and a UUID factory into anything that would otherwise
  call `datetime.now()` / `uuid4()` so tests can pin them.
- **Git:** work on a branch, never commit or push unless the user asks. Keep commits
  small and scoped to one concern. Commit-message footer:
  `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`.
- **Dependencies:** adding one requires a one-line justification in the commit / PR and
  an entry in `requirements.txt` / `pyproject.toml` with a version constraint.
- **Docs:** record non-obvious data-source quirks and design decisions in `docs/`
  (a short dated decision log), not only in code comments.
