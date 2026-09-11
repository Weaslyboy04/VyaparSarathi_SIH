"""Generate a Detailed Project Report (PDF + structured JSON) from a stored
advisory session, or from the bundled demo session (CLAUDE.md §25 Phase 8).

    # the SIH demo report (no stored session, no network):
    ./.venv/Scripts/python.exe scripts/generate_dpr.py --demo --out-dir build/dpr

    # a session persisted by AdvisoryService (SQLite by default):
    ./.venv/Scripts/python.exe scripts/generate_dpr.py \
        --session-id 7f3c... --pdf out/report.pdf --json out/report.json

Output paths are explicit; an existing file is never overwritten unless
``--overwrite`` is given. The report timestamp defaults to the current UTC
time (a caller-boundary clock read — the deterministic assembler itself never
reads a clock) and can be pinned with ``--date`` for a reproducible artifact.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

# Allow `python scripts/generate_dpr.py` to import `scripts.phase8_demo` for the
# `--demo` path (running a script directly puts scripts/ on sys.path, not the
# repo root). Mirrors scripts/build_parameter_registry.py's sys.path handling.
_REPO_ROOT = str(Path(__file__).resolve().parents[1])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from vyaparsarathi.config import get_settings  # noqa: E402
from vyaparsarathi.dpr.errors import DprError, DprOutputExistsError  # noqa: E402
from vyaparsarathi.dpr.service import DprArtifacts, DprService  # noqa: E402


def _parse_date(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _resolve_paths(args: argparse.Namespace, report_id_hint: str) -> tuple[Path, Path]:
    if args.pdf and args.json:
        return args.pdf, args.json
    out_dir: Path = args.out_dir
    stem = args.name or report_id_hint
    return out_dir / f"{stem}.pdf", out_dir / f"{stem}.json"


def _run(args: argparse.Namespace) -> DprArtifacts:
    generated_at: datetime = args.date

    if args.demo:
        from scripts.phase8_demo import build_demo_session  # local import: fixtures only

        session = build_demo_session()
        service = DprService(_DummyRepo())
        pdf_path, json_path = _resolve_paths(args, "vyaparsarathi_dpr_demo")
        return service.generate_from_session(
            session,
            generated_at=generated_at,
            pdf_path=pdf_path,
            json_path=json_path,
            overwrite=args.overwrite,
        )

    if not args.session_id:
        raise DprError("provide --session-id (a stored session) or --demo")

    from vyaparsarathi.database.session_sql import create_session_repository

    db_url = args.db_url or get_settings().db_url
    repo = create_session_repository(db_url, create_schema=False)
    service = DprService(repo)
    doc = service.build(args.session_id, generated_at=generated_at)
    pdf_path, json_path = _resolve_paths(args, doc.report_id)
    return service.generate(
        args.session_id,
        generated_at=generated_at,
        pdf_path=pdf_path,
        json_path=json_path,
        overwrite=args.overwrite,
    )


class _DummyRepo:
    """`DprService` needs a `SessionRepository` to construct; the `--demo`
    path only ever calls `build_from_session`, so this is never touched."""

    def get(self, session_id: str):  # pragma: no cover - never called
        return None

    def save(self, session) -> None:  # pragma: no cover
        raise NotImplementedError

    def delete(self, session_id: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def list_ids(self) -> list[str]:  # pragma: no cover
        return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="generate_dpr", description=__doc__)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--demo", action="store_true", help="use the bundled fixture demo session")
    src.add_argument("--session-id", help="id of a session persisted by AdvisoryService")
    parser.add_argument("--db-url", help="session store URL (default: VYAPAR_DB_URL)")
    parser.add_argument("--pdf", type=Path, help="explicit PDF output path")
    parser.add_argument("--json", type=Path, help="explicit JSON output path")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("build") / "dpr",
        help="directory when --pdf/--json are not given (default: build/dpr/)",
    )
    parser.add_argument("--name", help="basename for the output files (default: the report id)")
    parser.add_argument(
        "--date",
        type=_parse_date,
        default=datetime.now(UTC),
        help="report timestamp, ISO-8601 (default: now, UTC)",
    )
    parser.add_argument("--overwrite", action="store_true", help="replace existing output files")
    args = parser.parse_args(argv)

    try:
        artifacts = _run(args)
    except DprOutputExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except DprError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    doc = artifacts.document
    print(f"report id        : {doc.report_id}")
    print(f"recommendation   : {doc.executive_summary.recommendation.display}")
    print(f"financial status : {doc.financial.feasibility_status.display}")
    print(f"evidence gaps    : {len(doc.evidence_gaps)}")
    print(f"PDF              : {artifacts.pdf_path}  ({artifacts.pdf_byte_count:,} bytes)")
    print(f"JSON             : {artifacts.json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
