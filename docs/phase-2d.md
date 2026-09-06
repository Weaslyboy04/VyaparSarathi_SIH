# Phase 2D — Overall Market Assessment

Design notes for what is built. Authoritative rules live in `CLAUDE.md`
(§11 market engine, §12 opportunity engine — a **later** phase, §22 confidence).

## Scope

Phase 2B says how concentrated the competition is; Phase 2C says how large the
catchment is. Phase 2D is the first phase that reads them **together** and
answers:

> What does the observed competition + local demand evidence imply about the
> market for **this** proposed business **here**?

The output is a **label**, never a score. Phase 2D does not recommend a business,
compare alternatives, or touch finance — those are Phase 3 (`CLAUDE.md` §12) and
beyond, and are enforced against in `tests/test_market_assessment.py`.

## Data flow

```
Phase 2A CompetitorAnalysisResult ─ optional (competitor names for findings)
Phase 2B CompetitionMetricsResult ─ required ─┐
Phase 2C DemandSignalsResult ─────── required ─┤
                                               ▼
        market/assessment.py::assess_market(metrics, demand, analysis=None,
                                             config=None)
                    PURE — no I/O, clock, RNG, or LLM
                                               ▼
                                MarketAssessmentResult
```

Re-queries nothing, recomputes nothing 2A/2B/2C produced. The `market/` no-I/O
invariant (AST-checked in `tests/test_market_purity.py`) covers the new modules.

## Modules (`src/vyaparsarathi/market/`)

| module | contents |
|---|---|
| `assessment_models.py` | `MarketAssessmentStatus`, `MarketAssessmentLabel`, `CatchmentScale`, `ScaleTier`, `LadderRung`, `FindingKind`, `EvidenceRef`, `Finding`, `ScaleBasis`, `CompetitionSummary`, `DemandSummary`, `MarketAssessmentResult` |
| `assessment_config.py` | frozen `AssessmentConfig` + `DEFAULT_ASSESSMENT_CONFIG`: every threshold, the label matrix, the finding message templates, the fixed caveat strings. Import-pure (no model enums) so `assessment_models` can import it without a cycle |
| `assessment_findings.py` | one pure predicate per rule; `FINDING_RULES` is the ordered tuple |
| `assessment.py` | `assess_market(...)` — orchestration: gate → consistency → scale → ladder → matrix → findings → confidence |

## The two axes

### `CatchmentScale` — *not* "demand level"

Named for what was measured: a Census head-count is *residents on paper*, not
purchasing power (§3.5, §30). Tiered, with `scale_basis.tier` always populated:

| tier | when | can reach |
|---|---|---|
| `population` | `catchment.persons` present **and** `population_coverage >= min_population_coverage_for_scale` (0.34) | up to `LARGE` |
| `activity_proxy` | no usable population figure, but settlements / anchors exist | capped at `MODERATE` (`proxy_max_scale`) — structurally, not as a side effect of the numbers |
| `none` | nothing observed | → `CatchmentScale.UNKNOWN` |

Population tier: `persons >= population_large` (20,000) → `LARGE`;
`>= population_moderate` (5,000) → `MODERATE`; else `SMALL` (a real `persons == 0`
lands here, not `UNKNOWN`).

Proxy tier: `settlements_found >= proxy_settlements_moderate` (15) **or**
`distinct_activity_kinds >= proxy_kinds_moderate` (3) → `MODERATE`;
`>= proxy_settlements_small` (3) or `>= 1` kind → `SMALL`; else `UNKNOWN`.

