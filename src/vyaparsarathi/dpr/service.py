"""`DprService` — the one public entry point for turning a finished advisory
session into a Detailed Project Report (CLAUDE.md §4 "DPR generator", §25
Phase 8).

Channel-neutral, like `app/service.py`: it takes a `SessionRepository` (the
same contract the CLI, the demo and the tests already use) and produces a
`DprDocument` plus, on request, a PDF and a JSON file written side by side. It
composes existing structured results only — no engine is re-run — and the
report date is always supplied by the caller, never read from a clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from vyaparsarathi.conversation.session_models import ConversationSession
from vyaparsarathi.database.session_repository import SessionRepository
from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.errors import DprError, DprOutputExistsError
from vyaparsarathi.dpr.render_json import render_json_str
from vyaparsarathi.dpr.render_pdf import render_pdf_bytes
from vyaparsarathi.dpr.report_models import DprDocument


@dataclass(frozen=True)
class DprArtifacts:
    document: DprDocument
    pdf_path: Path
    json_path: Path
    pdf_byte_count: int
    report_id: str


class DprService:
    def __init__(self, sessions: SessionRepository) -> None:
        self._sessions = sessions

    def build(self, session_id: str, *, generated_at: datetime) -> DprDocument:
        session = self._sessions.get(session_id)
        if session is None:
            raise DprError(f"no stored session with id {session_id!r}")
        return assemble_report(session, generated_at=generated_at)

    def build_from_session(
        self, session: ConversationSession, *, generated_at: datetime
    ) -> DprDocument:
        return assemble_report(session, generated_at=generated_at)

    def generate(
        self,
        session_id: str,
        *,
        generated_at: datetime,
        pdf_path: Path,
        json_path: Path,
        overwrite: bool = False,
    ) -> DprArtifacts:
        doc = self.build(session_id, generated_at=generated_at)
        return _write(doc, pdf_path=pdf_path, json_path=json_path, overwrite=overwrite)

    def generate_from_session(
        self,
        session: ConversationSession,
        *,
        generated_at: datetime,
        pdf_path: Path,
        json_path: Path,
        overwrite: bool = False,
    ) -> DprArtifacts:
        doc = self.build_from_session(session, generated_at=generated_at)
        return _write(doc, pdf_path=pdf_path, json_path=json_path, overwrite=overwrite)


def _write(doc: DprDocument, *, pdf_path: Path, json_path: Path, overwrite: bool) -> DprArtifacts:
    for path in (pdf_path, json_path):
        if path.exists() and not overwrite:
            raise DprOutputExistsError(
                f"{path} already exists; pass overwrite=True (or --overwrite) to replace it"
            )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_bytes = render_pdf_bytes(doc)
    pdf_path.write_bytes(pdf_bytes)
    json_path.write_text(render_json_str(doc), encoding="utf-8")
    return DprArtifacts(
        document=doc,
        pdf_path=pdf_path,
        json_path=json_path,
        pdf_byte_count=len(pdf_bytes),
        report_id=doc.report_id,
    )


__all__ = ["DprArtifacts", "DprService"]
