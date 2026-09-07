# Phase 4 — Deterministic Financial Engine

Design notes for what is built. Authoritative rules live in `CLAUDE.md`
(§14 asset-aware model, §15 financial engine, §16 stress testing, §17
moratorium analysis, §22 confidence, §30 "do not").

## Scope

Phase 3 scores a shortlist of candidate businesses against a location, a
market label, and an entrepreneur's cash/assets/experience. It stops exactly
where money begins: its capital check is an ordinal screen against a
hand-authored typical-cost table, captioned in five places as *not a
financial assessment*.

Phase 4 answers:

> Can this business actually be **financed** and **survive financially** for
> this entrepreneur — and if not, what exactly breaks first?

The risk this phase is built to guard against is becoming a plausible-looking
profitability calculator whose numbers rest on invented sales and margins.
The countermeasure is structural: every financial figure that reaches a
calculation is a `FinancialInput`, tagged `user_provided` / `sourced` /
`assumed` / `calculated` with a rationale. There is **no default** anywhere —
in this module, `finance/*`, or the config — for revenue, price, volume,
gross margin, a project-cost line, an interest rate, a tenure, or a
moratorium treatment. Omitting one of these core drivers does not produce a
number; it produces `INSUFFICIENT_FINANCIAL_EVIDENCE` naming the missing
driver. Only genuinely structural modelling conventions (a contingency
percentage, an opex cushion in months, DSCR comfort thresholds, stress-test
deltas) may fall back to a configured default, and every such fallback is
recorded in the result's `notes`.

Phase 4 computes **no** market or opportunity information — it consumes an
entrepreneur profile and a caller-authored financial plan, never OSM data, a
market label, or a Phase 3 score. It hands one result back across the
already-declared Phase 3 seam (`FinancialFitInput`) and changes nothing about
how Phase 3 scores a candidate. No RAG, no LLM, no scheme retrieval — every
rate, tenure, margin and ceiling here is a caller-supplied input, never a
hard-coded scheme fact.

## Data flow

```
        ┌─ IMPURE (Phase 5+; NOT built in Phase 4) ────────────────┐
        │ retrieved scheme rates, margins, tenures, benchmark      │
        │ cost ratios -> FinancialInput(kind=SOURCED, source_ref=…)│
        └────────────────────────┬─────────────────────────────────┘
                                 │ (Phase 4: the caller/fixture supplies these directly)
                                 ▼
                      FinancialPlanInput          (models/finance.py)
                                 │
        ┌─ PURE (no I/O, clock, RNG, LLM, float) ──────────────────┐
        │ finance/costs.py       project cost + working capital    │
        │ finance/operations.py  revenue, opex, margin, break-even │
        │ finance/debt.py        EMI, amortisation, moratorium     │
        │ finance/cashflow.py    monthly cash flow                 │
        │ finance/dscr.py        the three DSCR windows            │
        │ finance/pipeline.py    shared core run + rungs 2-4       │
        │ finance/stress.py      scenarios = input transform + rerun│
        │ finance/assessment.py  rung 1 + rung 5 + orchestration   │
        └────────────────────────┬─────────────────────────────────┘
                                 ▼
                      FinancialAssessmentResult
                                 │
                      finance/fit.py  ── the ONLY module importing market/
                                 ▼
                      FinancialFitInput  →  score_opportunities(financial_fit=…)
                                            (Phase 3, unmodified)
```

`finance/pipeline.py` is an internal factoring, not a phase-boundary module.
Both `assessment.py` (the base case) and `stress.py` (each scenario) call its
`run_core_pipeline` / `decide_status` so a stress scenario can never diverge
from the base pipeline's own logic, without `assessment.py` and `stress.py`
importing each other — a small adaptation from the original plan's flat file
list, made to avoid a circular import, not a new public concept.

## Modules (`src/vyaparsarathi/finance/`, plus `models/finance.py`)

