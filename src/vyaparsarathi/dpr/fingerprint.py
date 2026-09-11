"""Deterministic report identity (CLAUDE.md §23, §28; Phase 8 requirement:
"the same artifacts generate the same report content"). PURE — no clock, no
network.

`input_fingerprint` is a SHA-256 over exactly the things that decide the
report's *content*: every step artifact's own fingerprint (already computed
by `conversation/artifacts.py` over declared inputs, never over wall-clock
outputs), every slot's provenance-bearing fields, the resolved category, and
the session warnings. It deliberately excludes `generated_at` (injected,
cover-only) and any `updated_at` / `computed_on_turn` bookkeeping, so
regenerating a finished session's report on a different day yields the same
`report_id` and the same structured body.
"""

from __future__ import annotations

import hashlib
import json

from vyaparsarathi.conversation.session_models import ConversationSession, SlotName, StepId

_REPORT_ID_PREFIX = "DPR-"


def _slot_fingerprint_parts(session: ConversationSession) -> list[list[str]]:
    parts: list[list[str]] = []
    for name in sorted(SlotName, key=lambda n: n.value):
        slot = session.slot(name)
        for tag, sv in (("current", slot.current), *[("history", h) for h in slot.history]):
            parts.append(
                [
                    name.value,
                    tag,
                    sv.state.value,
                    "" if sv.value is None else str(sv.value),
                    sv.value_token or "",
                    (sv.normalization.value if sv.normalization is not None else ""),
                    sv.source or "",
                    sv.source_ref or "",
                    sv.rationale,
                    ",".join(sv.options),
                    "" if sv.confidence is None else f"{sv.confidence:.6f}",
                    ",".join(sv.calculated_from),
                ]
            )
    return parts


def input_fingerprint(session: ConversationSession) -> str:
    payload = {
        "artifacts": sorted(
            (step.value, art.fingerprint) for step, art in session.artifacts.items()
        ),
        "artifact_types": sorted(
            (step.value, art.payload_type) for step, art in session.artifacts.items()
        ),
        "slots": _slot_fingerprint_parts(session),
        "assets": sorted(a.value for a in session.assets.current.items),
        "assets_state": session.assets.current.state.value,
        "experience": sorted(c.value for c in session.experience_categories.current.items),
        "experience_state": session.experience_categories.current.state.value,
        "resolved_category": (
            session.resolved_category.value if session.resolved_category is not None else ""
        ),
        "resolved_category_resolved": session.resolved_category_resolved,
        "resolved_subtypes": list(session.resolved_subtypes),
        "declined_slots": sorted(s.value for s in session.declined_slots),
        "selected_geocode_candidate": session.selected_geocode_candidate,
        "session_warnings": list(session.session_warnings),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def report_id_for(fingerprint: str) -> str:
    return f"{_REPORT_ID_PREFIX}{fingerprint[:16].upper()}"


def present_artifacts(session: ConversationSession) -> tuple[str, ...]:
    return tuple(sorted(step.value for step in session.artifacts))


def missing_artifacts(session: ConversationSession) -> tuple[str, ...]:
    present = set(session.artifacts)
    return tuple(sorted(step.value for step in StepId if step not in present))


__all__ = [
    "input_fingerprint",
    "missing_artifacts",
    "present_artifacts",
    "report_id_for",
]
