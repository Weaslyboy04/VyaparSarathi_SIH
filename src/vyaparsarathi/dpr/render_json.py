"""Emit the structured DPR as JSON, beside the PDF, for audit and debugging
(CLAUDE.md §23, §25 Phase 8). PURE.
"""

from __future__ import annotations

from vyaparsarathi.dpr.report_models import DprDocument


def render_json_str(doc: DprDocument, *, indent: int = 2) -> str:
    """The full `DprDocument` as indented JSON — every `ProvenancedValue`,
    citation, gap and metadata field preserved verbatim."""
    return doc.model_dump_json(indent=indent)


__all__ = ["render_json_str"]
