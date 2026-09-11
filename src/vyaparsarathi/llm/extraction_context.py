"""A small, explicit "what's currently pending" bundle for the LLM extractor
(CLAUDE.md §3.1, §25 Phase 6). PURE — no I/O, no provider call.

Without this, `extract_understanding` sees only the latest raw message and
has no way to recognise a natural reply to its own pending question ("the
first one", "Vaishali one", "pick1") or a correction against an
already-collected fact. This module builds that context from
already-committed session state only — never a live engine result, never a
hidden prompt, never a secret.

**This is a hint, not a trust boundary.** `conversation/deltas.py::
apply_understanding` always re-validates any `selected_choice` the LLM
returns against the session's actual live `AMBIGUOUS` slot options,
regardless of what this context said (see its out-of-range handling). A
stale or wrong context can at worst make the LLM's guess less accurate,
never let it bypass validation — the same is true if this module is skipped
entirely (``context=None`` is always a safe, supported call).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
    ConversationMode,
)
from vyaparsarathi.conversation.planner import NextActionKind, decide
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    SlotState,
)
from vyaparsarathi.conversation.understanding import Intent, TurnUnderstanding

# Only these states reflect a fact the user could plausibly be correcting —
# MISSING/AMBIGUOUS/DECLINED are not "already collected" in any sense a
# correction phrase would reference.
_COLLECTED_STATES = frozenset(
    {SlotState.USER_PROVIDED, SlotState.SOURCED, SlotState.ASSUMED, SlotState.CALCULATED}
)


class CollectedFact(BaseModel):
    """One already-collected fact, rendered compactly for the prompt — a
    label and the user's own short phrase, never raw provenance, never a
    full history."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    value_text: str


class ExtractionContext(BaseModel):
    """What the deterministic pipeline is currently waiting on, plus a
    compact summary of what is already known — built once per turn, before
    the LLM is called, and passed to it as plain labelled text (never a
    hidden system instruction, never a raw engine payload)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pending_kind: str = ""  # a NextActionKind value, e.g. "ask_disambiguation"
    pending_slot: SlotName | None = None
    pending_question: str = ""
    # 1-based display order — exactly what the user was shown, only
    # populated when a choice is genuinely pending (ASK_DISAMBIGUATION).
    pending_options: tuple[str, ...] = ()
    collected: tuple[CollectedFact, ...] = ()


def build_extraction_context(
    session: ConversationSession,
    *,
    cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG,
    mode: ConversationMode = ConversationMode.DEVELOPER,
) -> ExtractionContext:
    """PURE. Peeks `conversation.planner.decide()` with a fact-free
    (``Intent.UNCLEAR``, no updates) understanding to learn what the system
    is currently waiting on, without running or invalidating anything —
    `decide()` itself is pure and side-effect-free, so this peek is safe to
    call on every turn before extraction, and again for real immediately
    afterward in `llm/orchestrator.py::run_turn`."""
    peek = decide(session, TurnUnderstanding(intent=Intent.UNCLEAR), cfg=cfg, mode=mode)
    pending_options = peek.options if peek.kind is NextActionKind.ASK_DISAMBIGUATION else ()
    return ExtractionContext(
        pending_kind=peek.kind.value,
        pending_slot=peek.slot,
        pending_question=peek.message,
        pending_options=pending_options,
        collected=_collected_facts(session),
    )


def _collected_facts(session: ConversationSession) -> tuple[CollectedFact, ...]:
    facts: list[CollectedFact] = []
    for name in sorted(session.slots, key=lambda n: n.value):
        slot = session.slots[name]
        if slot.state not in _COLLECTED_STATES:
            continue
        text = slot.current.raw_text or (
            str(slot.current.value) if slot.current.value is not None else ""
        )
        if not text:
            continue
        facts.append(CollectedFact(label=name.value.replace("_", " "), value_text=text))
    if session.assets.current.state in _COLLECTED_STATES and session.assets.current.items:
        items = ", ".join(sorted(a.value for a in session.assets.current.items))
        facts.append(CollectedFact(label="assets", value_text=items))
    if (
        session.experience_categories.current.state in _COLLECTED_STATES
        and session.experience_categories.current.items
    ):
        items = ", ".join(sorted(c.value for c in session.experience_categories.current.items))
        facts.append(CollectedFact(label="experience", value_text=items))
    return tuple(facts)


__all__ = ["CollectedFact", "ExtractionContext", "build_extraction_context"]