| module | contents |
|---|---|
| `models/finance.py` | `InputKind`, `Unit`, `FinancialInput`, `AssumptionRegister`, `CostLine`, `AssetSpendOffset`, `ProjectCostInput`, `WorkingCapitalInput`, `RevenueInput`, `OperatingCostInput`, `OpexLine`, `MoratoriumTreatment`, `LoanTerms`, `FinancingInput`, `FinancialPlanInput` — the seam |
| `finance/money.py` | `Decimal` policy: `q_money`/`q_rate`/`q_ratio`, `rupees()`, `to_paise()`, `whole_rupees()`, `annual_pct_to_monthly_rate()`, the `MoneyINR`/`RateFrac` pydantic types |
| `finance/finance_config.py` | frozen `FinanceConfig` + `DEFAULT_FINANCE_CONFIG`: structural conventions, DSCR thresholds, `StressScenario` table, fixed caveats |
| `finance/results.py` | every sub-calculation's result model: `ProjectCostResult`, `WorkingCapitalResult`, `RevenueScheduleResult`, `OperatingCostResult`, `BreakEvenResult`, `AmortisationPeriod`/`MoratoriumPeriod`/`DebtScheduleResult`, `MonthlyCashFlow`/`CashFlowResult`, `DSCRPeriod`/`DSCRResult` |
| `finance/costs.py` | `compute_working_capital`, `compute_project_cost`, `compute_capital_gap`, `compute_promoter_contribution_pct` |
| `finance/operations.py` | `compute_revenue_schedule`, `compute_operating_costs`, `compute_break_even` |
| `finance/debt.py` | `compute_emi`, `compute_debt_schedule` (EMI, amortisation, all four moratorium treatments) |
| `finance/cashflow.py` | `compute_cash_flow` |
| `finance/dscr.py` | `compute_dscr` |
| `finance/pipeline.py` | `run_core_pipeline`, `decide_status` (rungs 2-4) — shared by `assessment.py` and `stress.py` |
| `finance/stress.py` | `run_stress_scenarios`, `find_breaking_point` |
| `finance/assessment_models.py` | `FinancialFeasibilityStatus`, `FinanceLadderRung`, `FinanceEvidenceRef`, `FinanceFinding`, `StressResult`, `FinancialAssessmentResult` |
| `finance/assessment.py` | `assess_financials(plan, *, cfg=DEFAULT_FINANCE_CONFIG)` — the orchestrator |
| `finance/fit.py` | `to_financial_fit(result) -> FinancialFitInput` — the only module importing `vyaparsarathi.market` |

## Money, rounding and dates

**Decision: Indian rupees as `decimal.Decimal`, quantised to paise (2 dp),
`ROUND_HALF_UP`, never `float`.** Binary floating point cannot represent
Rs 0.01 exactly, which would make golden-value tests platform-fragile;
integer paise would work but pushes manual scaling into every multiplication
and division along a ratio-heavy path (interest, DSCR, contribution margin,
break-even). `Decimal` is stdlib, deterministic, and lets every rounding
point be named and tested individually (CLAUDE.md §4.2). `finance/money.py`
rejects a `float` at construction — both in the plain `rupees()` helper and
in every pydantic money/rate field — raising `FinancialInputError`, a
programming-error exception distinct from the engine's own "no calculation
ran" outcomes.

| Point | Rule |
|---|---|
| Every stored/emitted money field | quantised to paise, HALF_UP |
| Rate conversion | `monthly = annual_pct / 100 / 12` — nominal annual ÷ 12, the Indian reducing-balance convention (`rate_convention = "nominal_annual/12"`, echoed on every `DebtScheduleResult`); never an effective-rate 12th root |
| EMI | quantised **once**, HALF_UP, then held fixed for the whole schedule |
| Per-period interest | `q_money(opening_balance × i)` — quantised each period, so the sum of reported interest equals the reported total exactly |
| Final instalment | absorbs the residual (`principal_n = balance_{n-1}`), so `Σ principal_k == P` to the paise and the schedule closes at exactly `0.00` |
| Ratios (DSCR, margin, contingency %) | `Decimal`, quantised to 4 dp internally (`q_ratio`) |
| Phase 3 handoff (`capital_gap_inr`, `required_promoter_margin_inr`) | `whole_rupees()`, HALF_UP — the only place this engine rounds to a whole rupee |
| The global `decimal` context | **never mutated** — every rounding decision is an explicit call at a named point above, not ambient state |

