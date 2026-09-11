"""Phase 8 — Detailed Project Report generation (CLAUDE.md §4, §25 Phase 8).

The DPR only *composes* what earlier phases already produced: stored step
artifacts, the recommendation, the SWOT, conversation slots (with their
provenance), and retrieved knowledge evidence. It creates no new market
analysis, no new financial calculation, no scheme rule, and no LLM-authored
text. Every figure it prints is one of: user-provided, source-backed (with a
citation), a deterministic calculation (with named inputs), or a labelled
assumption — and where an input is absent it says so.

Public surface:

* `assemble_report(session, *, generated_at)` -> `DprDocument` (pure)
* `render_pdf_bytes(doc)` / `render_json_str(doc)` (deterministic)
* `DprService(sessions)` — build a `DprDocument`, or write PDF + JSON side by
  side (never overwriting silently)
"""

from __future__ import annotations

from vyaparsarathi.dpr.assemble import assemble_report
from vyaparsarathi.dpr.errors import DprError, DprOutputExistsError
from vyaparsarathi.dpr.fingerprint import input_fingerprint, report_id_for
from vyaparsarathi.dpr.render_json import render_json_str
from vyaparsarathi.dpr.render_pdf import render_pdf_bytes
from vyaparsarathi.dpr.report_models import SCHEMA_VERSION, DprDocument
from vyaparsarathi.dpr.service import DprArtifacts, DprService

__all__ = [
    "SCHEMA_VERSION",
    "DprArtifacts",
    "DprDocument",
    "DprError",
    "DprOutputExistsError",
    "DprService",
    "assemble_report",
    "input_fingerprint",
    "render_json_str",
    "render_pdf_bytes",
    "report_id_for",
]
