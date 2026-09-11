"""Tunable parameters for :func:`vyaparsarathi.market.proposed.resolve_proposed_business`'s
fuzzy-typo fallback (CLAUDE.md §11, STEP 4, §33: thresholds live in config,
imported not redefined).

The fuzzy tier only ever runs when the exact/alias pass (CLAUDE.md's
preferred, non-negotiable first step) found ZERO matches — never when it
found several conflicting ones, which is genuine ambiguity, not a typo. It
must resolve only a single, clearly-ahead candidate; anything else stays
`resolved=False` (CLAUDE.md §30: never merge/classify on weak evidence)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ProposedBusinessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Minimum rapidfuzz token_sort_ratio (0-100) for the best-scoring category
    # to be accepted at all. [tunable] — chosen to catch common single-letter
    # typos/transpositions ("grocerry" -> "grocery", 93; "farniture" ->
    # "furniture", 89) while staying well clear of unrelated-word scores
    # (typically < 60 for genuinely different businesses).
    fuzzy_match_threshold: float = Field(default=85.0, ge=0.0, le=100.0)

    # The best-scoring category must beat the second-best DISTINCT category
    # by at least this many points, or the match is treated as competing/
    # ambiguous and rejected (never silently pick one of two close guesses).
    # [tunable]
    fuzzy_match_margin: float = Field(default=8.0, ge=0.0)


DEFAULT_PROPOSED_BUSINESS_CONFIG = ProposedBusinessConfig()

__all__ = ["DEFAULT_PROPOSED_BUSINESS_CONFIG", "ProposedBusinessConfig"]
