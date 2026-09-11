# Phase 5 — Knowledge / Evidence Layer

Design notes for what is built. Authoritative rules live in `CLAUDE.md` (§3.1
LLM is not the source of truth, §18/§19 RAG boundaries and retrieval, §22
confidence, §23 provenance, §30 "do not").

## Scope

Phase 4 shipped a deterministic financial engine whose provenance model was
built for exactly one thing it could not yet do: consume a **sourced** fact.
Every figure in a Phase 4 run was, until now, either what the entrepreneur
stated or a configured `ASSUMED` convention. Phase 5 answers:

> Given a named financial parameter (an interest rate, a scheme margin, a
> statutory fee, a sector benchmark), what does the corpus of official
> documents actually say, and can that be turned into a `FinancialInput`
> without ever letting an LLM — or a regex — invent the number?

The risk this phase is built to guard against is the same one Phase 4 named
for itself: becoming a plausible-looking pipeline whose numbers rest on
invented rates or margins, this time laundered through "the document said
so" instead of "the demo assumed so." The countermeasure is structural, in
three parts:

1. **Extraction happens offline, once, through a genuinely blind dual-LLM
   gate** — never at request time, and no single model's say-so is trusted:
   two DIFFERENT Gemini models each independently read the exact same
   chunk/document/instructions and each propose at most one candidate row —
   neither is ever shown the other's answer. The two independent candidates
   are compared only after both are in hand, deterministically (never
   fuzzy-matched); they publish only on exact canonical agreement, or the
   row is dropped — never queued, never averaged, never arbitrarily picked
   (`scripts/build_parameter_registry.py`'s module docstring has the full
   gate). This replaced an earlier human-signature requirement; see that
   script's docstring for exactly what each model is and isn't trusted to
   decide.
2. **A registry row is checkable, not merely asserted**: its `value_token`
   must occur in its own `evidence_quote`, its `value` must match what
   `normalize_value` derives from that token, and (at load time) its
   `evidence_quote` must occur verbatim in the chunk it cites. A row's
   `tier` is always copied from its own document's `DocumentRecord.tier`
   (never proposed by either model), and its `applicability.jurisdiction`
   is rejected if it claims broader or mismatched scope than its own
   document ever asserted — both checked again at load time
   (`sources/knowledge/loader.py::jurisdiction_exceeds_document_scope`) as
   defense-in-depth against a hand-edited `documents.jsonl`.
3. **The resolver never consults a retriever.** A parameter's value is
   chosen by a fixed precedence ladder over the registry alone; retrieval
   only supplies passages for citation/display. A bad ranking can degrade
   what a human sees next to a number — it can never change the number.

Phase 5 computes **no** market, opportunity, or financial arithmetic — it
resolves the value of a named parameter from reviewed documents and hands it
to Phase 4 as a `FinancialInput(kind=SOURCED, ...)`. It performs no scheme
eligibility determination and no routing between schemes (CLAUDE.md §25:
that is a separate, later component); given a scheme name, it looks up that
scheme's stated parameters and nothing more.

## Data flow

```
        ┌─ operator, offline, dual-LLM gated ────────────────────────┐
        │ data/knowledge/raw/<document_id>/{document.json,text.txt} │
        │   -> scripts/build_knowledge_corpus.py    (chunk)         │
        │   -> scripts/build_parameter_registry.py  (extract:       │
        │      extractor + verifier Gemini models must agree, or    │
        │      the row is dropped — never queued for a human)       │
        │   -> scripts/build_parameter_registry.py --verify         │
        └────────────────────────┬───────────────────────────────────┘
                                 │ commits
                    data/knowledge/{documents.jsonl,
                                    chunks.jsonl.gz,
                                    parameters.csv,
                                    manifest.json}
                                 │
        ┌─ IMPURE (file I/O only, no network) ───────────────────────┐
        │ sources/knowledge/loader.py::FileCorpusStore               │
        │   -> discovery/knowledge_acquisition.py::                  │
        │      acquire_finance_knowledge()  -> FinanceKnowledgeEvidence│
        └────────────────────────┬───────────────────────────────────┘
                                 │ (clock read ONCE, at this boundary)
        ┌─ PURE (no I/O, clock, RNG, LLM) ────────────────────────────┐
        │ knowledge/resolver.py       filters + precedence -> chosen  │
        │ knowledge/confidence.py     evidence-quality number, never  │
        │                             consulted by the resolver above │
        │ knowledge/plan_binding.py   FinancialInput(kind=SOURCED)    │
        └────────────────────────┬───────────────────────────────────┘
                                 ▼
                      FinancialPlanInput  ->  finance/assessment.py
                                              (UNCHANGED — no Phase 4
                                               module edited by this phase)

  knowledge/retrieval.py (metadata prefilter + lexical BM25) runs
  independently -> RetrievedPassage, for display/citation ONLY. It is never
  imported by resolver.py or plan_binding.py.
```

