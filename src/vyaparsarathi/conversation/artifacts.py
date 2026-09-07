"""Fingerprinting + cascade invalidation over the step DAG (CLAUDE.md §25
Phase 6). PURE — SHA-256 over recursive **inputs**, never outputs.

`FinanceKnowledgeEvidence.acquired_at`, `DemandEvidence.acquired_at` and
`NormalizedBusiness.retrieved_at` all carry wall-clock values (set once at
each acquisition boundary — CLAUDE.md §28). Hashing a step's *output* would
change every fingerprint on every turn and defeat caching entirely; hashing
only its *declared inputs* (this module) excludes them by construction. A
step's own artifact is retained across a turn exactly when its freshly
recomputed fingerprint still matches the fingerprint recorded when it last
ran — this single recursive comparison, walked once in `DAG_ORDER`, is both
"nothing changed, don't re-run" AND the Phase 3 <-> Phase 4 "loop": when
`FINANCIAL_FIT` goes absent -> present, `OPPORTUNITY`'s `optional_steps`
contribution changes, its fingerprint changes, and it re-runs — no special
case anywhere in this module.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal

from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    Slot,
    SlotName,
    StepArtifact,
    StepId,
)
from vyaparsarathi.conversation.workflow import DAG_ORDER, STEP_SPECS

# Exactly which SlotName values feed each step's fingerprint — the "curated
# engine-argument -> declared-input table" the approved plan's Risks section
# calls for. A step not listed here reads no slot directly (everything it
# needs arrives through an upstream artifact's fingerprint instead).
STEP_INPUT_SLOTS: dict[StepId, tuple[SlotName, ...]] = {
    StepId.RESOLVE_PROPOSED: (SlotName.PROPOSED_BUSINESS_TEXT,),
    StepId.DISCOVER: (SlotName.LOCATION_TEXT, SlotName.RADIUS_M),
    # DEMAND_EVIDENCE reads the same location slots directly (see
    # _FINGERPRINT_UPSTREAM_OVERRIDE below) rather than inheriting DISCOVER's
    # full fingerprint, which is category-sensitive.
    StepId.DEMAND_EVIDENCE: (SlotName.LOCATION_TEXT, SlotName.RADIUS_M),
    StepId.OPPORTUNITY: (SlotName.LIQUID_CASH_INR,),
    StepId.FINANCE_KNOWLEDGE: (SlotName.LOAN_PRINCIPAL_INR,),
    # The margin check compares the required-margin figure against BOTH of
    # these directly (finance/structuring.py), so a correction to either one
    # must re-run structuring even though its other inputs all arrive via
    # BIND_PLAN's own fingerprint.
    StepId.STRUCTURE_FINANCE: (
        SlotName.LIQUID_CASH_INR,
        SlotName.PROMOTER_CASH_CONTRIBUTION_INR,
    ),
    StepId.BUILD_PLAN: (
        SlotName.LIQUID_CASH_INR,
        SlotName.PROMOTER_CASH_CONTRIBUTION_INR,
        SlotName.YEARS_EXPERIENCE,
        SlotName.MONTHLY_REVENUE_INR,
        SlotName.COGS_PCT,
        SlotName.PROJECT_COST_INR,
        SlotName.FIXED_OPEX_INR,
        SlotName.LOAN_PRINCIPAL_INR,
        SlotName.LOAN_INTEREST_RATE_PCT,
        SlotName.LOAN_TENURE_MONTHS,
        SlotName.LOAN_MORATORIUM_MONTHS,
    ),
}

_STEPS_USING_ASSETS = frozenset({StepId.OPPORTUNITY})
_STEPS_USING_EXPERIENCE = frozenset({StepId.OPPORTUNITY})

# A per-step override of which upstream STEPS feed the fingerprint (distinct
# from workflow.py's `required_steps`, which govern READINESS only).
# `acquire_demand_evidence` (discovery/demand_acquisition.py) reads only
# `discovery.resolved_place` / `.query_text` / `.requested_radius_m` — never
# `discovery.category` or `.businesses` — so DEMAND_EVIDENCE's fingerprint
# must not inherit DISCOVER's category-sensitive fingerprint, or a category
# change (worked example B) would wrongly bust the expensive Overpass union
# query it exists to avoid repeating. A step absent from this table uses its
# full `required_steps` for fingerprinting (the common case).
_FINGERPRINT_UPSTREAM_OVERRIDE: dict[StepId, tuple[StepId, ...]] = {
    StepId.DEMAND_EVIDENCE: (),
}


def _slot_repr(slot: Slot) -> dict[str, str | None]:
    value = slot.value
    return {"state": slot.state.value, "value": None if value is None else str(value)}


def _fingerprint_one(
    step: StepId,
    session: ConversationSession,
    fresh: Mapping[StepId, str],
    config_blob: Mapping[str, object],
) -> str:
    spec = STEP_SPECS[step]
    required_for_fp = _FINGERPRINT_UPSTREAM_OVERRIDE.get(step, spec.required_steps)
    payload: dict[str, object] = {
        "step": step.value,
        "slots": {
            name.value: _slot_repr(session.slot(name)) for name in STEP_INPUT_SLOTS.get(step, ())
        },
        "required_upstream": {dep.value: fresh[dep] for dep in required_for_fp},
        # An optional dependency only contributes once IT has an artifact —
        # its appearance (not just its content) is part of the fingerprint,
        # which is exactly what makes OPPORTUNITY re-run once FINANCIAL_FIT
        # first appears.
        "optional_upstream": {
            dep.value: fresh[dep] for dep in spec.optional_steps if dep in session.artifacts
        },
        "config": config_blob,
    }
    if step in _STEPS_USING_ASSETS:
        payload["assets"] = sorted(a.value for a in session.assets.current.items)
    if step in _STEPS_USING_EXPERIENCE:
        payload["experience"] = sorted(c.value for c in session.experience_categories.current.items)
    if step in (StepId.DISCOVER, StepId.DEMAND_EVIDENCE):
        payload["geocode_candidate"] = session.selected_geocode_candidate
    if step is StepId.BUILD_PLAN or step is StepId.FINANCE_KNOWLEDGE:
        payload["resolved_category"] = (
            session.resolved_category.value if session.resolved_category is not None else None
        )
    blob = json.dumps(payload, sort_keys=True, default=_json_default)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _json_default(obj: object) -> str:
    if isinstance(obj, Decimal):
        return str(obj)
    return str(obj)  # pragma: no cover — defensive; every payload value above is already JSON-safe


def compute_fingerprints(
    session: ConversationSession, config_blobs: Mapping[StepId, Mapping[str, object]] | None = None
) -> dict[StepId, str]:
    """The fresh fingerprint every `StepId` WOULD have if it ran right now,
    computed once in `DAG_ORDER` (a topological order of required edges —
    `workflow.py::is_acyclic`) so each step's own computation can read its
    upstream steps' already-fresh fingerprints."""
    blobs = config_blobs or {}
    fresh: dict[StepId, str] = {}
    for step in DAG_ORDER:
        fresh[step] = _fingerprint_one(step, session, fresh, blobs.get(step, {}))
    return fresh


