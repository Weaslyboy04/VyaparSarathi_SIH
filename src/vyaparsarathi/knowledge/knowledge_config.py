"""The single configuration layer for Phase 5 (CLAUDE.md §18, §19, §22).

Mirrors ``market/demand_config.py`` / ``finance/finance_config.py``: a frozen
Pydantic model of tunables, echoed verbatim into every
:class:`~vyaparsarathi.models.parameters.ParameterResolution`. Every value is
``# [tunable]``. Nothing here is a market claim — it describes how confidently
this engine treats a document tier, how fast a stale fact decays, and how
strictly it enforces the applicability rules a document itself states.

``reference_year`` is the deliberate substitute for ``date.today().year``: the
resolver must never read a wall clock (CLAUDE.md §28), so "how current is this
fact" is a *stated assumption*, exactly as ``DemandConfig.reference_year`` is
for Phase 2C.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.models.knowledge import SourceTier

# --- fixed caveats (copied verbatim into every result) -----------------------
#
# Human-authored; NOT generated interpretation (that is Phase 6). [decision]
_CAVEATS: tuple[str, ...] = (
    "A resolved parameter is a fact retrieved from a specific document, not a "
    "verdict on whether it applies to this entrepreneur's situation — conditions "
    "stated in the source document are carried verbatim and are never interpreted "
    "or resolved by this engine.",
    "Parameter confidence measures how well a FACT is evidenced (source tier, "
    "freshness, applicability match, cross-document agreement) — it is not a "
    "probability of loan approval and not a measure of the business.",
    "A parameter's confidence, the Phase 3 opportunity score, the Phase 2C/2D "
    "market and demand confidence figures, and Phase 4's assumption_share are "
    "five separate measurements. None of them multiplies or implies another.",
    "This engine resolves the value of a named parameter from reviewed source "
    "documents. It does not determine scheme eligibility, loan sanction, or "
    "compliance — a bank's or scheme's own appraisal is a separate process this "
    "does not replace.",
    "A conflicting or stale reading is never averaged or arbitrarily chosen "
    "between; it is reported as unresolved, naming every candidate so a human "
    "can see what the corpus actually contains.",
)


class KnowledgeConfig(BaseModel):
    """Tunable parameters for the Phase 5 knowledge/evidence layer. Frozen;
    echoed into every :class:`~vyaparsarathi.models.parameters.
    ParameterResolution` for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- freshness (mirrors DemandConfig; same formula, see confidence.py) ---
    reference_year: int = 2026  # [tunable] a stated assumption, NOT a clock read
    freshness_floor: float = Field(default=0.40, ge=0.0, le=1.0)
    freshness_decay_per_year: float = Field(default=0.05, ge=0.0, le=1.0)

    # --- source-tier weights (CLAUDE.md §19) --- [tunable]
    tier_weight: dict[SourceTier, float] = Field(
        default_factory=lambda: {
            SourceTier.GOVT_PRIMARY: 1.00,
            SourceTier.REGULATOR: 0.95,
            SourceTier.PUBLIC_SECTOR_INSTITUTION: 0.85,
            SourceTier.INDUSTRY_BODY: 0.65,
            SourceTier.SECONDARY: 0.40,
        }
    )

    # One document, however authoritative, cannot justify top confidence — the
    # Phase 5 analogue of discovery/confidence.py's SINGLE_SOURCE_CEILING.
    # [tunable]
    single_document_ceiling: float = Field(default=0.80, ge=0.0, le=1.0)

    # Applied to applicability_match when a NATIONAL row answers a STATE/
    # DISTRICT query (a permitted fallback, never a rejection). [tunable]
    national_fallback_penalty: float = Field(default=0.85, ge=0.0, le=1.0)
    # Applied when a category-generic row answers a category-specific query.
    # [tunable]
    category_generic_penalty: float = Field(default=0.90, ge=0.0, le=1.0)

    # A capped bonus when >= 2 independent documents at the same tier state the
    # same (value, unit) — CLAUDE.md §22 "agreement between sources". The
    # multiplier applied is `min(1 + agreement_bonus, agreement_bonus_cap)`;
    # `agreement_bonus_cap` is a multiplier ceiling, so it must be >= 1.0 (a
    # cap of 1.0 would make the bonus permanently inert). [tunable]
    agreement_bonus: float = Field(default=0.15, ge=0.0)
    agreement_bonus_cap: float = Field(default=1.15, ge=1.0)

    # When True, a SourcedParameter carrying non-empty applicability.conditions
    # is held back (status CONDITIONS_UNRESOLVED) rather than treated as an
    # ordinary candidate. [tunable]
    reject_unresolved_conditions: bool = True

    # --- lexical retrieval (knowledge/lexical.py) --- [tunable]
    bm25_k1: float = Field(default=1.5, ge=0.0)
    bm25_b: float = Field(default=0.75, ge=0.0, le=1.0)
    # A rapidfuzz fallback only kicks in when BM25 returns fewer than `limit`
    # hits, to catch transliteration/OCR variants without changing ranking of
    # genuine BM25 matches. [tunable]
    fuzzy_fallback_min_ratio: float = Field(default=70.0, ge=0.0, le=100.0)

    # --- data ---
    caveats: tuple[str, ...] = _CAVEATS


DEFAULT_KNOWLEDGE_CONFIG = KnowledgeConfig()