**Dates and fractional periods.** The engine works entirely in integer month
indices: disbursement at month 0, operations from month 1, moratorium months
`1..m`, EMI months `m+1..m+n`. There are no fractional periods and no
calendar arithmetic, so there is no clock read and nothing platform-dependent.
`FinancialPlanInput.start_month_label` is an optional display-only string
(e.g. `"2026-04"`), never read by any calculation — the same
`reference_year`-not-`datetime.now()` idiom Phase 2C/2D use.

## Provenance — the four input kinds

Every `FinancialInput` carries a `kind`:

- **`user_provided`** — the entrepreneur stated it; unverified by definition
  (`source == "profile"`, no `confidence`, matching `EntrepreneurProfile`'s
  own `unverified` caption).
- **`sourced`** — an external document or dataset (the Phase 5 seam);
  requires `source`, `source_ref` (a citation) and `retrieved_at`.
- **`assumed`** — a configured MVP convention this engine is choosing to use;
  requires a non-empty `rationale` and a `source` starting with `"config:"`.
- **`calculated`** — derived by this engine; requires `calculated_from`
  (the labels it rests on) and forbids `source_ref` (a calculation is not a
  citation).

"Missing" is expressed by the *parent* field being `None`, never by a
`FinancialInput` whose own `value` is `None` — a `FinancialInput` that exists
always carries a concrete, non-negative value. This keeps "was this supplied
at all" unambiguous everywhere downstream.

`AssumptionRegister` collects every `FinancialInput` a run touched and
reports `assumption_share` — the financial analogue of Phase 2C's
`demand_data_confidence`: reported once, unmultiplied, and it never changes a
verdict. A plan whose `assumption_share` exceeds `FinanceConfig.
max_assumption_share` is itself a rung-1 `INSUFFICIENT_FINANCIAL_EVIDENCE`
case.

Two type-level guarantees back the "no scheme eligibility claims" rule:
`AssetSpendOffset.amount_avoided` must be `user_provided` or `sourced`, never
`assumed` — this engine never invents a rupee value for someone's asset — and
`FinancingInput.declared_margin_requirement` must not be `assumed` — Phase 4
never invents a scheme margin.

## The calculations

**Project cost.**

```
capex_subtotal = Σ CostLine.amount − Σ AssetSpendOffset.amount_avoided
contingency    = q_money(capex_subtotal × contingency_pct)   # capex only, never on working capital
project_cost   = capex_subtotal + contingency + net_working_capital
```

Opening inventory is counted once, inside working capital, never also as a
capex line.

**Working capital.**

```
inventory_requirement = opening_inventory (if stated directly)
                       , else (monthly_cogs / 30) × inventory_days
receivables  = (monthly_revenue / 30) × receivable_days
payables     = (monthly_cogs / 30) × payable_days
reserve      = monthly_fixed_opex × opex_cushion_months
net_working_capital = inventory_requirement + receivables + reserve − payables
```

Sized off the steady-state (fully-ramped) monthly revenue/COGS, not month 1's
ramped-down figure — a reserve is sized for the business the plan describes,
not its slow start. `receivable_days = 0` (cash rural retail) is still an
assumption and is recorded as such, never silent.

**Revenue.**

```
base(t)     = monthly_revenue, or unit_price × units_per_month
ramp(t)     = ramp_start_pct + (1 − ramp_start_pct) × min(t, ramp_months) / ramp_months
season(t)   = seasonality_index[(t−1) mod 12]     (flat 1.0 when none supplied)
revenue(t)  = q_money(base(t) × ramp(t) × season(t))
```

**Operating costs and profitability.** `cogs_pct = 1 − gross_margin_pct` (or
the reverse); `contribution(t) = revenue(t) − cogs(t) − variable_opex(t)`;
`operating_profit(t) = contribution(t) − fixed_opex`. `operating_profit` is a
**cash-basis operating surplus** — deliberately never called EBITDA, PAT, or
"net profit"; every `OperatingCostResult` states `depreciation_modelled=False`
and `tax_modelled=False`.

**Break-even.** The contribution-margin ratio is constant across months
(COGS and variable opex are flat percentages of revenue), so break-even is
computed directly from the percentages and fixed opex, not by scanning the
schedule:

