"""SWOT result models (CLAUDE.md §22, §23; Tier 1 "Deterministic Structured
SWOT"). PURE.

`SwotItem.source_ref` is a dotted path into the already-computed artifact the
item was drawn from — never a new fact and never LLM prose. `SwotResult`
carries no verdict of its own; `conversation/recommendation.py::combine`
(unmodified) still owns the proceed/adjust/pivot decision.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.conversation.swot_config import DEFAULT_SWOT_CONFIG, SwotConfig


class SwotQuadrant(StrEnum):
    STRENGTH = "strength"
    WEAKNESS = "weakness"
    OPPORTUNITY = "opportunity"
    THREAT = "threat"


class SwotOrigin(StrEnum):
    """Which already-computed result an item was drawn from — the SWOT
    analogue of `conversation/bundle.py::FactOrigin`."""

    MARKET_ASSESSMENT = "market_assessment"
    OPPORTUNITY = "opportunity"
    FINANCE = "finance"
    STRESS = "stress"
    STRUCTURE = "structure"


class SwotStatus(StrEnum):
    OK = "ok"
    NO_EVIDENCE = "no_evidence"  # the opportunity result itself had no usable evidence


class SwotItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str  # stable, machine-readable, unique within a result
    quadrant: SwotQuadrant
    text: str  # copied verbatim from an existing Finding/component/status message
    origin: SwotOrigin
    source_ref: str  # dotted path into the originating artifact/field


class SwotResult(BaseModel):
    """Tier 1 output. Deterministic, JSON-serializable. Carries no numeric
    score and no proceed/adjust/pivot verdict of its own (§12, §22)."""

    model_config = ConfigDict(extra="forbid")

    status: SwotStatus
    items: list[SwotItem] = Field(default_factory=list)
    # Evidence-quality notes (Phase 2D data_caveats + confidence), never
    # promoted into any quadrant — sparse evidence stays sparse, not a claim.
    data_caveats: list[str] = Field(default_factory=list)
    # One entry per quadrant that ended up with zero items, naming why —
    # never silently empty (CLAUDE.md §3.5).
    quadrant_notes: dict[SwotQuadrant, str] = Field(default_factory=dict)

    caveats: list[str] = Field(default_factory=list)
    config: SwotConfig = Field(default_factory=lambda: DEFAULT_SWOT_CONFIG)
    warnings: list[str] = Field(default_factory=list)


__all__ = ["SwotItem", "SwotOrigin", "SwotQuadrant", "SwotResult", "SwotStatus"]
