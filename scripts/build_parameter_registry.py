"""One-off ETL, stage 2: dual-LLM extract candidate `SourcedParameter` rows
from the chunk corpus and auto-publish or drop them, and verify a committed
``parameters.csv``'s integrity before it is committed.

Not imported by the package; runs no part of the request path (CLAUDE.md
§23, §33). Two subcommands:

``extract`` — GENUINELY BLIND dual extraction. Two Gemini models (a DIFFERENT
model each — never the same model called twice), labelled "extractor" and
"verifier" only to identify which key/model produced which answer
(``SourcedParameter.extractor_model``/``verifier_model``), each independently
receive the exact SAME request — same chunk text, same document jurisdiction,
same allowed-enum lists, same instructions
(``scripts/knowledge_extraction_prompts.py``) — and each independently
proposes AT MOST ONE candidate parameter, or none. Neither model is ever
shown the other's answer before producing its own; the two independent
responses are compared only after BOTH are already in hand (``_canonical_agree``).
This is not a "propose, then check" relationship any more — it is two
independent reads of the same evidence.

A row is published ONLY if:

1. both models found a candidate (not zero, not exactly one), AND
2. the two candidates canonically agree — see ``_canonical_agree`` for the
   exact deterministic comparison (normalized numeric value, unit,
   normalization, jurisdiction, scheme, categories, activity kind, loan-band
   bounds AND their inclusive/exclusive semantics, effective dates,
   conditions — every field that governs eligibility/resolution).
   Whitespace is normalized on free-text qualifiers before comparison;
   nothing is ever fuzzy-matched, averaged, or arbitrarily picked, AND
3. the three mechanical "never fabricate a number" checks still pass
   (``value_token`` in ``evidence_quote``; ``normalize_value`` agrees with
   ``value``; ``evidence_quote`` verbatim in the real chunk), AND
4. the row's ``tier`` (copied from its own document, never proposed by
   either model) and ``applicability.jurisdiction`` (never broader than, or
   disjoint from, its own document's jurisdiction) are consistent with the
   document it came from.

ANY disagreement or failed check drops the row silently — never queued for
later review, never averaged, never arbitrarily picked (CLAUDE.md §3.5, §30;
mirrors how ``knowledge/resolver.py`` already treats CONFLICTING evidence).
This is a full replacement for a human reviewing and signing every row: there
is no ``reviewed_by``/``reviewed_on`` in this pipeline any more —
``SourcedParameter.extractor_model``/``verifier_model``/``verified_on``
record which two models voted and when, not who a human was.

    python scripts/build_parameter_registry.py extract \\
        --corpus-dir data/knowledge --out data/knowledge/parameters.csv \\
        --verified-on 2026-09-08

Each successful (nonzero-row) run **fully overwrites** ``--out`` — simplest,
fully reproducible from the corpus + the two Gemini models, no merge/dedup
logic — writing through a temporary file for an atomic replace. A run that
publishes ZERO rows (total API/quota/parse failure, not a real evidence
change) does NOT overwrite an ``--out`` that already holds published rows —
see ``write_parameters_csv``. Every chunk in the corpus is sent to both
models (no keyword pre-filter); this trades free-tier API usage for never
silently skipping a parameter phrased outside a fixed keyword list.

``verify`` — loads the corpus exactly as the running application would
(`sources/knowledge/loader.py::FileCorpusStore`) and fails loudly (non-zero
exit, every rejection listed) if any row does not survive the three
mechanical "never fabricate a number" checks, the tier floor, the
tier-matches-document check, or the jurisdiction-scope check. Run this before
committing a change to ``data/knowledge/parameters.csv`` — it is the final
integrity gate regardless of how the CSV was produced.

    python scripts/build_parameter_registry.py verify --corpus-dir data/knowledge
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vyaparsarathi.config import get_settings  # noqa: E402
from vyaparsarathi.errors import (  # noqa: E402
    FinancialInputError,
    KnowledgeCorpusError,
    LlmPayloadError,
    LlmUnavailableError,
)
from vyaparsarathi.llm.gemini_provider import GeminiLlmProvider  # noqa: E402
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole  # noqa: E402
from vyaparsarathi.llm.provider import LlmProvider  # noqa: E402
from vyaparsarathi.llm.structured import extract_balanced_json_object  # noqa: E402
from vyaparsarathi.models.finance import Unit  # noqa: E402
from vyaparsarathi.models.knowledge import (  # noqa: E402
    DocumentChunk,
    DocumentRecord,
    JurisdictionLevel,
)
from vyaparsarathi.models.parameters import (  # noqa: E402
    Applicability,
    ParameterName,
    SourcedParameter,
    ValueNormalization,
    normalize_value,
)
from vyaparsarathi.models.taxonomy import BusinessCategory  # noqa: E402
from vyaparsarathi.sources.knowledge.loader import (  # noqa: E402
    PARAM_CSV_FIELDNAMES,
    FileCorpusStore,
    jurisdiction_exceeds_document_scope,
    normalize_whitespace,
    quote_occurs_in_chunk,
)
from vyaparsarathi.utils.logging import get_logger  # noqa: E402

# Kept as a sibling module (not under src/) so its plain-string-constant style
# mirrors llm/prompts.py without this ETL-only prompt text ever being part of
# the installed package.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from knowledge_extraction_prompts import (  # noqa: E402
    EXTRACTION_INSTRUCTIONS,
    EXTRACTION_SYSTEM_PROMPT,
)

logger = get_logger(__name__)

_MAX_OUTPUT_TOKENS = 4096
# Deterministic-as-possible, not creative — this is extraction of a printed
# fact, never composition.
_TEMPERATURE = 0.0

# `gemini-3.6-flash` (the extractor role) is a "thinking" model: internal
# reasoning tokens count against `max_output_tokens` unless the response
# already fits comfortably. A live probe against this exact pipeline showed
# `thoughtsTokenCount` around 980 of a 1024-token budget, truncating the
# visible JSON answer before it ever closed its final brace
# (`finishReason: MAX_TOKENS`) — 6 of 8 real corpus chunks failed to parse
# for exactly this reason before this constant was raised. 4096 leaves
# comfortable headroom for both the thinking budget and a full JSON answer.


class _ExtractedCandidate(BaseModel):
    """One candidate row, before it is checked against its document and
    turned into a real `SourcedParameter`. Deliberately has NO `value`
    field — the script always derives it from `value_token` via
    `normalize_value`, the same grounding discipline
    `llm/structured.py::extract_understanding` already uses for the
    conversational layer (CLAUDE.md §3.1). Returned independently by BOTH
    the extractor and verifier roles — this is the one candidate shape in
    the whole gate; there is no longer a separate "verifier checklist"
    schema, because the verifier no longer checks anything — it extracts,
    exactly like the extractor, from the same blind prompt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ParameterName
    value_token: str = Field(min_length=1)
    unit: Unit
    normalization: ValueNormalization
    evidence_quote: str = Field(min_length=1)
    applicability: Applicability
    reference_date: date | None = None
    is_benchmark: bool = False
    notes: str = ""