```
cm_ratio                    = 1 − cogs_pct − variable_opex_pct
break_even_revenue_monthly  = fixed_opex / cm_ratio
break_even_incl_debt        = (fixed_opex + monthly_debt_service) / cm_ratio
break_even_units            = break_even_revenue / unit_price    (unit-price model only)
```

`cm_ratio ≤ 0` makes every break-even figure `None` with `undefined_reason`
set — never a huge or misleading number.

**EMI and amortisation.**

```
i   = annual_rate_pct / 100 / 12                         (nominal_annual/12)
EMI = P / n                                              if i == 0
    = P × i × (1+i)^n / ((1+i)^n − 1)                    otherwise, quantised once
```

Each period: `interest_k = q_money(balance_{k-1} × i)`; `principal_k =
EMI − interest_k` for `k < n`; the final period absorbs the residual balance.
If `EMI ≤ interest_1` (checked after quantisation — under the standard
formula this never happens for realistic loans, but paise rounding at a tiny
principal stretched over a long tenure can make the *quantised* EMI equal the
*quantised* first-period interest), the loan is flagged
`negative_amortisation` rather than looped.

## DSCR — the exact definition used here

```
CADS(window) = Σ operating_profit(t) over the window
             = revenue collected − COGS paid − operating expenses, BEFORE any debt service.
DSCR(window) = CADS(window) / debt_service(window)     (None when debt_service == 0)
```

**No depreciation add-back and no tax deduction** — neither is modelled
anywhere in this engine, so adding either back to CADS would be a fiction.
This is a cash-basis DSCR, stated verbatim as `DSCRResult.definition` on
every result. Three windows are computed (`monthly_dscr`, `annual_dscr`,
`project_period_dscr`); the headline pair reported onward is
`average_annual_dscr` (the mean over years that carry any debt service) and
`first_post_moratorium_year_dscr` (the annual window containing the first
EMI month). A moratorium-only year yields `None`, never `0` or infinity.

## Moratorium — four treatments, none of them a default

`moratorium_treatment` is a required field on `LoanTerms` with no default;
`moratorium_months > 0` with `treatment = NONE` is a caller-contract error
(`ValueError`), not a case this engine silently resolves.

| Treatment | During the moratorium | Principal at EMI start |
|---|---|---|
| `none` | — (`moratorium_months == 0`) | `P` |
| `interest_serviced` | interest paid monthly, principal deferred | `P` |
| `interest_capitalised` | interest compounds into the balance monthly | grows each month |
| `interest_accrued_paid_on_emi_start` | interest accrues, unpaid | `P`, plus a one-time lump-sum outflow at the first EMI month |

Capitalisation compounds month by month with per-period quantisation (not a
closed-form power), so it stays self-consistent with the amortisation
schedule's own convention.

## The feasibility ladder

A fixed, ordered precedence — the Phase 2D `LadderRung` pattern — with the
deciding rung recorded on every result.

| # | Rung | Status | Fires when |
|---|---|---|---|
| 1 | `missing_core_driver` | `INSUFFICIENT_FINANCIAL_EVIDENCE` | a revenue driver, a margin driver, a project-cost line, or fixed-opex lines are absent, or `assumption_share` exceeds the configured maximum |
| 2 | `funding_gap` | `FINANCING_GAP` | `capital_gap_inr > 0` |
| 3 | `not_serviceable` | `UNSERVICEABLE` | negative amortisation, or average annual DSCR below the unserviceable threshold, or cash is still negative at the horizon's end |
| 4 | `cash_stress` | `CASH_FLOW_STRESS` | any month's cash goes negative, or the minimum balance is below the configured floor, or the first post-moratorium year's DSCR is weak |
| 5 | `stress_sensitive` | `FEASIBLE_WITH_STRETCH` | rungs 1-4 all clear, but a stress scenario turns cash negative or worsens the status |
| 6 | `clears_all` | `FEASIBLE` | rungs 1-4 clear and no stress scenario worsens the status |

