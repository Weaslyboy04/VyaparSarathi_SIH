# Phase 3 — Business Opportunity & Pivot Engine

Design notes for what is built. Authoritative rules live in `CLAUDE.md`
(§12 opportunity engine, §13 profile, §14 asset-aware model, §22 confidence,
§30 "do not").

## Scope

Phase 2D reads one proposed business and returns a market **label**. Phase 3 is
the first phase that (a) evaluates **several** businesses, (b) takes a **person**
as input, and (c) emits a **number**. All three are sanctioned by §12 (a 0–100
comparison across candidate businesses, each score decomposing into named factor
contributions) and by `docs/phase-2d.md` ("Phase 3 is built *by calling Phase 2D
N times*").

Phase 3 answers:

> Given this market **and this entrepreneur's resources**, which businesses
> should this person consider — and is the one they proposed actually the best of
> them?

It does **not** compute finance. Project cost, working capital, revenue, EMI,
DSCR, cash flow, loan structure, moratorium and stress tests are Phase 4 (§15–17)
and are enforced against in `tests/test_opportunity_scoring.py`
(`test_no_result_field_implies_a_probability_of_success`). No RAG, no LLM, no
scheme rules.

## Data flow

```
                    ┌─ IMPURE (network / disk) ─────────────────────────┐
location + cands ──►│ DiscoveryService.discover(also_fetch=…)           │  one union
                    │   → one DiscoveryResult over every candidate      │  Overpass
                    │ discovery/opportunity_acquisition.py:             │  business
                    │   • acquire_demand_evidence()  (one demand query) │  fetch
                    │   • per-candidate coverage confidence  (§0 below) │
                    └──────────────────┬───────────────────────────────┘
                                       ▼
                          OpportunityEvidence   (models/opportunity.py)
                                       │
  EntrepreneurProfile ─────────────────┤
  (models/profile.py)                  ▼
                    ┌─ PURE (no I/O, clock, RNG, LLM) ─────────────────┐
                    │ market/opportunity.py::score_opportunities()      │
                    │  per candidate X:                                │
                    │    view = discovery.model_copy(confidence=conf[X])│
                    │    proposed_from_category(X) → 2A → 2B → 2C → 2D  │
                    │    → market/asset/experience components → score  │
                    │  rank (score, capital) · stance · pivot          │
                    └──────────────────┬───────────────────────────────┘
                                       ▼
                          OpportunityAnalysisResult
```

The pure engine re-runs the Phase 2A–2D engines per candidate over the **same**
evidence — it never re-queries OSM. `analyze_competitors` /
`compute_competition_metrics` read `.businesses`, the query point, radius,
`query_text` and `confidence` — never `.category` — so per-candidate
re-classification is correct; only `confidence` is swapped per candidate.
`compute_demand_signals` is re-run per candidate (free) so
`competitors_per_1000_people` reflects that candidate's competitor count; the
`DemandEvidence` itself is category-agnostic and gathered once.

The `market/` no-I/O invariant (AST-checked in `tests/test_market_purity.py`)
covers `opportunity.py`, `opportunity_config.py`, `opportunity_models.py`.

## Modules

| module | contents |
|---|---|
| `models/profile.py` | `AssetKind`, `EntrepreneurProfile` — user-provided, unverified |
| `models/opportunity.py` | `OpportunityEvidence` — the acquisition → engine seam |
| `market/opportunity_config.py` | frozen `OpportunityConfig` + `DEFAULT_OPPORTUNITY_CONFIG`: candidate universe, weights, label→points, label lattice, `asset_relevance`, `capital_bands`, experience points, fixed caveats. Import-pure (plain strings) |
| `market/opportunity_models.py` | `OpportunityStatus`, `AssetRelevance`, `CapitalFit`, `Stance`, `OpportunityEvidenceRef`, `ScoreComponent`, `ScoredCandidate`, `FinancialFitInput`, `OpportunityAnalysisResult` |
| `market/opportunity.py` | `score_opportunities(evidence, profile, *, config=None, financial_fit=None)` |
| `discovery/opportunity_acquisition.py` | `acquire_opportunity_evidence(...)` — one demand query + per-candidate coverage confidence |
| `categories/osm_query_tags.py` | `selectors_for_many()` — order-stable union of `selectors_for` |

Upstream changes, all additive: `DiscoveryService.discover(..., also_fetch=())`
(default `()` reproduces Phase 1 exactly); `selectors_for_many([X])` equals
`selectors_for(X)`. `market/classifier.py`, `metrics.py`, `demand.py`,
`assessment.py`, `discovery/confidence.py` and every existing config are
untouched.

## §0 — the per-candidate coverage-confidence adaptation

**Not in the approved plan text; required by the plan's own manual checks and by
"fix any failure you introduce".**

`discovery/confidence.py::coverage_confidence` computes a single confidence from
the size of the *whole* `DiscoveryResult`, and `metrics.py` passes
`discovery.confidence` straight through as `data_confidence`. Under a
**one-category** Phase 1 fetch this was implicitly per-category. Under a **union**
fetch of ~8 categories it is not: a candidate with zero relevant businesses would
inherit the confidence earned by the other categories' POIs, Phase 2D's
`absence_not_evidence` rung (fires only when `data_confidence < 0.5`) would stop
firing, and a sparse rural zero-competitor case would flip from the honest
`insufficient_evidence` to a flattering `underserved` — the exact "few
competitors ⇒ great opportunity" failure Phase 2 was built to avoid.

The fix, entirely inside the new Phase 3 layer (no Phase 1/2 file changed):
`acquire_opportunity_evidence` recomputes `coverage_confidence` **per candidate**
over only the businesses that are *not* `Relationship.IRRELEVANT` to that
candidate (`relationship_for`, the Phase 2A table). Zero relevant businesses →
`normalized == 0` → `0.0`, exactly as a standalone single-category run. The pure
engine swaps the per-candidate value onto `discovery.model_copy(update=…)` before
Phase 2B. `tests/test_opportunity_scoring.py::
test_union_fetch_zero_relevant_stays_insufficient_not_underserved` is the
regression.

`assessment_data_confidence`'s demand half is candidate-invariant, so
`market_data_confidence` is reported once at result level (the Phase 2C
`demand_data_confidence`), unmultiplied; each candidate also carries its own
competition-side `coverage_confidence`.

## The score

Primary machine-readable verdict is the Phase 2D **label**; `opportunity_score`
is a display projection.

### `market_opportunity` (weight 0.60) — the only evidence-backed component

A lookup from the Phase 2D label **alone** (2D already fused competition +
demand; re-deriving either would double-count —
`test_market_component_depends_only_on_the_2d_label`):

| 2D label | points |
|---|---|
| `underserved` | 85 |
| `mixed` | 60 |
| `served` | 55 |
| `insufficient_evidence` | 40 |
| `crowded` | 25 |
| `thin_market` | 20 |

`insufficient_evidence` is scored (40), so such candidates still appear in the
ranking, **but** `evidence_sufficient` is `False` for them and they can never
become a pivot recommendation or anchor `proposed_is_best` (approved
clarification 1). 2D's `catchment_scale` is not category-specific, so the matrix
*row* is fixed across candidates and only the competition *column* varies —
correct: population belongs to the place, not the business.

### `asset_fit` (0.25) — curated table

`CATEGORY_ASSET_RELEVANCE: category → {AssetKind → essential | helpful}`, a
qualitative structural claim ("a dairy needs cold storage"), same class as
`relationships.py`, each entry justified in a comment.

```
essential_met = |owned ∩ essential| / |essential|   (1.0 if none essential)
helpful_met   = |owned ∩ helpful|   / |helpful|     (1.0 if none helpful)
asset_fit     = 100 * (0.70*essential_met + 0.30*helpful_met)
```

### `experience_fit` (0.15) — reuses `relationship_for`, no new table

```
per experience category E vs candidate X:
  E == X → 100 | DIRECT → 80 | ADJACENT → 60 | else 40
experience_fit = max over E
```

### Renormalisation

`opportunity_score = round( Σ value·nominal_weight / Σ nominal_weight )` over the
components that carry a value. A component with no profile input (no assets, no
experience) is dropped and the weights renormalise; the renormalisation is
visible in each component's `effective_weight` and in `components_missing`.
`weight_coverage_pct` is the share of the *total* nominal weight that had data
(100 = all three components scored; 60 = market only).

A component missing for lack of a **config table** (a proposed category outside
`asset_relevance`) is also dropped from the score, but the candidate is flagged
`capability_incomplete = True` and is **excluded from any "materially better"
claim** (`test_missing_config_table_flags_capability_incomplete_not_silent`).

### The score decides nothing on its own

Ranking sorts by `(capital_fit is OUT_OF_REACH, -opportunity_score,
category.value)`. The **pivot recommendation** is gated on the Phase 2D label
lattice, `evidence_sufficient`, `capability_incomplete` and `capital_fit` — never
on the scalar alone (approved clarification 3).

## Capital — an indicative screen, never a verdict

`CATEGORY_CAPITAL_BANDS: category → (indicative_minimum_inr, typical_inr)`. An
`[assumption]`, pending Phase 5 scheme retrieval. `liquid_cash_inr` maps to:

| condition | `CapitalFit` |
|---|---|
| cash not stated / no band | `UNKNOWN` |
| cash ≥ typical | `AFFORDABLE` |
| minimum ≤ cash < typical | `STRETCH` |
| cash < minimum | `OUT_OF_REACH` |

`OUT_OF_REACH` sinks a candidate in the ranking and blocks it from being a pivot,
but **never** changes its score or label
(`test_out_of_reach_changes_partition_not_score_or_label`) and the reason string
never says "impossible" / "ineligible" / "unaffordable" / "cannot"
(`test_capital_screen_language_is_never_a_verdict`). A fixed caveat names Phase 4
as the replacement.

## Stance — top-ranked ≠ recommended

| stance | when |
|---|---|
| `no_proposal_to_compare` | `proposed_category` is `None` / `UNKNOWN` — shortlist still ranked |
| `no_recommendation` | no candidate is `evidence_sufficient` (status `no_evidence`) |
| `alternative_materially_better` | an alternative is (a) strictly higher on the label lattice than the proposed, (b) `evidence_sufficient`, (c) not `capability_incomplete`, (d) `capital_fit` ∈ {affordable, stretch}, **and** (e) its score is ≥ `material_margin` (8) higher. `recommended_pivot` is set. |
| `proposed_is_best` | the proposed is `evidence_sufficient` and scores ≥ every alternative, and no pivot cleared the bar |
| `alternatives_comparable` | something scores higher / alongside but no pivot cleared every gate |

Each gate is tested independently.

## Confidence

`market_data_confidence` (Phase 2C demand coverage, candidate-invariant) and
`profile_completeness` are reported once at result level, **unmultiplied**. Each
candidate carries its own `coverage_confidence`. Confidence never changes a
score, a rank, or the stance — only whether a candidate is `evidence_sufficient`,
via Phase 2D's own rungs.

## The unsupported-factor register (§12)

§12 lists ten contributing factors. Three have backing; seven do not, and Phase 3
does not fake them:

| §12 factor | Phase 3 MVP |
|---|---|
| demand strength | inside `market_opportunity` (via 2C→2D) |
| competition / saturation | inside `market_opportunity` (via 2B→2D) |
| accessibility & catchment | already fused into 2C→2D; not counted again |
| **local resource availability** | **dropped** — no livestock / agri / economic dataset exists. The §31 worked example ("cattle-feed scores higher because local livestock is stronger") cannot be honestly produced; `ActivityKind` is only `{school, marketplace, bank, transport_stop}` and the Census extract carries only persons + households. |
| capital vs available | ordinal `CapitalFit` screen only (above) |
| working-capital intensity | Phase 4 |
| fit with existing assets | `asset_fit` (new profile + curated table) |
| fit with experience / skills | `experience_fit` (new profile, reuses `relationships.py`) |
| risk / resilience | Phase 4 (§15/§16) |

## Known limitations

- **Adjacency-table asymmetry.** `relationships.py` is unevenly populated. Within
  the shortlist the only cross-category `DIRECT` pairs are
  `grocery↔general_store` and `restaurant↔food_stall`, so those categories absorb
  a partner's businesses as direct competitors while `dairy`, `pharmacy`,
  `agri_input`, `livestock_services`, `food_processing` do not — the sparse ones
  can look *less* competitive purely because the table is thinner. This never
  mattered one-category-at-a-time; it is first-order the moment eight are
  compared. `direct_competitors` and `competition_signal` are exposed per
  candidate so the effect is visible.
- **The candidate universe is a fixed MVP list**, not an inferred one.
  Generating candidates from "categories with no OSM hits" would be the §6
  absence-inference trap. A fixed caveat says the list is not exhaustive; a
  proposed category outside it is still scored.
- **Curated tables are hand-authored heuristics.** `asset_relevance` and
  `capital_bands` being wrong yields a wrong *reason*, not a wrong *number* class
  — the same risk profile as `relationships.py`. Every table is in the frozen
  `OpportunityConfig` and echoed into every result.
- **`catchment_scale` is not per-category** (inherited from 2D) — a pharmacy and
  a tea stall use the same population thresholds.
- **Subtypes are ignored.** Phase 3 compares at category granularity;
  `proposed_from_category(X)` is used uniformly for every candidate including the
  proposed one, so a "pulses grocery" is scored as `grocery`.

## Phase 3 → Phase 4

No dead parameter: `financial_fit` is accepted and its `notes` surface onto the
matching candidate's `warnings`, but it does **not** affect the score in the MVP
(`test_financial_fit_input_is_recorded_but_does_not_change_the_score`). Phase 4
replaces the capital screen wholesale and may add a fourth `ScoreComponent`; the
component list, weights and `CapitalFit` states are already config-owned, so that
is a config + one-module change, not a redesign.

## CLI

`scripts/discover_businesses.py --opportunity` runs Phase 1 (widened to a union
Overpass fetch) → Phase 3. `--cash INR`, `--asset KIND` (repeatable),
`--experience CATEGORY` (repeatable) build the profile; `--proposed TEXT` /
`--category` set the proposed business. `--opportunity --json` emits the
`OpportunityAnalysisResult`. Phase 3 outcomes are all valid analyses, so the exit
code is `0` for `ok` and `no_evidence` alike (matching Phase 2D, where an
`insufficient_evidence` label keeps status `ok`); only the never-reached
`no_candidates` trips exit `2`.

## Real-data validation

- **Bhagwanpur, Vaishali, Bihar** (`grocery`, 8 km, union fetch): with zero
  grocery businesses the grocery candidate stays `insufficient_evidence` (the §0
  fix holds — `coverage_confidence` 0.0 for grocery), never `underserved`. If any
  candidate has geolocated Bihar Census population it is assessed on a real
  `catchment_scale`; candidates with no relevant business stay
  `insufficient_evidence` at `coverage_confidence` 0.0.
- **Sangareddy, Telangana** (`grocery`, 8 km): no geolocated population →
  `activity_proxy` tier capped at `moderate`; candidates are scored where
  settlements/anchors exist, `market_data_confidence` reflects the proxy penalty.
