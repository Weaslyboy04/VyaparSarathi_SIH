"""Conservative entity resolution / deduplication (CLAUDE.md §9)."""

from vyaparsarathi.dedup.deduplicator import (
    Deduplicator,
    DedupResult,
    MergeDecision,
    UncertainPair,
    pair_distance_m,
    pair_name_similarity,
)

__all__ = [
    "Deduplicator",
    "DedupResult",
    "MergeDecision",
    "UncertainPair",
    "pair_name_similarity",
    "pair_distance_m",
]