A requested loan's own rate/tenure/moratorium fields cannot be partially
missing — `LoanTerms` requires all of them at construction — so there is
nothing to check at rung 1 beyond whether a loan was requested at all; a
fully self-funded plan with no loan is a legitimate `FEASIBLE` case with
`dscr` left `None` throughout, never `UNSERVICEABLE`.

**There is no financial score.** `FinancialFeasibilityStatus` is a status
enum, never a number; nothing in this result is named `score`, `probability`,
`success`, or `guarantee`. It is also a *distinct* measurement from the
Phase 3 opportunity score, `market_data_confidence`, and `assumption_share` —
none of the four multiplies or implies another. A business can be
`underserved` at a high Phase 3 score and `UNSERVICEABLE` here; that is a
valid, expected combination.

## Stress testing

Each scenario in `FinanceConfig.stress_scenarios` is a pure transform of
`FinancialPlanInput`, applied by `finance/stress.py`, followed by a full
re-run of `finance/pipeline.py`'s own logic — a scenario can never diverge
from the base pipeline. Transforms never mutate their argument
(`model_copy`).

| Scenario | Transform |
|---|---|
| `revenue_down_20` / `revenue_down_30` | revenue × 0.80 / 0.70 |
| `margin_down_300bps` | gross margin − 3 percentage points |
| `opex_up_15` | fixed opex × 1.15 |
| `ramp_slower_2m` | ramp period + 2 months |
| `lean_season` | the whole horizon flattened to the *minimum* factor in the plan's own `seasonality_index` — a sustained low-season stretch, not the normal cyclical pattern. **Skipped with an explicit note, never fabricated, when no seasonality was supplied.** |
| `combined_downside` | revenue × 0.75, opex × 1.10, ramp + 2 months, together |

Stress scenarios only run once a plan is `FEASIBLE` on the base case
(rungs 1-4 clear); each reports its own status, and `breaking_point` names —
in one sentence — the first scenario, in config order, that worsens the base
status.

## What Phase 4 does NOT claim

- **The Phase 3 opportunity score is not financial feasibility.** They are
  independent measurements; a business can be commercially attractive and
  unfinanceable, or the reverse.
- **The opportunity score is not a profitability prediction**, and neither is
  anything in this engine — a `FEASIBLE` verdict says a *stated plan* holds
  together under the entered assumptions, not that the business will earn
  what the plan predicts.
- **Confidence and `assumption_share` are not probabilities of success.**
  They measure how well a plan is *evidenced*, never whether it will work.
- **Physical assets are not liquid cash.** An owned asset can reduce what a
  project needs to spend (`AssetSpendOffset`); it is never valued in rupees
  by this engine and never counts toward a scheme's promoter-margin
  requirement.
- **Financing assumptions are not scheme rules.** No rate, tenure, margin or
  ceiling in Phase 4 is authoritative — Phase 5 supplies retrieved ones, and
  until then every such figure here is either what the caller stated or an
  explicitly labelled MVP assumption.
- **This is not loan underwriting, credit scoring, or scheme eligibility
  determination.** It structures and pressure-tests a plan; a bank's or
  scheme's own appraisal is a separate process this does not replace.
- **The dominant limitation:** with no business-specific sourced cost or
  revenue data, a Phase 4 run is arithmetic on stated assumptions. It is a
  rigorous test of *whether a stated plan holds together*, not a forecast of
  what the business will actually earn.

## Edge cases

- No loan at all → `dscr` fields stay `None` throughout (undefined, not
  unserviceable); a fully self-funded plan can still be `FEASIBLE`.
- `moratorium_months == 0` with a stated (non-`NONE`) treatment is accepted
  with a "has no effect" note, not an error.
- `cm_ratio ≤ 0`, or both `cogs_pct` and `gross_margin_pct` supplied at once
  (rejected at the model boundary — supply one or the other).
- `unit_price` without `units_per_month` (or the reverse) is rejected at the
  model boundary as malformed, not treated as missing evidence.
- An `AssetSpendOffset` naming a `CostLine.label` that does not exist in the
  plan is not applied, and is noted.
- An offset larger than the line it reduces is floored at zero, with a
  warning — never a negative capex line.
- A negative value anywhere in a `FinancialInput` is rejected at construction
  (`ge=0` — every unit this engine carries is non-negative by construction).