@dataclass
class ExtractionStats:
    chunks_scanned: int = 0
    dropped_no_document: int = 0
    # Both independent models agreed there is nothing here — a normal,
    # expected outcome for filler/procedural text, not a failure.
    dropped_both_no_candidate: int = 0
    dropped_extractor_unparseable: int = 0
    dropped_verifier_unparseable: int = 0
    # Covers every disagreement shape: one model found something and the
    # other didn't, or both found something but the candidates don't
    # canonically agree.
    dropped_disagreement: int = 0
    dropped_mechanical_or_scope: int = 0
    published: int = 0


# --- prompt assembly (identical request for both roles) -----------------


def _allowed_lines() -> str:
    """Derived from the real enums, never hardcoded — a taxonomy/parameter
    addition needs no prompt edit (mirrors
    `llm/structured.py::_ALLOWED_SLOTS_LINE`)."""
    names = ", ".join(sorted(n.value for n in ParameterName))
    units = ", ".join(sorted(u.value for u in Unit))
    normalizations = ", ".join(sorted(n.value for n in ValueNormalization))
    categories = ", ".join(sorted(c.value for c in BusinessCategory))
    return (
        f"Allowed parameter names: {names}\n"
        f"Allowed units: {units}\n"
        f"Allowed normalizations: {normalizations}\n"
        f"Allowed business categories: {categories}"
    )


def _document_jurisdiction_line(document: DocumentRecord) -> str:
    j = document.jurisdiction
    if j.level is JurisdictionLevel.NATIONAL:
        return "This document's own jurisdiction: national (applies across India)."
    if j.level is JurisdictionLevel.STATE:
        return f"This document's own jurisdiction: state — {j.state}."
    return f"This document's own jurisdiction: district — {j.district}, {j.state}."


