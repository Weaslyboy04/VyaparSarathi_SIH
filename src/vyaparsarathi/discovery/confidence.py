"""Data-coverage confidence for a Phase 1 discovery run (CLAUDE.md §22).

This is **not** a viability score. It answers only "how well can we see the local
market?" and is deliberately capped: a single, contributor-dependent source (OSM)
cannot justify high confidence on its own (CLAUDE.md §6, §22). It will be
recomputed properly once more sources exist.
"""

from __future__ import annotations

# One uneven source => never above this.
SINGLE_SOURCE_CEILING = 0.75


def coverage_confidence(
    *,
    raw_count: int,
    normalized: int,
    after_dedup: int,
    unmapped_tag_count: int,
    mirror_fallback_used: bool,
) -> float:
    if normalized == 0:
        # We found nothing — we cannot claim the area is well covered, nor that
        # no businesses exist there.
        return 0.0

    score = 0.35
    score += min(0.30, 0.03 * after_dedup)  # bigger sample -> a bit more trust
    mapped_share = 1.0 - (unmapped_tag_count / max(raw_count, 1))
    score += 0.20 * max(0.0, mapped_share)
    if mirror_fallback_used:
        score -= 0.05

    return round(min(SINGLE_SOURCE_CEILING, max(0.05, score)), 2)