def invalidate(
    session: ConversationSession, config_blobs: Mapping[StepId, Mapping[str, object]] | None = None
) -> tuple[ConversationSession, tuple[StepId, ...]]:
    """Drop every existing artifact whose freshly recomputed fingerprint no
    longer matches what was stored when it last ran. Returns the updated
    session and the `StepId`s actually dropped, for the turn audit trail."""
    fresh = compute_fingerprints(session, config_blobs)
    artifacts = dict(session.artifacts)
    dropped: list[StepId] = []
    for step in DAG_ORDER:
        existing = artifacts.get(step)
        if existing is not None and existing.fingerprint != fresh[step]:
            del artifacts[step]
            dropped.append(step)
    if not dropped:
        return session, ()
    return session.model_copy(update={"artifacts": artifacts}), tuple(dropped)


def record_artifact(
    session: ConversationSession,
    step: StepId,
    *,
    payload: dict,
    payload_type: str,
    fingerprint: str,
    turn_index: int,
) -> ConversationSession:
    artifacts = dict(session.artifacts)
    artifacts[step] = StepArtifact(
        step=step,
        fingerprint=fingerprint,
        computed_on_turn=turn_index,
        payload=payload,
        payload_type=payload_type,
    )
    return session.model_copy(update={"artifacts": artifacts})


__all__ = [
    "STEP_INPUT_SLOTS",
    "compute_fingerprints",
    "invalidate",
    "record_artifact",
]
