"""Tunable Tier 1 SWOT parameters (CLAUDE.md §33: "all thresholds ... live in
config/ ... and are imported, not redefined"; mirrors
`conversation/conversation_config.py`'s own shape). Frozen.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SwotConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # A ScoreComponent.value (0..100) at or above this counts as a Strength.
    # [tunable]
    strong_component_threshold: float = Field(default=70.0, ge=0.0, le=100.0)

    # Deterministic cap per quadrant — truncates, never pads. [tunable]
    max_items_per_quadrant: int = Field(default=6, ge=1)

    # Fixed, human-authored — mirrors finance/finance_config.py's caveat
    # convention. Never generated interpretation. [decision]
    caveats: tuple[str, ...] = (
        "Every item here is drawn from an already-computed market, opportunity, "
        "finance, or structuring result — this view adds no new evidence and "
        "performs no new calculation.",
        "An empty quadrant means no qualifying evidence was found for it, not "
        "that the business has none of that kind — see this quadrant's own note.",
    )


DEFAULT_SWOT_CONFIG = SwotConfig()

__all__ = ["DEFAULT_SWOT_CONFIG", "SwotConfig"]