`knowledge/plan_binding.py` is the only module in the whole Phase 5 package
that constructs a `FinancialInput` or imports `vyaparsarathi.models.finance`'s
plan models — the mirror of `finance/fit.py` being the only module in
`finance/` allowed to import `vyaparsarathi.market`
(`tests/test_knowledge_purity.py`).

## Modules

| module | contents |
|---|---|
| `models/knowledge.py` | `SourceTier`, `JurisdictionLevel`, `Jurisdiction`, `ChunkLocator`, `KnowledgeTopic`, `DocumentRecord`, `DocumentChunk`, `RetrievedPassage`, `KnowledgeAcquisitionReport` |
| `models/parameters.py` | `ParameterName`, `ValueNormalization`, `normalize_value()`, `Applicability`, `SourcedParameter`, `ParameterQuery`, `ResolutionStatus`, `ParameterResolution`, `FinanceKnowledgeEvidence` — the seam |
| `knowledge/knowledge_config.py` | frozen `KnowledgeConfig` + `DEFAULT_KNOWLEDGE_CONFIG`: tier weights, freshness decay, single-document ceiling, applicability penalties, agreement bonus, lexical tunables |
| `knowledge/parameter_spec.py` | `PARAMETER_SPEC: dict[ParameterName, ParameterSpec]` — the fixed unit + tier-floor contract per parameter |
| `knowledge/base.py` | `CorpusStore`, `Retriever` Protocols; `RetrievalFilters` |
| `knowledge/confidence.py` | `parameter_confidence()` |
| `knowledge/resolver.py` | `resolve_parameters()` — the precedence ladder |
| `knowledge/tokenize.py`, `lexical.py`, `retrieval.py` | metadata-filtered lexical (BM25) passage retrieval, independent of resolution |
| `knowledge/plan_binding.py` | `bind_sourced_inputs()`, `build_loan_terms()` — the only Phase 4 seam |
| `sources/knowledge/loader.py` | `FileCorpusStore` — reads the committed corpus, never raises |
| `discovery/knowledge_acquisition.py` | `acquire_finance_knowledge()` — the impure acquisition layer |
| `scripts/build_knowledge_corpus.py` | ETL stage 1: operator text -> section-aware chunks |
| `scripts/build_parameter_registry.py` | ETL stage 2: `extract` (dual-LLM gate, publish or drop), `verify` a committed registry |
| `scripts/knowledge_extraction_prompts.py` | Prompt text for the extractor/verifier gate |

## The three "never fabricate a number" checks

Every committed `SourcedParameter` row survives three checks before it can
ever reach a plan:

1. **`value_token` occurs in `evidence_quote`** — a `SourcedParameter` model
   validator (`models/parameters.py`); a row failing this cannot even be
   constructed.
2. **`normalize_value(value_token, normalization) == value`** — the same
   validator; re-derives the stored value from the printed substring using a
   pure, stdlib-only function, so a value cannot silently drift from what
   the document actually printed. `scripts/build_parameter_registry.py
   --verify` re-runs this over the committed file before it can be
   published.
3. **`evidence_quote` occurs verbatim (whitespace-normalised) in the cited
   chunk's actual `text`** — checked only at load time
   (`sources/knowledge/loader.py`, where the real chunk is available),
   counted as `parameters_rejected_unverified_quote` and dropped, never kept
   "just in case."

A row that fails any of the three does not reach `.parameters()`, does not
reach the resolver, and cannot reach a plan. `KnowledgeAcquisitionReport`
counts every rejection kind separately (`parameters_rejected_unverified_quote`,
`parameters_rejected_unknown_chunk`, `parameters_rejected_tier_floor`,
`parse_errors`) so a degraded load is visible, never silent.

## Source hierarchy and precedence

`knowledge/resolver.py` applies eight filters (name, tier floor, jurisdiction,
scheme, category, loan band, staleness/supersession, unresolved conditions)
before ranking survivors by:

1. Higher `SourceTier` — the enum's own fixed declaration order
   (`GOVT_PRIMARY > REGULATOR > PUBLIC_SECTOR_INSTITUTION > INDUSTRY_BODY >
   SECONDARY`), never `KnowledgeConfig.tier_weight` (that value only scales
   the reported *confidence*, never which candidate wins).