def _extraction_request(chunk: DocumentChunk, document: DocumentRecord) -> LlmRequest:
    """The ONE request both independent roles receive, byte-identical —
    genuine blindness means there is no per-role variant of this function.
    Never mentions a "candidate" anywhere, because neither role is ever
    shown one."""
    user_content = (
        f"{EXTRACTION_INSTRUCTIONS}\n{_allowed_lines()}\n"
        f"{_document_jurisdiction_line(document)}\n\nChunk text:\n{chunk.text}"
    )
    return LlmRequest(
        messages=(
            LlmMessage(role=LlmRole.SYSTEM, content=EXTRACTION_SYSTEM_PROMPT),
            LlmMessage(role=LlmRole.USER, content=user_content),
        ),
        max_output_tokens=_MAX_OUTPUT_TOKENS,
        temperature=_TEMPERATURE,
        prompt_id=f"extract:{chunk.chunk_id}",
    )


# --- response parsing ---------------------------------------------------


def _parse_extracted_candidate(response_text: str) -> _ExtractedCandidate | None:
    blob = extract_balanced_json_object(response_text)
    if blob is None:
        raise LlmPayloadError("no JSON object found in extractor response")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        raise LlmPayloadError(f"malformed JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmPayloadError("parsed extractor JSON was not an object")
    if not data.get("found"):
        return None
    payload = {k: v for k, v in data.items() if k != "found"}
    try:
        return _ExtractedCandidate.model_validate(payload)
    except ValidationError as exc:
        raise LlmPayloadError(f"candidate payload did not match schema: {exc}") from exc


# --- canonical agreement — deterministic, never fuzzy --------------------


def _norm_optional_text(value: str | None) -> str | None:
    return None if value is None else normalize_whitespace(value)


def _applicability_canonically_agrees(a: Applicability, b: Applicability) -> bool:
    """Every field that governs resolution/eligibility must agree exactly.
    Free-text qualifiers (`scheme`, `activity_kind`, `conditions`) are
    whitespace-normalized before comparison — never fuzzy-matched; two
    independent reads that copy the same qualifier with different
    incidental spacing still agree, but two reads naming genuinely
    different qualifiers do not."""
    if a.jurisdiction != b.jurisdiction:
        return False
    if _norm_optional_text(a.scheme) != _norm_optional_text(b.scheme):
        return False
    if frozenset(a.categories) != frozenset(b.categories):
        return False
    if normalize_whitespace(a.activity_kind) != normalize_whitespace(b.activity_kind):
        return False
    if a.effective_from != b.effective_from or a.effective_to != b.effective_to:
        return False
    if frozenset(normalize_whitespace(c) for c in a.conditions) != frozenset(
        normalize_whitespace(c) for c in b.conditions
    ):
        return False
    if a.min_loan_inr != b.min_loan_inr or a.min_loan_inr_exclusive != b.min_loan_inr_exclusive:
        return False
    if a.max_loan_inr != b.max_loan_inr or a.max_loan_inr_exclusive != b.max_loan_inr_exclusive:
        return False
    return True


def _canonical_agree(a: _ExtractedCandidate, b: _ExtractedCandidate) -> bool:
    """The full agreement gate. Deliberately does NOT compare the raw
    `value_token`/`evidence_quote` strings — two independent reads may
    verbatim-quote different (both equally valid, both independently
    grounding-checked) spans of the same source sentence while agreeing on
    what the number and its scope actually are. What must agree is the
    NORMALIZED numeric value (via the same deterministic `normalize_value`
    used everywhere else), the unit, the normalization method itself (so a
    coincidentally-equal number derived two different ways is not treated
    as agreement), the parameter name, and every `Applicability` qualifier
    that governs resolution. No fuzzy matching anywhere in this function."""
    try:
        value_a = normalize_value(a.value_token, a.normalization)
        value_b = normalize_value(b.value_token, b.normalization)
    except FinancialInputError:
        return False
    if value_a != value_b:
        return False
    if a.name != b.name or a.unit != b.unit or a.normalization != b.normalization:
        return False
    return _applicability_canonically_agrees(a.applicability, b.applicability)


# --- the gate -------------------------------------------------------


def build_parameter_row(
    chunk: DocumentChunk,
    document: DocumentRecord,
    candidate: _ExtractedCandidate,
    seq: int,
    *,
    extractor_model: str,
    verifier_model: str,
    verified_on: date,
) -> SourcedParameter | None:
    """`tier` is copied from `document.tier`, never proposed by either model.
    `candidate` is the extractor's own candidate — safe to use its literal
    `value_token`/`evidence_quote`/`reference_date`/`is_benchmark`/`notes`
    once `_canonical_agree` has already confirmed the verifier's
    independent candidate agrees on everything that determines correctness;
    this is not "picking a side" in a disagreement, since by this point
    there is no disagreement left to pick a side of. Returns `None` (never
    raises) on a jurisdiction-scope violation or any
    `ValidationError`/`FinancialInputError` from construction (the 3
    mechanical checks) — the caller counts and drops silently."""
    if jurisdiction_exceeds_document_scope(
        candidate.applicability.jurisdiction, document.jurisdiction
    ):
        return None
    if not quote_occurs_in_chunk(candidate.evidence_quote, chunk.text):
        return None
    try:
        value = normalize_value(candidate.value_token, candidate.normalization)
    except FinancialInputError:
        return None
    try:
        return SourcedParameter(
            parameter_id=f"{chunk.document_id}:{candidate.name.value}:{seq}",
            name=candidate.name,
            value=value,
            unit=candidate.unit,
            value_token=candidate.value_token,
            normalization=candidate.normalization,
            evidence_quote=candidate.evidence_quote,
            document_id=chunk.document_id,
            chunk_id=chunk.chunk_id,
            locator=chunk.locator,
            tier=document.tier,
            applicability=candidate.applicability,
            reference_date=candidate.reference_date,
            is_benchmark=candidate.is_benchmark,
            extractor_model=extractor_model,
            verifier_model=verifier_model,
            verified_on=verified_on,
            notes=candidate.notes,
        )
    except ValidationError:
        return None


def extract_and_verify(
    chunks: list[DocumentChunk],
    documents: dict[str, DocumentRecord],
    extractor: LlmProvider,
    verifier: LlmProvider,
    *,
    extractor_model: str,
    verifier_model: str,
    verified_on: date,
    pacing_seconds: float = 0.0,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[SourcedParameter], ExtractionStats]:
    """Pure orchestration over injected `LlmProvider`s — no I/O of its own,
    so a unit test passes two fakes and never touches the network.
    Deterministic iteration order follows the given `chunks` list; callers
    sort for reproducibility (as `_cmd_extract` does).

    Genuinely blind: both providers receive the exact same
    `_extraction_request(chunk, document)` — `extractor` is never told what
    `verifier` will be asked, and vice versa; neither request ever mentions
    the other's answer, because at request-construction time neither answer
    exists yet. Comparison (`_canonical_agree`) happens only after BOTH
    responses have been received.

    `pacing_seconds` (default 0, so tests never sleep) adds a courtesy pause
    BETWEEN chunks — a live run against a free-tier Gemini key can burn
    through its requests-per-minute budget faster than any single request's
    own retry/backoff can absorb (observed directly: 429s on every retry
    attempt when chunks were sent back-to-back), so this paces the whole
    scan rather than relying on `GeminiLlmProvider`'s per-request retries
    alone."""
    stats = ExtractionStats()
    published: list[SourcedParameter] = []
    seq_by_key: dict[tuple[str, str], int] = {}

    for i, chunk in enumerate(chunks):
        if i > 0 and pacing_seconds > 0:
            sleep(pacing_seconds)
        stats.chunks_scanned += 1
        document = documents.get(chunk.document_id)
        if document is None:
            stats.dropped_no_document += 1
            continue

        request = _extraction_request(chunk, document)

        try:
            resp_a = extractor.complete(request)
            candidate_a = _parse_extracted_candidate(resp_a.text)
        except (LlmUnavailableError, LlmPayloadError) as exc:
            logger.warning("extractor failed on chunk %s: %s", chunk.chunk_id, exc)
            stats.dropped_extractor_unparseable += 1
            continue

        try:
            resp_b = verifier.complete(request)
            candidate_b = _parse_extracted_candidate(resp_b.text)
        except (LlmUnavailableError, LlmPayloadError) as exc:
            logger.warning("verifier failed on chunk %s: %s", chunk.chunk_id, exc)
            stats.dropped_verifier_unparseable += 1
            continue

        # Compare only now that BOTH independent responses are in hand.
        if candidate_a is None and candidate_b is None:
            stats.dropped_both_no_candidate += 1
            continue
        if candidate_a is None or candidate_b is None:
            stats.dropped_disagreement += 1
            continue
        if not _canonical_agree(candidate_a, candidate_b):
            stats.dropped_disagreement += 1
            continue

        key = (chunk.document_id, candidate_a.name.value)
        seq = seq_by_key.get(key, 0) + 1
        seq_by_key[key] = seq

        row = build_parameter_row(
            chunk,
            document,
            candidate_a,
            seq,
            extractor_model=extractor_model,
            verifier_model=verifier_model,
            verified_on=verified_on,
        )
        if row is None:
            stats.dropped_mechanical_or_scope += 1
            continue
        published.append(row)
        stats.published += 1

    return published, stats


# --- CSV serialization (the inverse of loader.py's _row_to_parameter) ---


def _optional(value: object) -> str:
    return "" if value is None else str(value)


def _parameter_to_row(param: SourcedParameter) -> dict[str, str]:
    j = param.applicability.jurisdiction
    a = param.applicability
    return {
        "parameter_id": param.parameter_id,
        "name": param.name.value,
        "value": str(param.value),
        "unit": param.unit.value,
        "value_token": param.value_token,
        "normalization": param.normalization.value,
        "evidence_quote": param.evidence_quote,
        "document_id": param.document_id,
        "chunk_id": param.chunk_id,
        "locator_page_from": _optional(param.locator.page_from),
        "locator_page_to": _optional(param.locator.page_to),
        "locator_section": param.locator.section,
        "locator_paragraph_index": _optional(param.locator.paragraph_index),
        "tier": param.tier.value,
        "appl_jurisdiction_level": j.level.value,
        "appl_jurisdiction_state": _optional(j.state),
        "appl_jurisdiction_district": _optional(j.district),
        "appl_scheme": _optional(a.scheme),
        "appl_categories": "|".join(c.value for c in a.categories),
        "appl_activity_kind": a.activity_kind,
        "appl_min_loan_inr": _optional(a.min_loan_inr),
        "appl_min_loan_exclusive": "true" if a.min_loan_inr_exclusive else "false",
        "appl_max_loan_inr": _optional(a.max_loan_inr),
        "appl_max_loan_exclusive": "true" if a.max_loan_inr_exclusive else "false",
        "appl_effective_from": _optional(a.effective_from),
        "appl_effective_to": _optional(a.effective_to),
        "appl_conditions": "|".join(a.conditions),
        "reference_date": _optional(param.reference_date),
        "is_benchmark": "true" if param.is_benchmark else "false",
        "extractor_model": param.extractor_model,
        "verifier_model": param.verifier_model,
        "verified_on": param.verified_on.isoformat(),
        "notes": param.notes,
    }


def _has_published_rows(path: Path) -> bool:
    """True when `path` already exists and holds at least one data row
    (header + >=1 line) — the test `write_parameters_csv` uses to decide
    whether a zero-row run may overwrite it."""
    if not path.exists():
        return False
    with path.open(encoding="utf-8", newline="") as f:
        next(f, None)  # header line
        return next(f, None) is not None


def write_parameters_csv(rows: list[SourcedParameter], out: Path) -> bool:
    """Write `rows` to `out` via a temporary file in the same directory,
    replaced atomically into place — EXCEPT when `rows` is empty AND `out`
    already holds previously-published rows, in which case `out` is left
    untouched: a run that published nothing (an API/quota/parse failure, not
    a real change in the evidence) must never blank out a working corpus.
    Returns `True` if `out` was (re)written, `False` if it was left as-is."""
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out.with_name(out.name + ".tmp")
    with tmp_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(PARAM_CSV_FIELDNAMES))
        writer.writeheader()
        for row in rows:
            writer.writerow(_parameter_to_row(row))

    if not rows and _has_published_rows(out):
        tmp_path.unlink()
        return False
    tmp_path.replace(out)
    return True


