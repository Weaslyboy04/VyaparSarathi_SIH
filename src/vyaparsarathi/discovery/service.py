"""The Phase 1 discovery pipeline (CLAUDE.md §26.1).

``DiscoveryService.discover`` never raises for an operational problem — a failed
geocode, an unreachable source, zero results all come back as a
:class:`DiscoveryResult` with an explanatory ``status`` and ``warnings`` and no
fabricated data. It *does* raise ``ValueError`` for a caller mistake (bad radius),
which is a programming error, not a runtime condition.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

from vyaparsarathi.categories.osm_query_tags import selectors_for_many
from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.database.memory import InMemoryBusinessRepository
from vyaparsarathi.database.repository import BusinessRepository
from vyaparsarathi.dedup import Deduplicator
from vyaparsarathi.discovery.confidence import coverage_confidence
from vyaparsarathi.errors import (
    GeocodingError,
    HttpError,
    LocationAmbiguousError,
    NormalizationError,
    SourcePayloadError,
    SourceUnavailableError,
)
from vyaparsarathi.geocoding.base import Geocoder
from vyaparsarathi.geocoding.nominatim import NominatimGeocoder
from vyaparsarathi.geocoding.resolve import resolve_place, select_candidate
from vyaparsarathi.models.business import BusinessHit
from vyaparsarathi.models.query import DiscoveryQuery
from vyaparsarathi.models.results import (
    CoverageSummary,
    DiscoveryResult,
    DiscoveryStatus,
    SourceCoverage,
)
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.normalization.business import normalize_osm_element
from vyaparsarathi.sources.osm.adapter import OverpassSource
from vyaparsarathi.utils.geo import haversine_m
from vyaparsarathi.utils.logging import get_logger
from vyaparsarathi.utils.time import Clock, utcnow

logger = get_logger(__name__)

_GEOCODE_LIMIT = 6


class DiscoveryService:
    def __init__(
        self,
        geocoder: Geocoder,
        source: OverpassSource,
        repository: BusinessRepository,
        settings: Settings | None = None,
        deduplicator: Deduplicator | None = None,
        clock: Clock = utcnow,
    ) -> None:
        self._geocoder = geocoder
        self._source = source
        self._repository = repository
        self._settings = settings or get_settings()
        self._dedup = deduplicator or Deduplicator(self._settings)
        self._clock = clock

    # -- public -----------------------------------------------------------

    def discover(
        self,
        location_text: str,
        category: BusinessCategory,
        radius_m: int,
        *,
        candidate: int | None = None,
        also_fetch: Sequence[BusinessCategory] = (),
    ) -> DiscoveryResult:
        """Run the Phase 1 pipeline for ``location_text``.

        ``candidate`` (1-based) explicitly selects one geocoding candidate — in
        the same order the ``location_ambiguous`` listing shows — instead of
        failing on ambiguity. The geocoder is still queried exactly once; no
        second lookup is made. An out-of-range ``candidate`` raises ``ValueError``
        (candidate 1 is never assumed).

        ``also_fetch`` widens the single Overpass query to cover extra categories
        in the same request (Phase 3 scores several candidate businesses over one
        fetch). The returned ``query`` / ``category`` still name the primary
        ``category``; every returned business is normalized and radius-clamped
        exactly as before. The default ``()`` reproduces Phase 1 behaviour
        unchanged — the fetch then asks only for ``category``'s selectors.
        """
        location_text = location_text.strip()
        if not location_text:
            raise ValueError("location_text must not be empty")
        if radius_m <= 0:
            raise ValueError("radius_m must be positive")
        if radius_m > self._settings.max_radius_m:
            raise ValueError(
                f"radius_m {radius_m} exceeds the configured maximum "
                f"{self._settings.max_radius_m} m"
            )

        base = {
            "query_text": location_text,
            "category": category,
            "requested_radius_m": radius_m,
        }

        # 1. Geocode (once) + 2. disambiguate or select ----------------------
        try:
            candidates = self._geocoder.geocode(location_text, limit=_GEOCODE_LIMIT)
            if candidate is not None:
                resolved = select_candidate(location_text, candidates, candidate)
            else:
                resolved = resolve_place(location_text, candidates)
        except LocationAmbiguousError as exc:
            logger.warning(
                "location %r ambiguous: %d candidates", location_text, len(exc.candidates)
            )
            return DiscoveryResult(
                status=DiscoveryStatus.LOCATION_AMBIGUOUS,
                candidates=list(exc.candidates),
                warnings=[
                    f"{len(exc.candidates)} plausible places match {location_text!r}; "
                    "re-run with a more specific location or --candidate N.",
                ],
                **base,
            )
        except (GeocodingError, HttpError, SourcePayloadError) as exc:
            logger.warning("geocoding failed for %r: %s", location_text, exc)
            return DiscoveryResult(
                status=DiscoveryStatus.LOCATION_NOT_FOUND,
                warnings=[f"Could not resolve {location_text!r}: {exc}"],
                **base,
            )

        query = DiscoveryQuery(
            location_text=location_text,
            latitude=resolved.latitude,
            longitude=resolved.longitude,
            radius_m=radius_m,
            category=category,
        )
        warnings: list[str] = []
        if candidate is not None and len(candidates) > 1:
            warnings.append(
                f"{len(candidates)} places matched {location_text!r}; candidate {candidate} "
                f"({resolved.display_name}) was selected explicitly."
            )
        if resolved.alternates:
            warnings.append(
                f"{len(resolved.alternates)} nearby alternate match(es) for "
                f"{location_text!r} were treated as the same place."
            )

        # 3. Which OSM tags to ask for ------------------------------------
        # Phase 3 may widen the fetch to sibling candidate categories; a single-
        # element list reproduces the Phase 1 selector set exactly.
        fetch_categories = [category, *also_fetch]
        selectors = selectors_for_many(fetch_categories)
        if len(fetch_categories) > 1:
            extra = ", ".join(sorted({c.value for c in also_fetch if c != category}))
            if extra:
                warnings.append(
                    f"Overpass query widened beyond {category.value!r} to also cover: {extra} "
                    "(Phase 3 candidate scan); every result is still normalized and "
                    "radius-clamped as usual."
                )
        if not selectors:
            warnings.append(f"Category {category!r} has no OSM tag mapping yet — nothing to query.")
            return DiscoveryResult(
                status=DiscoveryStatus.NO_RESULTS,
                query=query,
                resolved_place=resolved,
                sources_queried=[SourceName.OSM],
                confidence=0.0,
                warnings=warnings,
                **base,
            )

        # 4. Fetch raw OSM elements -------------------------------------
        try:
            fetch = self._source.fetch(query, selectors)
        except (SourceUnavailableError, SourcePayloadError, HttpError) as exc:
            logger.warning("Overpass fetch failed: %s", exc)
            return DiscoveryResult(
                status=DiscoveryStatus.SOURCE_UNAVAILABLE,
                query=query,
                resolved_place=resolved,
                sources_queried=[SourceName.OSM],
                confidence=0.0,
                warnings=[*warnings, f"OpenStreetMap / Overpass unavailable: {exc}"],
                **base,
            )

        # 5. Normalize -------------------------------------------------
        now = self._clock()
        normalized = []
        unmapped: Counter[str] = Counter()
        dropped_other = 0
        for element in fetch.elements:
            try:
                result = normalize_osm_element(element, now=now)
            except NormalizationError as exc:
                dropped_other += 1
                logger.debug("normalization dropped %s: %s", element.source_id, exc)
                continue
            normalized.append(result.business)
            if result.unmapped_tag:
                unmapped[result.unmapped_tag] += 1
        if unmapped:
            logger.info("unmapped OSM tags (for taxonomy expansion): %s", dict(unmapped))

        # 6. Deduplicate ---------------------------------------------
        dedup = self._dedup.dedupe(normalized)
        for decision in dedup.merges:
            logger.info(
                "merge %s <- %s (%s)",
                decision.kept_source_id,
                decision.absorbed_source_id,
                decision.reason,
            )

        # 7. Persist ----------------------------------------------------
        try:
            self._repository.save_businesses(dedup.businesses)
        except Exception as exc:  # noqa: BLE001 - persistence must not crash discovery
            logger.error("persistence failed: %s", exc)
            warnings.append(f"Results were not persisted: {exc}")

        # 8. Distances + radius clamp -------------------------------
        hits: list[BusinessHit] = []
        for business in dedup.businesses:
            distance = haversine_m(
                query.latitude, query.longitude, business.latitude, business.longitude
            )
            if distance <= radius_m:
                hits.append(BusinessHit(business=business, distance_m=distance))
        hits.sort(key=lambda h: h.distance_m)

        # 9. Coverage + confidence ---------------------------------
        source_coverage = SourceCoverage(
            source=SourceName.OSM,
            endpoint_used=fetch.endpoint_used,
            mirror_fallback_used=fetch.mirror_fallback_used,
            raw_records=fetch.raw_count,
            normalized=len(normalized),
            dropped_no_coordinates=fetch.dropped_no_coordinates,
            dropped_other=dropped_other,
            unmapped_tags=dict(unmapped),
        )
        coverage = CoverageSummary(
            per_source=[source_coverage],
            total_before_dedup=len(normalized),
            total_after_dedup=len(dedup.businesses),
            duplicates_merged=dedup.merged_count,
            uncertain_pairs=len(dedup.uncertain_pairs),
        )
        confidence = coverage_confidence(
            raw_count=fetch.raw_count,
            normalized=len(normalized),
            after_dedup=len(hits),
            unmapped_tag_count=sum(unmapped.values()),
            mirror_fallback_used=fetch.mirror_fallback_used,
        )

        if fetch.mirror_fallback_used:
            warnings.append("An Overpass mirror was used after the primary endpoint failed.")
        if fetch.dropped_no_coordinates:
            warnings.append(
                f"{fetch.dropped_no_coordinates} OSM element(s) had no usable coordinate "
                "and were dropped."
            )
        if unmapped:
            warnings.append(
                f"{sum(unmapped.values())} OSM element(s) across {len(unmapped)} tag(s) "
                "could not be categorised (recorded as 'unknown')."
            )
        warnings.append(
            "OpenStreetMap coverage of rural businesses is partial; absence from this "
            "list does not mean a business does not exist."
        )

        status = DiscoveryStatus.OK if hits else DiscoveryStatus.NO_RESULTS
        if not hits:
            warnings.insert(0, "No matching businesses were found in OpenStreetMap for this area.")

        return DiscoveryResult(
            status=status,
            query=query,
            resolved_place=resolved,
            businesses=hits,
            sources_queried=[SourceName.OSM],
            coverage=coverage,
            confidence=confidence,
            warnings=warnings,
            **base,
        )


def build_default_service(
    settings: Settings | None = None,
    repository: BusinessRepository | None = None,
) -> DiscoveryService:
    """Wire the standard Phase 1 stack (Nominatim + Overpass + in-memory repo)."""
    settings = settings or get_settings()
    return DiscoveryService(
        geocoder=NominatimGeocoder(settings),
        source=OverpassSource(settings),
        repository=repository or InMemoryBusinessRepository(),
        settings=settings,
    )
