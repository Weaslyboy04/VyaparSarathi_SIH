"""What one turn means, structurally (CLAUDE.md §3.1, §25 Phase 6). PURE.

A :class:`TurnUnderstanding` is produced by exactly two callers, both in
`llm/`: `llm/structured.py` (parsing the LLM's JSON response) when
``llm_enabled=True``, or `llm/orchestrator.py`'s deterministic structured-input
path when ``llm_enabled=False`` (the user's own explicit instruction: without
an LLM this layer does NOT attempt free-text extraction — it accepts only the
caller-supplied :class:`~vyaparsarathi.conversation.understanding.SlotUpdateInput`
tuples a structured channel already knows how to build). Either way the result
lands here in the same shape and is validated identically by
`conversation/deltas.py` — the LLM is not a trusted source of a *value*, only
of a *pointer into the user's own text* (``value_token``), which is checked
against ``raw_text`` before anything is derived from it.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.conversation.session_models import SlotName, StepId
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.profile import AssetKind
from vyaparsarathi.models.taxonomy import BusinessCategory


class Intent(StrEnum):
    """A closed classification of what the turn is doing — never a free-form
    label, so `conversation/planner.py` can switch on it exhaustively."""

    PROVIDE_INFO = "provide_info"  # states one or more new facts
    CORRECT_SLOT = "correct_slot"  # explicitly changes a previously-stated fact
    DECLINE_SLOT = "decline_slot"  # refuses to answer an asked question
    SELECT_CANDIDATE = "select_candidate"  # picks a numbered choice (location/pivot)
    ANSWER_CLARIFICATION = "answer_clarification"  # answers the specific question asked
    ASK_QUESTION = "ask_question"  # the user is asking US something
    UNCLEAR = "unclear"  # extraction/repair both failed; the planner asks its own question


class SlotUpdateInput(BaseModel):
    """One proposed change to a scalar `Slot`. ``value_token`` MUST occur
    verbatim in ``raw_text`` — `conversation/deltas.py` enforces this before
    deriving anything (the Phase 5 `SourcedParameter.value_token` pattern,
    reused verbatim for user-stated numbers: CLAUDE.md §3.1, §30)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: SlotName
    raw_text: str = Field(min_length=1)
    value_token: str = Field(min_length=1)
    normalization: ValueNormalization = ValueNormalization.AS_STATED


class AssetUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[AssetKind, ...] = Field(min_length=1)
    raw_text: str = Field(min_length=1)


class ExperienceUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[BusinessCategory, ...] = Field(min_length=1)
    raw_text: str = Field(min_length=1)


class TurnUnderstanding(BaseModel):
    """The structured meaning of one turn — the sole boundary between
    "whatever produced this" (LLM extraction, or a structured channel input)
    and the deterministic conversation state machine. `conversation/deltas.py`
    is the only consumer."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    slot_updates: tuple[SlotUpdateInput, ...] = ()
    asset_update: AssetUpdateInput | None = None
    experience_update: ExperienceUpdateInput | None = None
    declined_slots: tuple[SlotName, ...] = ()
    selected_choice: int | None = None  # 1-based, for SELECT_CANDIDATE
    # The LLM/channel MAY name a step to prioritise; `planner.py` only ever
    # narrows this against what is actually ready — never expands it
    # (property-tested in test_conversation_workflow.py).
    requested_step: StepId | None = None
    raw_message: str = ""  # kept for the session audit trail only
    notes: str = ""


__all__ = [
    "AssetUpdateInput",
    "ExperienceUpdateInput",
    "Intent",
    "SlotUpdateInput",
    "TurnUnderstanding",
]