# --- CLI -----------------------------------------------------------


def _cmd_extract(args: argparse.Namespace) -> int:
    store = FileCorpusStore(args.corpus_dir)
    report = store.report()
    if not report.corpus_present:
        print(
            f"no corpus found at {args.corpus_dir} — run build_knowledge_corpus.py first",
            file=sys.stderr,
        )
        return 1

    settings = get_settings()
    documents = {d.document_id: d for d in store.documents()}
    chunks = sorted(store.chunks(), key=lambda c: c.chunk_id)

    extractor = GeminiLlmProvider(settings, role="extractor")
    verifier = GeminiLlmProvider(settings, role="verifier")
    try:
        rows, stats = extract_and_verify(
            chunks,
            documents,
            extractor,
            verifier,
            extractor_model=settings.gemini_extractor_model,
            verifier_model=settings.gemini_verifier_model,
            verified_on=args.verified_on,
            pacing_seconds=8.0,
        )
    finally:
        extractor.close()
        verifier.close()

    wrote = write_parameters_csv(rows, args.out)

    dropped = stats.chunks_scanned - stats.published
    print(
        f"scanned {stats.chunks_scanned} chunk(s): published {stats.published}, "
        f"dropped {dropped} (no_document={stats.dropped_no_document}, "
        f"both_no_candidate={stats.dropped_both_no_candidate}, "
        f"extractor_unparseable={stats.dropped_extractor_unparseable}, "
        f"verifier_unparseable={stats.dropped_verifier_unparseable}, "
        f"disagreement={stats.dropped_disagreement}, "
        f"mechanical_or_scope={stats.dropped_mechanical_or_scope})",
        file=sys.stderr,
    )
    if wrote:
        print(f"wrote {len(rows)} row(s) to {args.out} (full overwrite)", file=sys.stderr)
    else:
        print(
            f"published 0 rows this run; kept the existing {args.out} unchanged "
            "(a zero-row run must never blank out previously-published evidence)",
            file=sys.stderr,
        )
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    store = FileCorpusStore(args.corpus_dir)
    report = store.report()
    if not report.corpus_present:
        print(f"no corpus found at {args.corpus_dir}", file=sys.stderr)
        return 1

    problems: list[str] = []
    if report.parse_errors:
        problems.append(f"{report.parse_errors} row(s)/line(s) failed to parse")
    if report.parameters_rejected_unverified_quote:
        problems.append(
            f"{report.parameters_rejected_unverified_quote} row(s) whose evidence_quote does "
            "not occur verbatim in the cited chunk"
        )
    if report.parameters_rejected_unknown_chunk:
        problems.append(
            f"{report.parameters_rejected_unknown_chunk} row(s) citing an unknown chunk_id"
        )
    if report.parameters_rejected_tier_floor:
        problems.append(
            f"{report.parameters_rejected_tier_floor} row(s) below the tier floor "
            "for their parameter"
        )
    if report.parameters_rejected_tier_mismatch:
        problems.append(
            f"{report.parameters_rejected_tier_mismatch} row(s) whose tier does not match "
            "their own document's tier"
        )
    if report.parameters_rejected_jurisdiction_scope:
        problems.append(
            f"{report.parameters_rejected_jurisdiction_scope} row(s) whose jurisdiction "
            "exceeds their own document's scope"
        )

    print(
        f"{report.documents_loaded} document(s), {report.chunks_loaded} chunk(s), "
        f"{report.parameters_loaded} verified parameter(s)",
        file=sys.stderr,
    )
    if problems:
        for p in problems:
            print(f"FAIL: {p}", file=sys.stderr)
        raise KnowledgeCorpusError(
            f"{args.corpus_dir} did not verify cleanly: " + "; ".join(problems)
        )
    print(
        "verify: clean — every committed row is fully traceable to its cited evidence",
        file=sys.stderr,
    )
    return 0


def _date_arg(value: str) -> date:
    return date.fromisoformat(value)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build_parameter_registry", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    extract_p = sub.add_parser(
        "extract", help="dual-LLM extract + verify candidate parameter rows, publish or drop"
    )
    extract_p.add_argument("--corpus-dir", required=True, type=Path)
    extract_p.add_argument("--out", required=True, type=Path)
    extract_p.add_argument(
        "--verified-on",
        required=True,
        type=_date_arg,
        help="operator-stated date this run happened (YYYY-MM-DD) — never the wall clock",
    )
    extract_p.set_defaults(func=_cmd_extract)

    verify_p = sub.add_parser("verify", help="check a committed parameters.csv's integrity")
    verify_p.add_argument("--corpus-dir", required=True, type=Path)
    verify_p.set_defaults(func=_cmd_verify)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        return int(args.func(args))
    except KnowledgeCorpusError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
