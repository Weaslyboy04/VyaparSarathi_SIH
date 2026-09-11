# `data/knowledge/` — provenance

The Phase 5 knowledge corpus: official/credible source documents, chunked and
resolved into typed financial parameters with a full citation trail. Treat
every committed file here as **evidence**, not as VyaparSarathi's own claim
(CLAUDE.md §5, §23) — every number traces back to a specific document, page,
and printed substring.

**As shipped, this corpus is empty.** `documents.jsonl` and `manifest.json`
have zero rows/counts, `chunks.jsonl.gz` is an empty gzip stream, and
`parameters.csv` has only its header row. This is deliberate: Phase 5's
machinery — resolution, confidence, binding into `FinancialPlanInput` — is
built and tested (`tests/test_knowledge_*.py`) against a synthetic fixture
corpus under `tests/fixtures/knowledge/` (all invented, see its own README),
never against fabricated real-world content. Populating this directory with
real documents is a separate, ongoing operator task (staging real
`data/knowledge/raw/` documents) — not part of any code change; the
`extract` step itself runs unattended once real documents are staged.
`docs/phase-5.md`'s unsupported-parameter register tracks exactly which
`ParameterName`s this corpus currently has no evidence for.

Raw operator downloads live under `data/knowledge/raw/` (git-ignored,
per-document `document.json` + `text.txt` pairs — see
`scripts/build_knowledge_corpus.py`'s docstring for the exact layout and text
convention). Only the built, chunked, reviewed output is committed here.

## Layout

| file | contents |
|---|---|
| `documents.jsonl` | one `DocumentRecord` per line — publisher, tier, jurisdiction, dates, `retrieved_at` |
| `chunks.jsonl.gz` | one `DocumentChunk` per line — section-aware text slices, gzip-compressed |
| `parameters.csv` | one reviewed `SourcedParameter` per row — the flattened, human-editable registry |
| `manifest.json` | `{version, built_at, documents, chunks, parameters}` — corpus build metadata |

## Regeneration (once real documents exist under `data/knowledge/raw/`)

```bash
# 1. Prepare data/knowledge/raw/<document_id>/document.json + text.txt for
#    each downloaded document (see scripts/build_knowledge_corpus.py's
#    docstring for the exact JSON fields and text-marker convention:
#    # headings, [[page:N]], [[topics:...]], [[schemes:...]], [[categories:...]]).
#    document.json's own `tier` and `jurisdiction` are the one part of this
#    pipeline that stays an operator decision — who published this document
#    and what it applies to isn't re-derivable from the text alone.

# 2. Chunk them (deterministic; --built-at is a stated value, never a clock read):
python scripts/build_knowledge_corpus.py \
    --raw-dir data/knowledge/raw \
    --built-at 2026-01-15T00:00:00+00:00 \
    --out-dir data/knowledge

# 3. Genuinely blind dual-LLM extract: two DIFFERENT Gemini models each
#    independently read the exact same chunk/document/instructions and
#    each propose at most one candidate row — neither ever sees the
#    other's answer. Published only on exact, deterministic agreement
#    between the two independent candidates (never fuzzy-matched). A row's
#    `tier` is copied from its own document, never proposed. Requires
#    VYAPAR_GEMINI_EXTRACTOR_API_KEY / VYAPAR_GEMINI_VERIFIER_API_KEY set
#    (see .env.example). Writes data/knowledge/parameters.csv directly —
#    full overwrite on any nonzero-row run, no manual merge step (a
#    zero-row run never overwrites an --out that already holds published
#    rows — see write_parameters_csv):
python scripts/build_parameter_registry.py extract \
    --corpus-dir data/knowledge --out data/knowledge/parameters.csv \
    --verified-on 2026-01-15

# 4. Verify the committed corpus is internally consistent before committing:
python scripts/build_parameter_registry.py verify --corpus-dir data/knowledge

# then update this file's "Coverage" section below with what was actually
# added, and update docs/phase-5.md's unsupported-parameter register.
```

## Source-tier policy (CLAUDE.md §19)

Every document is tagged with a `SourceTier` (`knowledge/parameter_spec.py`
documents the exact floor per `ParameterName`):

- `govt_primary` — a ministry, gazette notification, or official scheme
  guideline. Required for a binding rate, margin, tenure, moratorium,
  ceiling, subsidy, or statutory fee.
- `regulator` — a central regulator's circular. Same standing as
  `govt_primary` for most parameters.
- `public_sector_institution` — a development bank or PSU bank's own
  published terms.
- `industry_body` — a recognised trade/industry association's published
  report. Admissible only for sector benchmarks (`gross_margin_pct`,
  `cogs_pct`, `inventory_days`), never for a scheme's own binding terms.
- `secondary` — commentary, press coverage. **Never sufficient alone** for
  any parameter this registry resolves (CLAUDE.md §30); a `SECONDARY`-tier
  row is rejected at the tier floor regardless of what it states.

## Coverage

_As shipped: none. This section is updated by whoever runs the regeneration
steps above, naming exactly which documents, schemes, states, and
`ParameterName`s the corpus covers — mirroring `data/demand/SOURCES.md`'s
"State coverage" table so a reader never has to guess what evidence actually
exists versus what the machinery merely supports._
