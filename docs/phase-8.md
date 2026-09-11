# Phase 8 — Detailed Project Report (DPR) generation

Status: implemented. This is a decision log and design record — read `CLAUDE.md`
§4 ("DPR generator"), §23 (provenance) and §25 Phase 8 first.

## What this phase delivers

1. **`dpr/` package** — a strongly-typed report model, a pure assembler, a
   deterministic PDF renderer, a JSON renderer, and a channel-neutral service.
2. **`scripts/generate_dpr.py`** — a CLI that writes PDF + JSON from a stored
   session or the bundled demo.
3. **`scripts/phase8_demo.py`** — the SIH demo report (CLAUDE.md §31 scenario),
   fully offline.

The DPR **only composes** what Phases 1–7 already produced. It runs no market
analysis, no financial calculation, no scheme lookup, and no LLM. `tests/
test_dpr_purity.py` AST-checks that the assembler modules import no engine
function, no I/O layer, no `reportlab`, and never read a clock.

## Data flow

```
AdvisoryService session  ──►  dpr.assemble_report(session, *, generated_at)  ──►  DprDocument
   (ConversationSession)          │  load_artifacts()  — reconstruct each StepArtifact.payload
                                  │                       into its pydantic model (STEP_RESULT_MODEL)
                                  │  build_bundle()    — reuse the Phase 6 four-way Fact projection
                                  │  build_profile()   — reuse the Phase 6 slot → EntrepreneurProfile
                                  │  build_citations() — copy citation fields off retrieved evidence
                                  │  per-section builders (dpr/sections.py, dpr/assemble.py)
                                  ▼
   render_pdf_bytes(doc) ─► PDF     render_json_str(doc) ─► JSON     (dpr/service.py writes both)
```

`generated_at` is **injected by the caller** (the CLI defaults it to `now`, a
boundary clock read; the demo pins it). The assembler itself never calls
`datetime.now()`.

## Provenance model

Every leaf figure is a `dpr.provenance.ProvenancedValue` with exactly one
`origin`:

| origin | meaning | required fields |
|---|---|---|
| `user_provided` | the entrepreneur stated it; unverified | — |
| `sourced` | an official document said it | `citation_id` |
| `calculated` | a deterministic engine produced it | `inputs` (named) |
| `assumed` | a configured MVP convention | `rationale` |
| `declared_config` | the SIH26091 problem statement's own 10%/90% structure — configuration this deployment states, **never** a retrieved scheme rule | `rationale` |
| `not_available` | input or evidence absent — shown, never hidden | `gap_reason` |

`declared_config` is kept distinct from `sourced` so the SIH split can never be
cited as external evidence. A `not_available` value carries no number and its
`display` is one of *"Not available from current evidence"* / *"Additional
input required"* / *"…declined…"* / *"…needs disambiguation"*.

Conversation slots map to origins exactly: `USER_PROVIDED → user_provided`,
`ASSUMED → assumed` (or `declared_config` when `source == "config:sih_scheme"`),
`SOURCED → sourced`, `CALCULATED → calculated`, and
`MISSING / DECLINED / AMBIGUOUS → not_available`.

## Report sections

Cover · Executive summary · Entrepreneur & project profile · Local market
assessment · Opportunity & alternatives · Project & operating plan · Financial
assessment · Scheme / compliance / knowledge evidence · Risks & SWOT ·
Assumptions, limitations & evidence confidence · Annexures (sources & citations,
calculation provenance, stated-input history, glossary).

Each section carries a `SectionStatus` — `rendered`, `partial`, or
`evidence_gap` with a worded `gap_note`. `DprDocument.evidence_gaps` is a
report-wide roll-up of every gap, echoed into the executive summary and the
assumptions section.

### When the DPR intentionally renders "insufficient evidence"

- **Executive summary** → `recommendation` is `not_available` when no `RECOMMEND`
  artifact exists; the verdict text is `insufficient_evidence` when the
  recommendation combiner itself returned that.
- **Local market assessment** → whole section is an `evidence_gap` when
  `DISCOVER` is absent or its status is not `ok` (location not found / ambiguous
  / no coverage / source unavailable — each with its own wording). `market_label`
  is `not_available` when `ASSESS_MARKET` is absent.
- **Opportunity & alternatives** → `evidence_gap` when `OPPORTUNITY` is absent or
  its status is not `ok`; the proposed score is `not_available` when the proposed
  business had no usable market reading.
- **Financial assessment** → a prominent *"Financial assessment incomplete"*
  section (status `partial`/`evidence_gap`) naming the exact
  `missing_core_drivers` whenever `ASSESS_FINANCE` is absent or its status is
  `insufficient_financial_evidence`. DSCR, cash-flow, break-even and stress
  figures that depend on the unstated drivers stay `not_available`; nothing is
  assumed in their place. (The SIH capacity screen from stated cash alone may
  still show a `declared_config` margin/loan/EMI — labelled as such, never a
  scheme fact.)
- **Scheme / knowledge evidence** → every `ParameterResolution` whose status is
  not `resolved` renders as `not_available` with the status (`no_evidence` /
  `conflicting` / `stale_only` / `conditions_unresolved`). With an empty corpus,
  *every* scheme parameter is `no_evidence` and `corpus_note` says so.
- **Risks & SWOT** → `evidence_gap` when `SWOT` is absent or returned
  `no_evidence` and there are no other structured risks.
- **Any slot** a section needs that is `MISSING` / `DECLINED` / `AMBIGUOUS` →
  "Additional input required" / "The entrepreneur declined…" / "Stated but
  unresolved — needs disambiguation".

