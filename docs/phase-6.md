# Phase 6 — Application Backend + LLM Orchestration

Status: implemented on branch `phase-6-orchestration`. This is a decision log and
design record, not a tutorial — read `CLAUDE.md` §25 Phase 6 and the approved plan
first.

## What this phase delivers

1. **`app/`** — a channel-neutral backend service (`AdvisoryService`) that owns
   session lifecycle and persistence. No HTTP server ships in this phase (CLAUDE.md
   §25 assigns transport to Phase 7); `app/` is an in-process Python API.
2. **`conversation/`** — a pure state machine: slots with provenance, a 15-node step
   DAG, cascade invalidation, a recommendation combiner, deterministic rendering,
   and a grounding check for LLM-authored prose.
3. **`llm/`** — the provider edge, prompts, structured-output parsing, and
   `tools.py` (the *only* module in the repository that calls a Phase 1-5 engine).
4. **The first time the full Phase 1→5 loop actually runs**, multi-turn, through a
   backend API. `scripts/discover_businesses.py` never reached Phase 4/5.

## Architecture: deterministic workflow, constrained LLM

Every edge in the pipeline is a data dependency (`compute_competition_metrics`
needs a `CompetitorAnalysisResult`; `acquire_opportunity_evidence` needs a union
`DiscoveryResult`), not a judgement call worth spending LLM tokens rediscovering.
`conversation/workflow.py` names the 15 `StepId`s and their dependency edges;
`conversation/planner.py::decide` is a fixed precedence ladder that either asks the
user something or names exactly one step to run next. `llm/tools.py::STEP_RUNNERS`
is where a `StepId` actually becomes a call into `market/`, `finance/`,
`knowledge/`, `discovery/`, `sources/`, `geocoding/`, `database/` — every real
signature used verbatim, `config=`/`cfg=` inconsistency preserved (checked by
`inspect.signature` in `tests/test_conversation_workflow.py`).

The LLM (`llm_enabled=True`) is confined to two narrow jobs, both structurally
checked, never trusted:

* **It cannot invent a number.** It emits `(slot, value_token, normalization)`;
  `conversation/deltas.py::resolve_slot_value` requires `value_token` to occur
  verbatim in the user's own text, then re-derives the value through Phase 5's
  `normalize_value` (`models/parameters.py`) — the same check
  `SourcedParameter` uses so a reviewed document row cannot be fabricated, reused
  here for user-stated numbers.
* **It cannot pick a business category.** It only ever supplies the raw phrase
  (`PROPOSED_BUSINESS_TEXT`); `market/proposed.py::resolve_proposed_business`
  (already existing, deterministic) owns the taxonomy mapping.

`llm_enabled=False` is a fully supported mode, not a degraded one (CLAUDE.md
§3.1). Per the explicit instruction this phase was implemented under: **without an
LLM, this layer does not attempt arbitrary free-text extraction.** A channel must
supply structured `SlotUpdateInput`s directly (`app/dto.py::MessageRequest`) —
the same deterministic `value_token`-in-text check applies either way, so both
paths funnel into the identical `llm/orchestrator.py::run_turn` and reach the
same recommendation. `tests/test_app_service.py` proves this by never
constructing an `LlmProvider` at all.

## The step DAG and cascade invalidation

`conversation/artifacts.py` fingerprints each step as a SHA-256 over its
*declared inputs* — the relevant slots (`STEP_INPUT_SLOTS`), its upstream steps'
own fresh fingerprints, and the config it was called with (`llm/tools.py::
CONFIG_BLOBS`) — **never over outputs**, because several engine results carry a
wall-clock field (`DemandEvidence.acquired_at`, `NormalizedBusiness.
retrieved_at`) that would otherwise bust every fingerprint on every turn.
`invalidate()` walks the DAG once, dropping any artifact whose freshly
recomputed fingerprint no longer matches what was stored when it last ran; a
step re-runs exactly when something it actually depends on changed.

Two subtleties worth recording, because they are not obvious from the plan's
prose alone and were resolved during implementation:

* **`FINANCIAL_FIT` → `OPPORTUNITY` is a real dependency edge, not a cycle**, but
  it is *optional*: `OPPORTUNITY` may run before `FINANCIAL_FIT` exists (with
  `financial_fit=None`) and re-runs once it appears, because its appearance (not
  just its content) enters the fingerprint. `workflow.py::DAG_ORDER` is a true
  topological sort over **every** declared edge (required and optional) via
  Kahn's algorithm — a plain insertion-order table is not sufficient once an
  optional edge exists, because `compute_fingerprints` needs every dependency's
  fingerprint already computed when it visits a step.
* **`DEMAND_EVIDENCE` must not inherit `DISCOVER`'s fingerprint**, even though it
  requires `DISCOVER` to be *ready*. `acquire_demand_evidence` reads only
  `discovery.resolved_place` / `.query_text` / `.requested_radius_m` — never
  `.category` or `.businesses` — so a category change would otherwise wrongly
  bust the fixed-selector Overpass union query it exists to avoid repeating.
  `artifacts.py::_FINGERPRINT_UPSTREAM_OVERRIDE` fingerprints it directly off the
  location slots instead of off `DISCOVER`'s (category-sensitive) fingerprint.

**Verified invalidation behaviour** (`tests/test_conversation_invalidation.py`):
a cash-only correction re-runs `OPPORTUNITY` and everything downstream of it
(including the finance chain, since `finance/pipeline.py` reads
`plan.profile.liquid_cash_inr` for a plausibility check) — but **zero impure
(network/disk) steps re-run**, which is the actual payoff, not the exact literal
set of two steps the plan's illustrative prose named before this dependency was
traced through the real code. A category change re-runs every category-dependent
step but `DEMAND_EVIDENCE` survives.

## Slots and provenance