2. Narrower `Applicability.specificity()` (district > state > national, then
   scheme-specific, category-specific, loan-band-specific).
3. Later `applicability.effective_from`.
4. Later source `DocumentRecord.published_on`.

A single top-ranked survivor resolves. Several tied at the top rank resolve
only if they **all** state the same `(value, unit)` (`RESOLVED`, with every
agreeing `document_id` recorded and a confidence bonus); tied survivors with
**different** values are `CONFLICTING` — `chosen` stays `None`, never
averaged, never arbitrarily picked. Zero survivors, with a candidate that
existed but was stale or superseded, is `STALE_ONLY`; a candidate held back
only for an unresolved qualifying condition is `CONDITIONS_UNRESOLVED`;
otherwise `NO_EVIDENCE`.

## Confidence

Evidence quality only — never viability, and never a factor in which
candidate is chosen (see above). Mirrors Phase 2C's shape
(`market/demand.py`'s freshness/source-tier confidence):

```
confidence = tier_weight[tier] × freshness(effective_year) × applicability_match
           × agreement_multiplier
capped at single_document_ceiling unless >= 2 independent documents agree
```

`freshness` uses the identical formula as `market/demand.py::_freshness`,
with `KnowledgeConfig.reference_year` standing in for a clock read exactly as
`DemandConfig.reference_year` does. `single_document_ceiling` is the Phase 5
analogue of `discovery/confidence.py`'s `SINGLE_SOURCE_CEILING`: one
document, however authoritative, cannot justify top confidence.

A parameter's confidence, the Phase 3 opportunity score, Phase 2C/2D's
market/demand confidence, and Phase 4's `assumption_share` are **five**
separate measurements. None of them multiplies or implies another
(`KnowledgeConfig.caveats`, echoed nowhere into a Phase 4 result — Phase 5
confidence lives only on `ParameterResolution`, never on `FinancialInput`
itself beyond its own `confidence` field, which Phase 4 already supports for
any `SOURCED` value).

## The Phase 4 seam — what binds and what does not

`knowledge/plan_binding.py::bind_sourced_inputs()` and `build_loan_terms()`
are the only functions in the codebase, outside `finance/fit.py`'s own
direction, that cross a phase boundary into Phase 4's models. Auditing every
`ParameterName` against Phase 4's actual field shapes:

* `interest_rate_pct`, `loan_tenure_months`, `moratorium_months` bind as a
  bundle, via `build_loan_terms`, into `LoanTerms` — all-or-nothing, since
  `LoanTerms` itself is all-or-nothing at construction.
* `licence_fee_inr` binds as a new `CostLine(kind=LICENCE)`.
* `gross_margin_pct` / `cogs_pct` bind into `OperatingCostInput`'s matching
  field, whichever isn't already claimed (the two are mutually exclusive on
  the plan itself).
* `inventory_days` binds into `WorkingCapitalInput.inventory_days`.
* `promoter_margin_pct`, `subsidy_pct`, `loan_ceiling_inr`, and
  `security_deposit_months` bind **nowhere** — each would need Phase 4
  arithmetic (multiplying a resolved percentage by project cost, or turning
  a months-of-rent figure into rupees) that this phase must not perform, or
  has no corresponding Phase 4 plan-driver field at all. They stay fully
  visible on `FinanceKnowledgeEvidence` and are reported as
  `UnboundParameter`, naming exactly why.

A field that already carries **any** value — `user_provided`, `assumed`, or
an earlier `sourced` one — is never overwritten; a difference is only noted.
`FinancialInput.retrieved_at` always comes from the chosen row's source
`DocumentRecord.retrieved_at` (via `ParameterResolution.retrieved_at`), never
the acquisition run's own clock, so a bound plan's assessment is
byte-identical across repeated runs
(`tests/test_knowledge_to_finance.py::test_repeat_runs_are_byte_identical`).

**The concrete trap this design exists to avoid:** `finance/fit.py` consumes
`FinancingInput.declared_margin_requirement` as absolute rupees
(`Unit.INR`), but scheme documents state margin as a percentage of project
cost. Binding a resolved `PROMOTER_MARGIN_PCT` (`Unit.RATIO`) there would
have silently produced `required_promoter_margin_inr = 0`. `plan_binding.py`
checks a resolved value's unit against `PARAMETER_SPEC[name].unit` and
**rejects rather than coerces** any mismatch —
`tests/test_knowledge_plan_binding.py::test_promoter_margin_pct_never_binds_regardless_of_status`
pins this.