## Deterministic report identity

`dpr.fingerprint.input_fingerprint(session)` is a SHA-256 over the *content*
inputs only: every step artifact's own fingerprint (which
`conversation/artifacts.py` already computes over declared inputs, never over
wall-clock outputs), every slot's provenance-bearing fields and history, the
resolved category, and the session warnings. It excludes `generated_at`,
`updated_at` and `computed_on_turn`. `report_id = "DPR-" + fingerprint[:16]`.

Consequence: regenerating a finished session's report on a different day yields
the **same `report_id` and the same structured body** (only the cover date and
`metadata.generated_at` differ). `render_pdf_bytes` is byte-deterministic too
(reportlab `invariant` mode fixes the PDF's own timestamp/ids).

## PDF renderer choice — `reportlab>=4,<5`

Recorded in `pyproject.toml`. Chosen because it is:

- **pure-Python wheels, no system libraries** — matters on the Windows dev box
  and for CI; `weasyprint` (HTML→PDF) was rejected for its pango/cairo/
  gdk-pixbuf native dependencies.
- **the long-standing standard** for programmatic PDF in Python, with a stable
  API.
- **exactly the feature set the requirements name**: `platypus.LongTable`
  auto-paginates a table across pages and repeats its header row (stress
  scenarios, stated-input history, citations); `BaseDocTemplate` + a
  `PageTemplate` + a two-pass `Canvas` give a repeated header (report id) and
  footer (page X of Y + a one-line disclaimer strip); reflowable `Paragraph`
  keeps a section readable whether it is one line or three pages.

`fpdf2` (lighter) was considered and rejected only because its cross-page table
behaviour is younger and less proven for the "bank / official review" bar. The
extra transitive deps `reportlab` pulls (`pillow`, `charset-normalizer`) are
themselves ubiquitous and wheel-packaged, and are not exercised by the
text-and-table-only DPR. `pypdf` is a **dev-only** dependency, used by the tests
to read generated PDFs back.

`tests/test_dpr_render_pdf.py` and `tests/test_dpr_page_breaks.py` assert: valid
`%PDF-…%%EOF`, every section title present, "Page 1 of N" and "Page N of N",
byte-determinism, JSON round-trip, a 250-row annexure table paginating with its
header row repeated on multiple pages, 60-row stress + 120-row calc tables
rendering, and a report with an entirely empty section still rendering.

## Safety wording

Fixed, human-authored constants in `dpr/disclaimers.py` (no LLM): the cover
disclaimer states the report is decision-support material, **not a loan
sanction / approval / guarantee / financial advice of record**; a market note
states the nearby-business data is a partial OSM sample and absence ≠
non-existence; a confidence note states market-data confidence and financial
feasibility are separate, never-multiplied measurements; a declared-config note
states the SIH split is not a scheme fact; and a "no LLM" note states the report
was assembled and rendered deterministically.

## Public interface & CLI

```python
from vyaparsarathi.dpr import DprService, assemble_report, render_pdf_bytes, render_json_str

doc = assemble_report(session, generated_at=when)                 # pure
DprService(session_repo).generate(session_id, generated_at=when,  # writes PDF + JSON
                                  pdf_path=..., json_path=..., overwrite=False)
```

`_write` refuses to replace an existing file unless `overwrite=True`
(`DprOutputExistsError`).

```bash
# the SIH demo report (offline; fixture geocoder + OSM sample + demo Census
# extract + the shipped real knowledge corpus):
./.venv/Scripts/python.exe scripts/generate_dpr.py --demo --out-dir build/dpr

# a session persisted by AdvisoryService (SQLite session store by default):
./.venv/Scripts/python.exe scripts/generate_dpr.py --session-id <id> \
    --pdf out/report.pdf --json out/report.json [--date 2026-09-09T09:30:00Z] [--overwrite]

# or run the demo script directly (also prints a summary):
./.venv/Scripts/python.exe scripts/phase8_demo.py
```

The demo report currently comes out **`DPR-9DF3C947297C75A3`**: recommendation
*Pivot* → dairy (a better-scoring local alternative than the proposed pulses
grocery — the CLAUDE.md §32 "catch a poor decision, point to a better one"
story), financial status *Feasible with stretch* on the stated plan, one
genuinely-sourced scheme parameter from the shipped corpus (`KB1`) plus honest
"no evidence" for interest rate / tenure / moratorium, and 19 disclosed evidence
gaps. `tests/test_phase8_demo.py` pins this.

## Limitations / deliberately deferred

- **Fixed-radius catchment only** (inherited from Phases 2–3); no travel-time
  isochrones.
- **The shipped knowledge corpus is small** (3 documents, 14 chunks, 2 approved
  parameter rows) — so most scheme parameters render "no evidence". This is
  surfaced, not hidden; it is not something the DPR phase fixes.
- **`declared_config` figures** (the SIH 10%/90% split, 8% p.a., 7-yr, 6-mo) are
  configuration, shown as such. The DPR does not claim they are a retrieved
  scheme rule.
- **One page break per section**: a short section still starts a fresh page. A
  deliberate readability choice; the report is ~18 pages for a full scenario.
- **No charts** — every figure is a table row with its provenance tag. A future
  enhancement could add a cash-flow sparkline from the existing
  `CashFlowResult.months`, still deterministically.
- This is a **structured bank-handoff / decision-support report with visible
  limitations**, not a "bank-approval-ready" document — the wording throughout
  says so.
