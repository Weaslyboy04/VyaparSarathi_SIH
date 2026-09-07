"""The provider-agnostic request/response shape (CLAUDE.md §4.1, §25 Phase
6). No vendor SDK types leak past `llm/provider.py` — every caller (`llm/
structured.py`, `llm/orchestrator.py`, tests, the demo's
`ScriptedLlmProvider`) sees only these two models.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class LlmRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LlmMessage(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    role: LlmRole
    content: str


class LlmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    messages: tuple[LlmMessage, ...] = Field(min_length=1)
    max_output_tokens: int = Field(gt=0)
    temperature: float = Field(ge=0.0, le=2.0)
    # Which entry in `llm/prompts.py::PROMPT_REGISTRY` produced this request
    # — logged (CLAUDE.md §33) and used by `ScriptedLlmProvider` to look up
    # the right canned response; never sent to a real provider's API.
    prompt_id: str = ""


class LlmResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str
    prompt_id: str = ""


__all__ = ["LlmMessage", "LlmRequest", "LlmResponse", "LlmRole"]