## What Phase 5 does NOT claim

- **A resolved parameter is not a scheme-eligibility determination.**
  Conditions stated in the source document (`applicability.conditions`) are
  carried verbatim and never interpreted or resolved by this engine — the
  same "we retrieve rules, we do not decide eligibility" boundary Phase 4
  drew for itself.
- **Confidence is not a probability of loan approval**, and it is not a
  measure of the business — it measures how well a *fact* is evidenced
  (source tier, freshness, applicability match, cross-document agreement).
- **A conflicting or stale reading is never resolved by averaging or
  guessing.** It is reported as unresolved, naming every candidate, so a
  human can see what the corpus actually contains.
- **This is not a scheme router.** Given a named scheme, it looks up that
  scheme's parameters; it does not decide which scheme applies to a given
  entrepreneur, business, or state (CLAUDE.md §4's separate credit-routing
  component, not built here).
- **No LLM ever selects, extracts, or approves a value on the request
  path.** Two LLMs (an extractor and an independent verifier) do the
  extraction itself, but only offline, in `extract`, and only when they
  agree — a single model's proposal is never sufficient on its own, and
  every published row still passes all three mechanical "never fabricate"
  checks plus the tier/jurisdiction-scope checks before it can be
  committed.

## Edge cases

- Corpus directory absent → every requested name resolves `NO_EVIDENCE`;
  `KnowledgeAcquisitionReport.corpus_present=False`; one warning; nothing
  binds; the plan comes back exactly as it would with Phase 5 uninstalled.
- Corpus directory present but empty (the shipped state) → identical
  behaviour to "absent", except `corpus_present=True` and every count is
  zero.
- A malformed JSONL line, CSV row, or manifest → skipped and counted
  (`parse_errors`), never raised, the rest of the corpus still loads.
- A row citing a `chunk_id` that does not exist → dropped, counted
  (`parameters_rejected_unknown_chunk`).
- A row below its parameter's tier floor → dropped at **both** the loader
  (defense in depth) and the resolver (in case a caller ever constructs a
  candidate list directly, bypassing the loader) — counted at whichever
  layer first sees it.
- Two documents at the same tier and specificity stating different values →
  `CONFLICTING`, never averaged.
- A query with no `state`/`scheme`/`category` given → every filter that
  compares against an unset query field passes automatically (only an
  explicit *disagreement* rejects); if that leaves several genuinely
  independent candidates tied at the top rank, the honest outcome can still
  be `CONFLICTING` — see
  `tests/test_knowledge_resolver.py::test_multiple_schemes_with_no_scheme_filter_can_legitimately_conflict`.
- A resolved value whose unit does not match `PARAMETER_SPEC` → the binder
  rejects it, never coerces.
- No `DocumentRecord.retrieved_at` available for a chosen row → the binder
  refuses to build a `FinancialInput` (that field is required by
  `InputKind.SOURCED`) and reports it as unbound, rather than defaulting.

## Known limitations

- **No PDF/DOCX/HTML parsing in the package or the ETL.** `scripts/
  build_knowledge_corpus.py` reads plain text an operator has already
  extracted, to avoid a new dependency (CLAUDE.md §4.1) before the corpus is
  large enough to justify one.
- **No vector/embedding retrieval.** Lexical (BM25) + metadata filtering
  only, because the resolver never consults retrieval at all — a retrieval
  quality gap cannot corrupt a resolved value, only the passages shown
  beside it. `docs/phase-5.md` (this file) records the trigger conditions
  for revisiting that decision: corpus size and latency past a stated
  budget, a measured recall gap on a held-out paraphrase query set, or a
  Phase 6 need lexical genuinely cannot serve — whichever comes first,
  evaluate `pgvector` on the existing PostgreSQL target before anything
  else (CLAUDE.md §4.1).
- **`SUBSIDY_PCT` is percentage-only.** A scheme stating a flat-rupee
  subsidy has no home in the current `ParameterName` set; adding one is a
  model change, not attempted here.
- **`extract` scans every chunk in the corpus, with no keyword pre-filter.**
  Deliberate: a regex/keyword gate risks silently skipping a parameter
  phrased outside a fixed word list. Trade-off: 2 LLM calls per chunk on
  every run, spending free-tier quota proportional to corpus size — fine at
  the current handful-of-documents scale, worth revisiting if the corpus
  grows substantially.