- `promoter_cash_contribution` exceeding the entrepreneur's stated
  `liquid_cash_inr` is warned about, never silently clamped.
- Zero project cost / zero promoter cash does not crash;
  `promoter_contribution_pct` is `None` when project cost is zero (a ratio
  has no meaning there).

## Known limitations

- **No calendar dates.** Everything is integer month indices;
  `start_month_label` is display-only. A real DPR handoff will need to
  translate months to calendar dates at the presentation layer, not here.
- **Revenue-collection timing is modelled once, not twice.** The
  receivable/payable lag is funded upfront as part of `net_working_capital`
  (see `finance/cashflow.py`'s module docstring) rather than also lagged
  month-by-month in the cash-flow simulation — doing both would double-count
  the same timing gap. This is a stated simplification: revenue is treated
  as collected, and COGS/opex as paid, in the same month they are
  earned/incurred.
- **Only monthly repayment is modelled.** `LoanTerms.repayment_frequency`
  exists as a field so a different frequency is additive later, but only
  `MONTHLY` is implemented.
- **The `CapitalFit` screen in Phase 3 is not replaced.** `docs/phase-3.md`
  describes Phase 4 as replacing it wholesale; this build deliberately keeps
  Phase 3 untouched (§ below) and defers that replacement.
- **No per-category cost templates.** Deliberately: shipping a curated
  "typical margin for a grocery" table would reproduce exactly the
  fabrication risk this phase exists to avoid. Every market-facing figure
  must come from the caller.

## Phase 4 → Phase 5

`finance/fit.py::to_financial_fit()` populates the Phase 3 seam exactly:

- `feasible` — `True` for `FEASIBLE`/`FEASIBLE_WITH_STRETCH`, `False` for
  `FINANCING_GAP`/`CASH_FLOW_STRESS`/`UNSERVICEABLE` (a plan that runs out of
  cash is not "fundable and serviceable as structured", even short of
  `UNSERVICEABLE`), `None` for `INSUFFICIENT_FINANCIAL_EVIDENCE`.
- `capital_gap_inr` — from the funding arithmetic, rounded to whole rupees.
- `required_promoter_margin_inr` — populated **only** when the plan's
  `declared_margin_requirement` was supplied as `user_provided` or `sourced`
  (the model itself forbids `assumed` here). `None` otherwise — Phase 4 never
  derives a scheme margin.
- `notes` — the status, rung, headline DSCR, minimum cash position, the
  assumption-share summary, and an explicit disclaimer that scheme-margin
  satisfaction is not determined here.

`score_opportunities()` already accepts `financial_fit=` and already surfaces
these notes onto the matching candidate's `warnings` without touching any
score — no Phase 3 source file changes for Phase 4.

Two items explicitly deferred to Phase 5 (or a later Phase 4 iteration), not
built here:

1. **Replacing `CapitalFit` / `_CAPITAL_BANDS`** in `market/opportunity.py`
   with a real project-cost-based screen, and possibly adding a fourth
   Phase 3 `ScoreComponent`. Both are explicitly config + one-module changes
   per `docs/phase-3.md`, deferred to avoid re-opening approved Phase 3
   behaviour and moving its existing test expectations.
2. **Retrieved scheme parameters.** Phase 5 injects `FinancialInput(kind=
   SOURCED, source_ref=…)` values into `FinancingInput` /
   `OperatingCostInput` — no engine module changes required; the provenance
   model was built for exactly this.

## CLI

`scripts/phase4_demo.py` runs six fixture-based scenarios end to end, no live
APIs, no market/geospatial data:

```
./.venv/Scripts/python.exe scripts/phase4_demo.py          # all six
./.venv/Scripts/python.exe scripts/phase4_demo.py 3        # one
```

Every figure in every demo is an `ASSUMED` `FinancialInput` whose rationale
reads *"illustrative fixture value for the demo"* — none is a market survey,
a quotation, or a benchmark. The same builders are imported by
`tests/test_finance_demos.py` so the demo and the gate assert identical
behaviour. There is no `--finance` flag on `scripts/discover_businesses.py`:
a real-location run has no financial inputs, and inventing them to fill the
gap is exactly what this phase forbids.
