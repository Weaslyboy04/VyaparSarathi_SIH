"""Conservative, provenance-preserving deduplication (CLAUDE.md §9).

Design choices, all from §9:

* **Conservative.** A pair merges only on *combined* strong evidence: normalized
  name similarity >= ``name_similarity_merge`` **and** distance
  <= ``distance_merge_m`` **and** compatible categories. When in doubt, don't
  merge — a false merge destroys a real competitor.
* **No transitive chains.** A record joins an existing cluster only if it merges
  pairwise with *every* member. So A~B and B~C but A/~C yields {A, B} and {C},
  never {A, B, C}.
* **Provenance preserved.** The merged record carries every contributing
  ``(source, source_id, retrieved_at)``; the surviving ``name`` is the
  best candidate, originals remain reachable via provenance and the source
  record store.
* **Reversible.** Every merge is recorded as a :class:`MergeDecision` (which
  records, which rule, the scores) rather than only its result.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field
from rapidfuzz import fuzz

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.models.business import NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.taxonomy import BusinessCategory, categories_compatible
from vyaparsarathi.utils.geo import haversine_m
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)


def pair_name_similarity(a: NormalizedBusiness, b: NormalizedBusiness) -> float:
    """0..100 token-set similarity of the two normalized names.

    Returns 0.0 if either name is missing: we never merge unnamed records on
    geometry alone.
    """
    if not a.normalized_name or not b.normalized_name:
        return 0.0
    return float(fuzz.token_set_ratio(a.normalized_name, b.normalized_name))


def pair_distance_m(a: NormalizedBusiness, b: NormalizedBusiness) -> float:
    return haversine_m(a.latitude, a.longitude, b.latitude, b.longitude)


class MergeDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kept_source_id: str
    absorbed_source_id: str
    name_similarity: float
    distance_m: float
    categories: tuple[BusinessCategory, BusinessCategory]
    rule: str = "name>=merge & distance<=merge & category_compatible"
    reason: str


class UncertainPair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a_source_id: str
    b_source_id: str
    name_similarity: float
    distance_m: float
    reason: str


class DedupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    businesses: list[NormalizedBusiness]
    merges: list[MergeDecision] = Field(default_factory=list)
    uncertain_pairs: list[UncertainPair] = Field(default_factory=list)

    @property
    def merged_count(self) -> int:
        return len(self.merges)


class Deduplicator:
    def __init__(self, settings: Settings | None = None) -> None:
        s = settings or get_settings()
        self.name_merge = s.dedup_name_similarity_merge
        self.name_uncertain = s.dedup_name_similarity_uncertain
        self.dist_merge = s.dedup_distance_merge_m
        self.dist_uncertain = s.dedup_distance_uncertain_m

    # -- pair predicates ---------------------------------------------------

    def _is_merge(self, a: NormalizedBusiness, b: NormalizedBusiness) -> tuple[bool, float, float]:
        sim = pair_name_similarity(a, b)
        dist = pair_distance_m(a, b)
        ok = (
            sim >= self.name_merge
            and dist <= self.dist_merge
            and categories_compatible(a.category, b.category)
        )
        return ok, sim, dist

    def _is_uncertain(
        self, a: NormalizedBusiness, b: NormalizedBusiness
    ) -> tuple[bool, float, float]:
        sim = pair_name_similarity(a, b)
        dist = pair_distance_m(a, b)
        ok = (
            sim >= self.name_uncertain
            and dist <= self.dist_uncertain
            and categories_compatible(a.category, b.category)
        )
        return ok, sim, dist

    # -- merge combination ----------------------------------------------------

    @staticmethod
    def _combine(cluster: list[NormalizedBusiness]) -> NormalizedBusiness:
        # cluster is ordered best-first (see dedupe()).
        rep = cluster[0]
        named = next((b for b in cluster if b.name), None)
        best_category = next(
            (b.category for b in cluster if b.category is not BusinessCategory.UNKNOWN),
            rep.category,
        )
        addressed = next((b for b in cluster if b.address), None)

        provenance: list[ProvenanceEntry] = []
        seen: set[tuple[str, str]] = set()
        for member in cluster:
            for entry in member.provenance:
                key = (entry.source.value, entry.source_id)
                if key not in seen:
                    seen.add(key)
                    provenance.append(entry)

        distinct_sources = {e.source for e in provenance}
        quality = max(b.data_quality for b in cluster)
        if len(distinct_sources) > 1:
            quality = min(1.0, quality + 0.05)

        merged = rep.model_copy(
            update={
                "name": named.name if named else rep.name,
                "normalized_name": named.normalized_name if named else rep.normalized_name,
                "category": best_category,
                "address": addressed.address if addressed else rep.address,
                "first_seen": min(b.first_seen for b in cluster),
                "last_updated": max(b.last_updated for b in cluster),
                "data_quality": round(quality, 3),
                "provenance": provenance,
            }
        )
        return merged

    # -- entrypoint ---------------------------------------------------------

    def dedupe(self, businesses: list[NormalizedBusiness]) -> DedupResult:
        # Deterministic order: best data_quality first, then source_id.
        ordered = sorted(businesses, key=lambda b: (-b.data_quality, b.source_id))

        clusters: list[list[NormalizedBusiness]] = []
        merges: list[MergeDecision] = []

        for business in ordered:
            for cluster in clusters:
                results = [self._is_merge(business, member) for member in cluster]
                if all(ok for ok, _, _ in results):
                    for member, (_, sim, dist) in zip(cluster, results, strict=True):
                        merges.append(
                            MergeDecision(
                                kept_source_id=member.source_id,
                                absorbed_source_id=business.source_id,
                                name_similarity=round(sim, 1),
                                distance_m=round(dist, 1),
                                categories=(member.category, business.category),
                                reason=(
                                    f"name {sim:.0f} >= {self.name_merge:.0f} and "
                                    f"{dist:.0f} m <= {self.dist_merge:.0f} m and "
                                    f"categories {member.category}/{business.category} compatible"
                                ),
                            )
                        )
                    cluster.append(business)
                    break
            else:
                clusters.append([business])

        merged_businesses = [
            cluster[0] if len(cluster) == 1 else self._combine(cluster) for cluster in clusters
        ]

        uncertain = self._collect_uncertain(clusters)

        if merges:
            logger.info("dedup merged %d record(s) into clusters", len(merges))
        if uncertain:
            logger.info("dedup flagged %d uncertain pair(s) for review", len(uncertain))

        return DedupResult(businesses=merged_businesses, merges=merges, uncertain_pairs=uncertain)

    def _collect_uncertain(self, clusters: list[list[NormalizedBusiness]]) -> list[UncertainPair]:
        """Pairs that are suggestive but not strong enough to merge.

        Only across *different* clusters (within-cluster pairs already merged).
        """
        reps = [c[0] for c in clusters]
        pairs: list[UncertainPair] = []
        for i in range(len(reps)):
            for j in range(i + 1, len(reps)):
                a, b = reps[i], reps[j]
                is_merge, _, _ = self._is_merge(a, b)
                if is_merge:
                    continue
                ok, sim, dist = self._is_uncertain(a, b)
                if ok:
                    pairs.append(
                        UncertainPair(
                            a_source_id=a.source_id,
                            b_source_id=b.source_id,
                            name_similarity=round(sim, 1),
                            distance_m=round(dist, 1),
                            reason=(
                                f"name {sim:.0f} in [{self.name_uncertain:.0f},"
                                f"{self.name_merge:.0f}) or distance {dist:.0f} m in "
                                f"({self.dist_merge:.0f},{self.dist_uncertain:.0f}] m"
                            ),
                        )
                    )
        return pairs