- **`extractor_model`/`verifier_model` from the same vendor reduce, not
  eliminate, the value of independent verification.** Two Gemini models of
  different generations still share more training/architecture than a
  cross-vendor pair would; a systematic blind spot both models share (e.g.
  a language/formatting convention neither reads correctly) would not be
  caught by their agreement matching.
- **Phase 3's `_CAPITAL_BANDS` is untouched.** `market/opportunity_config.py`
  still marks it `[assumption], pending Phase 5 scheme retrieval` — wiring
  real capital bands through this registry is deferred to avoid reopening
  approved Phase 3 test expectations, exactly as Phase 4 deferred it before.
- **`FinanceConfig.max_assumption_share` is untouched.** A `SOURCED` input
  mechanically lowers `assumption_share`, but that ratio measures *how much*
  of a plan is evidenced, not *how well*. Tightening the gate is a Phase 4
  decision this phase does not make.

## The unsupported-parameter register

Mirrors `docs/phase-3.md`'s "unsupported-factor register" (§12): of the
eleven `ParameterName`s this phase knows how to resolve, the shipped
(empty) corpus supports **none of them with real evidence** — that is
expected at this stage (see "What Phase 5 does NOT claim" and
`data/knowledge/SOURCES.md`). Once real documents are ingested, update this
table with what is actually covered, not what the machinery merely permits:

| `ParameterName` | binds into | tier floor | real-document coverage |
|---|---|---|---|
| `interest_rate_pct` | `LoanTerms` (bundle) | govt/regulator/PSU bank | none yet |
| `loan_tenure_months` | `LoanTerms` (bundle) | govt/regulator/PSU bank | none yet |
| `moratorium_months` | `LoanTerms` (bundle) | govt/regulator/PSU bank | none yet |
| `licence_fee_inr` | new `CostLine(LICENCE)` | govt/regulator | none yet |
| `gross_margin_pct` | `OperatingCostInput.gross_margin_pct` | govt/PSU bank/industry body | none yet |
| `cogs_pct` | `OperatingCostInput.cogs_pct` | govt/PSU bank/industry body | none yet |
| `inventory_days` | `WorkingCapitalInput.inventory_days` | govt/PSU bank/industry body | none yet |
| `promoter_margin_pct` | nowhere (evidence-only) | govt/regulator/PSU bank | none yet |
| `subsidy_pct` | nowhere (evidence-only) | govt/regulator/PSU bank | none yet |
| `loan_ceiling_inr` | nowhere (evidence-only) | govt/regulator/PSU bank | none yet |
| `security_deposit_months` | nowhere (evidence-only) | govt/regulator/PSU bank | none yet |

The last four rows are unbindable by design (see above), not by omission —
they would stay in this state even with full document coverage, unless a
later Phase 4 iteration adds the arithmetic or a Phase 5 iteration adds a
converted parameter name.

## Phase 5 → Phase 6 / Phase 8

Two seams this phase deliberately leaves for later, un-built components,
consistent with CLAUDE.md §25's phase boundaries:

1. **Narrative composition.** `ParameterResolution.citation` and
   `RetrievedPassage` carry everything a Phase 6 LLM layer needs to explain
   *why* a figure is what it is, with a citation — but composing that
   explanation is explicitly not this phase's job (CLAUDE.md §3.1: the LLM
   explains from structured results, it does not compute them).
2. **DPR annexures.** A Phase 8 DPR generator can walk
   `FinanceKnowledgeEvidence.resolutions` and `.passages` to build a sourced
   annexure listing every rate/fee/margin used and its citation, including
   the ones that stayed unbound and why — the same four-way distinction
   (source fact / retrieved rule / calculation / AI explanation) CLAUDE.md
   §23 asks every DPR annexure to preserve.

## ETL CLI

```bash
python scripts/build_knowledge_corpus.py \
    --raw-dir data/knowledge/raw --built-at 2026-01-15T00:00:00+00:00 \
    --out-dir data/knowledge

python scripts/build_parameter_registry.py extract \
    --corpus-dir data/knowledge --out data/knowledge/parameters.csv \
    --verified-on 2026-01-15
# -> the dual-LLM gate runs and writes data/knowledge/parameters.csv
#    directly (full overwrite) — every published row already agreed between
#    the extractor and verifier models and passed every mechanical/scope
#    check; nothing further to sign or merge.

python scripts/build_parameter_registry.py verify --corpus-dir data/knowledge
```

No flag exists on `scripts/discover_businesses.py` for Phase 5, for the same
reason Phase 4 has none: a real-location run's financial/parameter inputs
come from evidence and the entrepreneur's own statements, never invented to
fill a demo gap.
