"""One-off ETL, stage 2: propose candidate `SourcedParameter` rows from the
chunk corpus for human review, and verify a signed ``parameters.csv``
integrity before it is committed.

Not imported by the package; runs no part of the request path (CLAUDE.md
§23, §33). Two subcommands:

``propose`` — deterministic pattern rules scan every chunk for numerals near
a handful of keywords (rate/tenure/moratorium/fee/margin/subsidy) and write
**unreviewed** candidate rows to a CSV. This is mechanical pattern-matching,
not judgement: every row is written with ``reviewed_by`` and ``reviewed_on``
left blank, and a human must read the cited chunk, correct the row (name,
value, unit, normalization, applicability — the proposer's applicability is
always ``NATIONAL`` and unscoped; narrow it), delete false positives, and
fill in ``reviewed_by``/``reviewed_on`` before the row can become part of the
committed ``parameters.csv`` — an unsigned row fails `SourcedParameter`
construction outright (CLAUDE.md §3.5, §30: propose, never publish, without a
human signature).

``verify`` — loads the corpus exactly as the running application would
(`sources/knowledge/loader.py::FileCorpusStore`) and fails loudly (non-zero
exit, every rejection listed) if any row does not survive the three
"never fabricate a number" checks (CLAUDE.md §3.5) or the tier floor. Run
this before committing a change to ``data/knowledge/parameters.csv``.

    python scripts/build_parameter_registry.py propose \\
        --corpus-dir data/knowledge --out data/knowledge/parameters_proposed.csv

    python scripts/build_parameter_registry.py verify --corpus-dir data/knowledge
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vyaparsarathi.errors import KnowledgeCorpusError  # noqa: E402
from vyaparsarathi.models.knowledge import DocumentChunk  # noqa: E402
from vyaparsarathi.models.parameters import ParameterName, ValueNormalization  # noqa: E402
from vyaparsarathi.sources.knowledge.loader import (  # noqa: E402
    PARAM_CSV_FIELDNAMES,
    FileCorpusStore,
)


@dataclass(frozen=True)
class _Rule:
    name: ParameterName
    unit: str
    normalization: ValueNormalization
    # A numeral optionally followed by a unit word, near one of `keywords`
    # somewhere in the same chunk (a coarse proximity check — a human reviews
    # every hit, this is a candidate generator, not an extractor of record).
    value_pattern: re.Pattern[str]
    keywords: tuple[str, ...]


_PCT_RE = re.compile(r"\d+(?:\.\d+)?\s*%")
_MONTHS_RE = re.compile(r"\d+\s*months?")
_YEARS_RE = re.compile(r"\d+\s*years?")
_RUPEES_RE = re.compile(r"(?:Rs\.?|₹|INR)\s*[\d,]+(?:\.\d+)?")

_RULES: tuple[_Rule, ...] = (
    _Rule(
        ParameterName.INTEREST_RATE_PCT,
        "percent_per_annum",
        ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        _PCT_RE,
        ("interest", "per annum", "rate of interest"),
    ),
    _Rule(
        ParameterName.PROMOTER_MARGIN_PCT,
        "ratio",
        ValueNormalization.PERCENT_TO_RATIO,
        _PCT_RE,
        ("margin", "promoter contribution", "promoter's contribution"),
    ),
    _Rule(
        ParameterName.SUBSIDY_PCT,
        "ratio",
        ValueNormalization.PERCENT_TO_RATIO,
        _PCT_RE,
        ("subsidy",),
    ),
    _Rule(
        ParameterName.LOAN_TENURE_MONTHS,
        "months",
        ValueNormalization.AS_STATED,
        _MONTHS_RE,
        ("tenure", "repayment period"),
    ),
    _Rule(
        ParameterName.LOAN_TENURE_MONTHS,
        "months",
        ValueNormalization.YEARS_TO_MONTHS,
        _YEARS_RE,
        ("tenure", "repayment period"),
    ),
    _Rule(
        ParameterName.MORATORIUM_MONTHS,
        "months",
        ValueNormalization.AS_STATED,
        _MONTHS_RE,
        ("moratorium",),
    ),
    _Rule(
        ParameterName.SECURITY_DEPOSIT_MONTHS,
        "months",
        ValueNormalization.AS_STATED,
        _MONTHS_RE,
        ("security deposit",),
    ),
    _Rule(
        ParameterName.LICENCE_FEE_INR,
        "inr",
        ValueNormalization.AS_STATED,
        _RUPEES_RE,
        ("fee", "registration fee", "licence fee", "license fee"),
    ),
    _Rule(
        ParameterName.LOAN_CEILING_INR,
        "inr",
        ValueNormalization.AS_STATED,
        _RUPEES_RE,
        ("maximum loan", "loan ceiling", "up to"),
    ),
)


def propose_candidates(chunks: list[DocumentChunk]) -> list[dict[str, str]]:
    """Pure: chunks in, unreviewed candidate CSV rows out. Deterministic —
    iteration order follows the input list, which callers should sort for
    reproducibility (as `main` does)."""
    rows: list[dict[str, str]] = []
    for chunk in chunks:
        lowered = chunk.text.lower()
        seen_in_chunk = 0
        for rule in _RULES:
            if not any(k in lowered for k in rule.keywords):
                continue
            for match in rule.value_pattern.finditer(chunk.text):
                seen_in_chunk += 1
                token = match.group(0)
                rows.append(
                    {
                        "parameter_id": f"{chunk.document_id}:{rule.name.value}:{seen_in_chunk}",
                        "name": rule.name.value,
                        "value": "",  # the reviewer fills this in from value_token
                        "unit": rule.unit,
                        "value_token": token,
                        "normalization": rule.normalization.value,
                        "evidence_quote": chunk.text.strip(),
                        "document_id": chunk.document_id,
                        "chunk_id": chunk.chunk_id,
                        "locator_page_from": (
                            "" if chunk.locator.page_from is None else str(chunk.locator.page_from)
                        ),
                        "locator_page_to": (
                            "" if chunk.locator.page_to is None else str(chunk.locator.page_to)
                        ),
                        "locator_section": chunk.locator.section,
                        "locator_paragraph_index": (
                            ""
                            if chunk.locator.paragraph_index is None
                            else str(chunk.locator.paragraph_index)
                        ),
                        "tier": "",  # the reviewer fills this in from the DocumentRecord
                        "appl_jurisdiction_level": "",
                        "appl_jurisdiction_state": "",
                        "appl_jurisdiction_district": "",
                        "appl_scheme": "",
                        "appl_categories": "",
                        "appl_activity_kind": "",
                        "appl_min_loan_inr": "",
                        "appl_max_loan_inr": "",
                        "appl_effective_from": "",
                        "appl_effective_to": "",
                        "appl_conditions": "",
                        "reference_date": "",
                        "is_benchmark": "false",
                        "reviewed_by": "",  # UNSIGNED — a human must fill this in
                        "reviewed_on": "",
                        "notes": (
                            "PROPOSED, NOT REVIEWED — verify against evidence_quote before signing"
                        ),
                    }
                )
    return rows


def _cmd_propose(args: argparse.Namespace) -> int:
    store = FileCorpusStore(args.corpus_dir)
    report = store.report()
    if not report.corpus_present:
        print(
            f"no corpus found at {args.corpus_dir} — run build_knowledge_corpus.py first",
            file=sys.stderr,
        )
        return 1
    chunks = sorted(store.chunks(), key=lambda c: c.chunk_id)
    rows = propose_candidates(chunks)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(PARAM_CSV_FIELDNAMES))
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(
        f"proposed {len(rows)} unreviewed candidate row(s) from {len(chunks)} chunk(s) "
        f"to {args.out}",
        file=sys.stderr,
    )
    print(
        "Every row needs: value filled in, tier + applicability set, and "
        "reviewed_by/reviewed_on signed.",
        file=sys.stderr,
    )
    print(
        "A row missing any of those fails SourcedParameter construction — it cannot "
        "reach the corpus unsigned.",
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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build_parameter_registry", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    propose_p = sub.add_parser("propose", help="scan chunks for candidate parameter rows")
    propose_p.add_argument("--corpus-dir", required=True, type=Path)
    propose_p.add_argument("--out", required=True, type=Path)
    propose_p.set_defaults(func=_cmd_propose)

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
