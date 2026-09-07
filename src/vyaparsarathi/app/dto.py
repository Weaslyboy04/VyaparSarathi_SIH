"""Channel-neutral DTOs (CLAUDE.md §25 Phase 6 — the user's clarification:
"Phase 6 exposes channel-neutral APIs/interfaces").

`MessageRequest` / `AdvisoryReply` are deliberately presentation-neutral:
`messages` is plain text lines, `choices` are numbered options, `expects`
says what kind of answer is wanted next — never WhatsApp buttons, never
ANSI/markdown formatting. Phase 7's WhatsApp adapter maps `choices` onto an
interactive list message; a CLI numbers them in a terminal. Neither reshapes
this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.conversation.render import Narrative
from vyaparsarathi.conversation.session_models import SlotName, StepId
from vyaparsarathi.conversation.severity import Severity
from vyaparsarathi.conversation.understanding import (
    AssetUpdateInput,
    ExperienceUpdateInput,
    SlotUpdateInput,
)


class ChannelId(StrEnum):
    """Provenance only — never used to change what advice is given."""

    CLI = "cli"
    WHATSAPP = "whatsapp"
    WEB = "web"
    TEST = "test"


class ExpectedInput(StrEnum):
    FREE_TEXT = "free_text"
    CHOICE = "choice"
    AMOUNT = "amount"
    NONE = "none"


class AdvisoryPhase(StrEnum):
    COLLECTING = "collecting"
    ANALYSING = "analysing"
    COMPLETE = "complete"
    BLOCKED = "blocked"


class Choice(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    index: int
    label: str


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    choices: tuple[Choice, ...] = ()


class StartSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str | None = None  # None -> the backend generates one
    channel: ChannelId
    started_at: datetime


class SessionHandle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    created_at: datetime


class MessageRequest(BaseModel):
    """A structured, channel-neutral inbound turn.

    ``text`` is the user's raw message (kept for audit and, when
    ``llm_enabled=True``, sent to the extraction prompt). ``slot_updates``
    etc. are the deterministic, structured path
    (``llm_enabled=False`` — CLAUDE.md §3.1: no arbitrary free-text
    extraction is attempted without an LLM) a channel can fill in directly
    once it already knows which field a reply answers (e.g. a WhatsApp list
    reply, a web form field, or a scripted demo turn).
    """

    model_config = ConfigDict(extra="forbid")

    session_id: str
    text: str = ""
    channel: ChannelId
    received_at: datetime
    locale_hint: str = ""
    slot_updates: tuple[SlotUpdateInput, ...] = ()
    asset_update: AssetUpdateInput | None = None
    experience_update: ExperienceUpdateInput | None = None
    declined_slots: tuple[SlotName, ...] = ()
    selected_choice: int | None = None
    requested_step: StepId | None = None


class AdvisoryReply(BaseModel):
    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn_index: int
    messages: tuple[OutboundMessage, ...] = ()
    expects: ExpectedInput = ExpectedInput.NONE
    choices: tuple[Choice, ...] = ()
    state: AdvisoryPhase = AdvisoryPhase.COLLECTING
    severity: Severity = Severity.INFO
    narrative: Narrative = Field(default_factory=Narrative)
    warnings: list[str] = Field(default_factory=list)


class AdvisorySnapshot(BaseModel):
    """The Phase 8 DPR-generation contract — the composite envelope
    `scripts/discover_businesses.py`'s `--json` never had (it emits only the
    deepest phase's result). Carries every phase's stored artifact payload,
    the slot table (with provenance), and the recommendation, so a DPR
    generator can walk CLAUDE.md §23's four-way distinction without
    re-running anything or importing `conversation/`."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    turn_index: int
    updated_at: datetime
    profile: dict
    slots: dict[str, dict]
    artifacts: dict[str, dict]  # StepId.value -> StepArtifact.model_dump(mode="json")
    recommendation: dict | None
    warnings: list[str]


def new_session_id() -> str:
    """The only place `app/` reads a UUID source — `AdvisoryService.
    start_session` when the caller does not supply one."""
    return str(uuid4())


__all__ = [
    "AdvisoryPhase",
    "AdvisoryReply",
    "AdvisorySnapshot",
    "ChannelId",
    "Choice",
    "ExpectedInput",
    "MessageRequest",
    "OutboundMessage",
    "SessionHandle",
    "StartSessionRequest",
    "new_session_id",
]
