# Phase 2A — Competitor Identification

Design notes for what is built. Authoritative rules live in `CLAUDE.md` (§11);
this file records how Phase 2A realises the "identify competitors" step.

## Scope

Phase 2A answers exactly one question: **which discovered businesses compete with
the proposed business, and why?** It classifies every Phase 1 business as
`direct`, `adjacent`, or `irrelevant` with a deterministic reason.

It does **not** compute any market metric — no saturation, density, demand,
market size, opportunity, viability, or competition score. Those are Phase 2B+.

## Data flow

```
Phase 1  DiscoveryResult
              │  (consumed as-is; no OSM / Nominatim / HTTP / DB access)
              ▼
Phase 2A  analyze_competitors(discovery, proposed) -> CompetitorAnalysisResult
```

`market/` imports only `market.*`, `models.*`, and `normalization.text` — it has
no path to a source adapter, geocoder, HTTP client, or repository (enforced by
`test_market_classifier.py`).

## Modules (`src/vyaparsarathi/market/`)

| module | contents |
|---|---|
| `models.py` | `Relationship` (`direct`/`adjacent`/`irrelevant`/`unknown`), `CompetitorAnalysisStatus` (`ok`/`unknown_category`), `ProposedBusiness`, `ClassifiedCompetitor`, `CompetitorAnalysisResult` — Pydantic v2, JSON-serializable |
| `relationships.py` | **the single config layer** — `CATEGORY_RELATIONSHIPS`, `SUBTYPE_CATEGORY_OVERLAPS`, `KNOWN_SUBTYPES`, `relationship_for()`, `strongest()` |
| `proposed.py` | `proposed_from_category()` and `resolve_proposed_business(text)` — map the entrepreneur's proposal onto the taxonomy |
| `classifier.py` | `analyze_competitors()` + `analyze_from_discovery()` convenience |

## The proposed business (`proposed.py`)

`ProposedBusiness` carries a `category: BusinessCategory` and optional
`subtypes: list[str]` (lowercase product tokens, e.g. `["pulses"]`).

`resolve_proposed_business(text)` maps free text deliberately without NLP:

1. exact internal category name (`"grocery"`);
2. an alias table `PROPOSED_ALIASES` (`"kirana"`, `"supermarket"`, `"cattle
   feed"`, `"dal mill"`, …), matched longest-phrase-first with the matched span
   consumed so `"dal mill"` is not also read as `"dal"`;
3. bare `KNOWN_SUBTYPES` tokens found in the text become subtypes.

Outcome:
- exactly one category matched → `resolved=True`;
- zero, or two-or-more conflicting categories → `category=UNKNOWN`,
  `resolved=False`, with a `note` — **never a silent guess**.

`analyze_competitors` with an unresolved proposal returns
`status=unknown_category`, empty buckets, and a clarification warning.

## Category relationships (`relationships.py`)

`CATEGORY_RELATIONSHIPS: dict[proposed -> dict[existing -> Relationship]]`.
Directional (a proposed grocery's view of a dairy need not equal a proposed
dairy's view of a grocery). Only `direct` / `adjacent` pairs are listed; anything
omitted is `irrelevant`; a category is always `direct` against itself.

Current highlights (edit this file as real data teaches us):

| proposed | direct | adjacent |
|---|---|---|
| `grocery` | `grocery`, `general_store` | `dairy`, `food_stall`, `food_processing`, `agri_input` |
| `dairy` | `dairy` | `grocery`, `general_store`, `food_stall` |
| `agri_input` | `agri_input` | `livestock_services`, `grocery`, `general_store`, `hardware`, `food_processing` |
| `pharmacy` | `pharmacy` | — |
| `restaurant` / `food_stall` | each other | `dairy`, `grocery` |
| `hardware` / `building_materials` | each other | `agri_input`, `furniture` |

> Note: `shop=supermarket` and `shop=general` are already folded into `grocery` /
> `general_store` by the Phase 1 OSM map, so a "supermarket" is classified via
> the `grocery` row, not a separate rule.

## Subtypes (`SUBTYPE_CATEGORY_OVERLAPS`)

A subtype can only **strengthen** a relationship (`irrelevant < adjacent <
direct`), two ways:

1. **Category-level:** `SUBTYPE_CATEGORY_OVERLAPS[subtype][existing_category]`,
   e.g. `"feed"` makes a proposed `agri_input` see a `livestock_services` shop as
   `direct` instead of `adjacent`.
2. **Name reference:** the subtype token appears in the existing business's
   normalized name (e.g. a shop literally named "… Pulses …"). This promotes an
   `irrelevant` classification to at least `adjacent`, and keeps a
   same-category / already-`direct` match at `direct`.

`matched_subtypes` on each `ClassifiedCompetitor` records which subtype tokens
fired, and the `reason` string explains it.

This is a small extensible mechanism, **not** a product ontology.

## Determinism & explainability

- No LLM, no network, no wall-clock, no RNG. Same inputs → identical
  `CompetitorAnalysisResult` (asserted in tests).
- Every classification carries a `reason` built from f-strings over category /
  subtype values. `direct` and `adjacent` reasons are always non-empty.
- Buckets are sorted nearest-first by `distance_m` (carried from the Phase 1
  `BusinessHit`; `None` sorts last).

## CLI

`scripts/discover_businesses.py --competitors` runs Phase 1 then Phase 2A.
`--subtype KEYWORD` (repeatable) and `--proposed "free text"` refine the
proposal; `--proposed` overrides `--category` for the classification only.
`--competitors --json` emits the `CompetitorAnalysisResult`. Exit code `2` when
the proposed business is `unknown_category`.

## Known limitations

- On a Phase 1 result that was itself category-filtered (the normal case — a
  `--category grocery` discovery only fetches grocery-ish OSM tags), every
  discovered business is already grocery-ish, so Phase 2A mostly returns
  `direct`. Mixed direct/adjacent/irrelevant output needs a wider discovery net,
  which is a Phase 2B concern.
- Relationship tables are hand-curated for the ~7 rural categories we expect in
  the demo; the long tail (`salon`, `furniture`, …) has only self-`direct` rules.
- Subtype matching is exact-token against the normalized name; no stemming or
  synonyms (`"dal"` ≠ `"pulses"` for name matching, though the resolver maps the
  word `"dal"` to the `pulses` subtype).
- No confidence score on individual classifications yet (`ClassifiedCompetitor`
  has room for one); the relationship + reason are the explainability contract.

## What Phase 2B should do next (not built)

Consume `CompetitorAnalysisResult` to compute **competition metrics only**:
competitor counts (direct vs direct+adjacent), nearest-competitor distances,
competitor density per catchment, a saturation signal, and a market-data
confidence — each reported with the data-coverage caveat. Phase 2B does not
re-classify and does not judge viability.