`SlotState` is a strict superset of `models/finance.py::InputKind`
(`MISSING`/`AMBIGUOUS`/`DECLINED` added on top), and `Slot`/`SlotValue`
validators mirror `FinancialInput._kind_shape` directly. Corrections append to
`Slot.history`, never overwrite. `conversation/plan_builder.py` is the **only**
module allowed to construct a `FinancialInput` or `EntrepreneurProfile` inside
`conversation/` (AST-enforced, `tests/test_conversation_purity.py`) and emits
`InputKind.USER_PROVIDED` only — never `ASSUMED` (a missing driver becomes
`INSUFFICIENT_FINANCIAL_EVIDENCE` plus a question, not an invented number) and
never `SOURCED` (that kind is reserved for `knowledge/plan_binding.py`, called
from `llm/tools.py`'s `BIND_PLAN` runner, never from inside `conversation/`).

A financial driver is **not** gated as a hard precondition anywhere in the DAG:
`BUILD_PLAN` always runs once `RESOLVE_PROPOSED` has (with whatever `FinancialInput`s
the entrepreneur has actually stated), and `finance/assessment.py`'s own
`missing_core_drivers` reporting is what surfaces a gap — `conversation/
clarify.py`'s map is asserted, in `tests/test_conversation_clarify.py`, to have
exactly the four keys that function can emit. This means the whole DAG runs to
completion in one turn whenever the two structural blockers (`LOCATION_TEXT`,
`PROPOSED_BUSINESS_TEXT`) are supplied — `test_app_service.py`'s milestone test
reaches `RECOMMEND` from a single `send_message` call.

## Evidence bundle, rendering, grounding

`conversation/bundle.py::build_bundle` extracts a short, named `Fact` list from
whichever step artifacts already exist — each carrying `origin` (CLAUDE.md §23's
four-way distinction) and a `render` string that is the *only* form a number may
take in narrative prose. This exists because feeding a raw engine result to an
LLM is both impossible and unsafe: one `FinancialAssessmentResult` serialises to
roughly 10k tokens.

`conversation/render.py` is the deterministic renderer — always available,
never blank, never an exception. `conversation/grounding.py::check_section`
checks LLM-authored prose against the bundle **per section, scoped by that
section's own citations** (not the whole bundle), plus a banned-phrase list that
is the union of the three already in this repo (`scripts/phase3_demo.py:382`,
`scripts/phase4_demo.py:250`, `tests/test_finance_guardrails.py:33-34`). The
load-bearing property, proven rather than merely claimed
(`tests/test_conversation_grounding.py`): every fact, calculation and verdict is
computed by pure code *before* an LLM is ever invoked, and `check_section` has no
write path back into any of it — a fully successful prompt injection can at most
cause its own section to be rejected and replaced by the template. It cannot
change a number or a verdict.

**Stated limits.** A presence/citation check catches a fabricated number; it
cannot catch *mis-attribution* (a real number from the bundle, under the wrong
label) or a qualitative claim that carries no digits. Numeral matching is
ASCII-digit only (mirrors `models/parameters.py::_NUMERAL_RE`); Devanagari
digits or Hindi word-numbers degrade to a template fallback, which is the honest
outcome, not a silent gap.

## The Bhagwanpur scenario, honestly

With `data/knowledge/` shipped empty (as it is in this repository),
`resolve_parameters` returns `NO_EVIDENCE` for every name, `build_loan_terms`
returns `None` (all-or-nothing `LoanTerms`), and no interest rate, tenure or
margin can appear anywhere in the narrative — `LoanTerms` and
`declared_margin_requirement` simply cannot be constructed without a resolved
or user-stated figure. CLAUDE.md §31's illustrative line ("promoter margin
required ≈ ₹0.8 L; loan ≈ ₹4.6 L @ ~11%") is **not truthfully producible** with
the shipped corpus, and this phase says so rather than papering over it —
`scripts/phase6_demo.py`'s transcript A demonstrates exactly this, and
`tests/test_phase6_demo.py::test_phase6_demo_transcript_a_never_states_an_interest_rate`
gates it. Transcript B runs the identical scenario against the visibly
synthetic corpus under `tests/fixtures/knowledge/` (see its own README: no
figure in it describes a real scheme), where a rate does resolve.

## What is deliberately deferred or simplified relative to the approved plan

Recorded here rather than silently dropped, per CLAUDE.md §29's "state
assumptions and limitations":

* **The planner ladder** implemented has 6 rungs (contradiction, disambiguation,
  run-step, structural clarification, deliver-final, deliver-partial), not the
  full 10 the plan sketched (`SOURCE_DOWN` retry-budget tracking and a separate
  `PROPOSE_PIVOT` turn are folded into the delivered narrative/warnings instead
  of a dedicated rung) — the DAG's degrade-gracefully design meant most of that
  branching was unreachable once financial drivers stopped gating step
  readiness.
* **LLM narrative generation** (an `explanation` prompt producing per-section
  prose, checked by `grounding.py`, swapped in over the template) is not yet
  wired into `app/service.py::send_message` — `conversation/grounding.py` and
  `llm/prompts.py::EXPLANATION_INSTRUCTIONS` exist and are tested standalone,
  but every reply today is the deterministic renderer. `Narrative.
  generated_by` will read `"llm"` for an accepted section once this is wired.
* **Numeric grounding** does not yet generate rendering variants
  (`650000` / `6,50,000` / `6.5 lakh`) — it matches comma-insensitively only.
* **Injection defence** relies on structural separation (untrusted text never
  reaches extraction's schema-constrained output; grounding never has write
  access to a fact) rather than the plan's nonce-delimited passage wrapping,
  since no passage-in-prompt narrative call exists yet to wrap.
* **`SqlSessionRepository`** stores the whole `ConversationSession` as one JSON
  blob per row (documented in `database/session_schema.py`), not decomposed
  columns — a deliberate simplification, not an oversight.
* An interactive `chat` REPL, `RecordingLlmProvider` + `--record`, and a
  `--why <fact_id>` provenance-dump affordance (all "SHOULD" items in the
  approved plan) are not implemented.

## Running it

```bash
pytest
ruff check src tests scripts
ruff format --check src tests scripts
mypy
python scripts/phase6_demo.py
```

`git diff --stat` against `market/`, `finance/`, `knowledge/`,
`models/finance.py`, `discovery/service.py` must be empty; the one intentional
engine change is the additive `demand: DemandEvidence | None = None` keyword on
`discovery/opportunity_acquisition.py::acquire_opportunity_evidence` — its
default reproduces the prior behaviour exactly.