`osm_tagged_population_total` (2C's quarantined signal) never enters the scale.

### `competition_signal`

`metrics.signal`, verbatim. 2B owns competition; 2D does not re-derive it.

## The precedence ladder — explicit, ordered, recorded

`label_basis["rung"]` records which rung decided the label. First match wins.

| rung | condition | 2D `status` | `label` |
|---|---|---|---|
| 0 `upstream_status` | any required upstream status ≠ OK | mapped from upstream | `INSUFFICIENT_EVIDENCE` |
| 1 `inconsistent_inputs` | 2B & 2C radius / point / category disagree | `INCONSISTENT_INPUTS` | `INSUFFICIENT_EVIDENCE` |
| 2 `scale_unknown` | `catchment_scale is UNKNOWN` | `OK` | `INSUFFICIENT_EVIDENCE` |
| 3 `nothing_observed` | `settlements_found == 0` **and** `direct_count == 0` | `OK` | `INSUFFICIENT_EVIDENCE` |
| 4 `absence_not_evidence` | `signal is NONE` **and** `metrics.data_confidence < min_confidence_for_absence_claim` (0.5) | `OK` | `INSUFFICIENT_EVIDENCE` |
| — `matrix` | none of the above | `OK` | from `label_matrix` |

**Rung 0 reads only `.status`.** `metrics.signal` defaults to
`CompetitionSignal.NONE` and `direct_count` to `0` on a non-OK 2B result
(verified in `market/metrics.py`), so reading them before the gate would turn
"could not compute" into "no competition found" — the exact input that yields
`UNDERSERVED`. 2B is checked before 2C. Rung-0 results carry
`competition_summary = None` as proof no non-status field was read.

Rung-0 upstream → 2D status map:

| 2B | 2C | → 2D status |
|---|---|---|
| `unknown_category` | any | `unknown_category` |
| `invalid_radius` | any | `invalid_radius` |
| `ok` | `location_unresolved` | `location_unresolved` |
| `ok` | `source_unavailable` | `source_unavailable` |
| `ok` | `invalid_radius` | `invalid_radius` |
| `ok` | `ok` / `no_population_data` / `population_not_geolocated` / `no_settlements_found` | continue |

Rung 4 is the "absence of evidence is not evidence of absence" trap (§6), handled
structurally rather than as a warning a consumer can ignore.

## The label matrix (`config.label_matrix`)

3 scale rows × 4 competition columns. `none` competition that survives rung 4 is
read as the `low` column plus a mandatory coverage caveat.

| scale \ competition | `none` | `low` | `moderate` | `high` |
|---|---|---|---|---|
| `small` | **thin_market** | **thin_market** | crowded | crowded |
| `moderate` | underserved | mixed | mixed | crowded |
| `large` | underserved | underserved | served | served |

The `small` / `none` cell is the whole point of the phase: no competitors **plus**
a small catchment is `thin_market`, not an opportunity.

`label_reason` names both axes, the tier and the matrix cell.

## `persons_per_direct_competitor`

The legible inverse of 2C's `competitors_per_1000_people` (*"one grocery per
5,000 residents"* reads better than *"0.2 competitors per 1,000 people"*). Pure
arithmetic on `demand.catchment.persons / metrics.direct_count`; 2C is not
modified. **Supporting only, never an axis** — it collapses the two dimensions
into one ratio (`1 competitor / 500 people` and `20 / 10,000` are equal), it is
`None` wherever population is missing, and on a floor population it is an *upper*
bound on saturation. `None` unless both a population figure and ≥1 direct
competitor exist.

## Findings — three buckets

`positive_signals` / `concerns` / **`data_caveats`**. The third bucket is
load-bearing: *"no competitors found, but coverage is sparse"* is a data caveat,
not a market negative — filing it under `concerns` would make a coverage problem
read as a verdict (§22). No `severity` field (severity is an implicit weight → §12).

Each `Finding` has a machine-readable `code`, a `message` rendered from a fixed
template in `config.finding_messages`, and typed `EvidenceRef`s
(`source` ∈ {competition, demand, analysis}, dotted `field` path, `value`,
optional `compared_to`). `tests/test_market_assessment.py` resolves every emitted
finding's `(source, field)` against the actual input model and asserts the value
matches — a fabricated or stale finding is a test failure.

Rule codes (each is a `FINDING_RULES` function name minus its leading `_`):

- **positive:** `no_direct_competitors_confident`, `nearest_competitor_distant`,
  `large_catchment_population`, `multiple_settlements_in_catchment`,
  `marketplace_anchor_present`, `transport_access_present`
- **concerns:** `small_catchment`, `high_competition`, `competitor_very_close`,
  `adjacent_substitutes_present`
- **data caveats:** `population_not_geolocated`, `population_is_floor`,
  `population_unknown`, `stale_population_data`, `low_market_data_confidence`,
  `competitor_absence_low_coverage`, `distances_unavailable`

Distance-based rules check `direct_distance.count > 0`, not `direct_count > 0`
(`DistanceStats.missing_distance` exists for competitors with no usable distance).

## Confidence

```
assessment_data_confidence = min(metrics.data_confidence,
                                 demand.demand_data_confidence) × tier_penalty
tier_penalty = 1.0 (population tier) | 0.7 (activity proxy tier)
```

**`min`, not a weighted mean** — a fused conclusion is only as observable as its
weakest input, and a mean lets a confident competition read mask an absent
population read (§22). `assessment_data_confidence_basis` carries both inputs,
which one bound, and the penalty.

Two hard rules, both test-enforced:

- **Confidence never changes the label.** Low confidence goes to `data_caveats`
  and `warnings`. The only paths from evidence quality to the label are ladder
  rungs 2–4, which are structural refusals, not downgrades.
- It measures **how well the market is observed, not whether the business will
  succeed** — the `*_data_confidence` house wording.

## Edge cases

| case | behaviour |
|---|---|
| 2B `unknown_category` / `invalid_radius` | rung 0; `competition_summary` never populated |
| 2C `location_unresolved` / `source_unavailable` / `invalid_radius` | rung 0 |
| 2C `no_population_data` / `population_not_geolocated` | **not** blocking — proxy tier |
| 2C `no_settlements_found` | scale `UNKNOWN` → rung 2, 2D status `OK` |
| radius / point / category mismatch between 2B and 2C | rung 1, `INCONSISTENT_INPUTS`, no label |
| `direct_count == 0` + low coverage | rung 4 → `INSUFFICIENT_EVIDENCE`, not `UNDERSERVED` |
| `direct_count > 0` but all distances `None` | distance findings suppressed; `distances_unavailable` caveat |
| `is_floor` | `population_is_floor` caveat; a `thin_market` label is marked provisional in `warnings`; scale never auto-upgraded |
| zero settlements **and** zero competitors | rung 3 — an empty pipeline is not a thin market |
| both statuses OK, very low confidence | label emitted; confidence in `data_caveats` |

## CLI

`scripts/discover_businesses.py --assess` runs Phase 1 → 2A → 2B → 2C → 2D
(`--assess` implies `--metrics --demand`). `--assess --json` emits the
`MarketAssessmentResult` (JSON precedence assessment → demand → metrics →
analysis → discovery). Exit code `2` on any non-OK assessment status.

## Real-data validation

- **Bhagwanpur, Vaishali, Bihar** (`grocery`, 8 km): catchment scale `LARGE`
  (349,749 residents, population tier). Phase 1 found 0 grocery businesses at
  `data_confidence 0.00`, so the assessment is **`INSUFFICIENT_EVIDENCE` via
  rung `absence_not_evidence`** — not a flattering `UNDERSERVED`. The ladder
  working as intended.
- **Sangareddy, Telangana** (`grocery`, 8 km): no geolocated population →
  `activity_proxy` tier, scale `MODERATE` (capped), competition `low`, label
  `MIXED` (`matrix[moderate][low]`), `assessment_data_confidence 0.37`
  (0.525 × 0.7 proxy penalty).

## Known limitations

- The matrix and thresholds are hand-picked MVP heuristics; all are in the frozen
  `AssessmentConfig` and echoed into every result.
- `label_reason` embeds `scale_basis.reason` verbatim, producing a long
  nested-parenthesis sentence; the structured fields carry the clean data.
- The proxy tier answers "is this a populated place?", not "how big is the
  catchment?"; it can never justify `UNDERSERVED`.
- `catchment_scale` is not per-category — a pharmacy and a tea stall use the same
  population thresholds. A `min_viable_catchment_by_category` table is a
  *viability* judgement (§12) and is deliberately not built here.

## What Phase 3 should do next (not built)

The §12 opportunity / pivot engine: score the proposed business **and a set of
alternatives** for the same location, capital and assets, each score decomposing
into named factor contributions. Phase 3 is built *by calling Phase 2D N times*
— which is why 2D takes exactly one proposed business, returns no list, and
carries no numeric score. Finance (§15), scheme routing (§18) and the DPR (§8)
are further out still.
