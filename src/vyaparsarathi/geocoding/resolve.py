"""Disambiguation: candidate list -> one :class:`ResolvedPlace`, or an error.

Pure and independently testable (CLAUDE.md §28). The rule is conservative
(CLAUDE.md §2 step 2, §26.1): never silently pick one of several genuinely
different places.

* 0 candidates                      -> :class:`GeocodingError`
* 1 candidate                       -> resolved
* N candidates within ``cluster_m`` -> resolved to the top rank, the rest kept
  as ``alternates`` (they describe the same place at slightly different points)
* N candidates spread wider         -> :class:`LocationAmbiguousError`
"""

from __future__ import annotations

from vyaparsarathi.errors import GeocodingError, LocationAmbiguousError
from vyaparsarathi.models.place import PlaceCandidate, ResolvedPlace
from vyaparsarathi.utils.geo import haversine_m

# Candidates closer than this to the best one are treated as the same place.
DEFAULT_CLUSTER_M = 2_000.0


def _rank_key(candidate: PlaceCandidate) -> tuple[float, float]:
    # Higher importance first; break ties by lower (finer) place_rank.
    importance = candidate.importance if candidate.importance is not None else 0.0
    place_rank = float(candidate.place_rank) if candidate.place_rank is not None else 99.0
    return (-importance, place_rank)


def resolve_place(
    query: str,
    candidates: list[PlaceCandidate],
    *,
    cluster_m: float = DEFAULT_CLUSTER_M,
) -> ResolvedPlace:
    if not candidates:
        raise GeocodingError(f"No place matched {query!r}")

    ranked = sorted(candidates, key=_rank_key)
    best = ranked[0]
    rest = ranked[1:]

    if not rest:
        return ResolvedPlace.from_candidate(query, best)

    near: list[PlaceCandidate] = []
    far: list[PlaceCandidate] = []
    for candidate in rest:
        distance = haversine_m(
            best.latitude, best.longitude, candidate.latitude, candidate.longitude
        )
        (near if distance <= cluster_m else far).append(candidate)

    if far:
        raise LocationAmbiguousError(query, ranked)

    return ResolvedPlace.from_candidate(query, best, alternates=near)


def rank_candidates(candidates: list[PlaceCandidate]) -> list[PlaceCandidate]:
    """The candidate order the ambiguous listing (and ``--candidate N``) uses."""
    return sorted(candidates, key=_rank_key)


def select_candidate(query: str, candidates: list[PlaceCandidate], index: int) -> ResolvedPlace:
    """Explicitly resolve to the 1-based ``index``-th candidate.

    ``index`` counts positions in :func:`rank_candidates` order — the same order
    a caller sees in a ``LocationAmbiguousError`` / the CLI's candidate list.
    Performs no geocoding. Raises :class:`GeocodingError` when the geocoder
    returned nothing, and ``ValueError`` when ``index`` is out of range (so the
    caller must choose deliberately — candidate 1 is never assumed).
    """
    if not candidates:
        raise GeocodingError(f"No place matched {query!r}")
    ranked = rank_candidates(candidates)
    if index < 1 or index > len(ranked):
        raise ValueError(
            f"candidate {index} is out of range: {len(ranked)} candidate(s) available "
            f"(choose 1..{len(ranked)})"
        )
    return ResolvedPlace.from_candidate(query, ranked[index - 1])
