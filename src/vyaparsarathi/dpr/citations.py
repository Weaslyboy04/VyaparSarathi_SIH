"""Build the DPR's citation table from already-retrieved evidence (CLAUDE.md
§19, §23). PURE — copies fields off existing result objects; retrieves
nothing.
"""

from __future__ import annotations

from vyaparsarathi.dpr.artifacts import ArtifactSet
from vyaparsarathi.dpr.report_models import Citation
from vyaparsarathi.models.parameters import ResolutionStatus
from vyaparsarathi.models.results import DiscoveryStatus


def _dt(value: object) -> str:
    return "" if value is None else str(value)[:10]


def build_citations(arts: ArtifactSet) -> dict[str, Citation]:
    """Ordered, stable citation ids: ``KB*`` for a resolved scheme parameter,
    ``P*`` for a retrieved passage, ``DS`` for the business-discovery source,
    ``CEN`` for the Census population extract."""
    out: dict[str, Citation] = {}

    if arts.knowledge is not None:
        n = 0
        for res in arts.knowledge.resolutions:
            if res.status is ResolutionStatus.RESOLVED and res.chosen is not None:
                n += 1
                cid = f"KB{n}"
                chosen = res.chosen
                out[cid] = Citation(
                    citation_id=cid,
                    text=(
                        res.citation
                        or f"{res.name.value} = {chosen.value_token} "
                        f"(doc {chosen.document_id}, {chosen.locator.as_ref()})"
                    ),
                    document_title=chosen.document_id,
                    publisher="",
                    tier=chosen.tier.value,
                    locator=chosen.locator.as_ref(),
                    reference_date=_dt(chosen.reference_date),
                    retrieved_at=_dt(res.retrieved_at),
                )
        for i, passage in enumerate(arts.knowledge.passages, start=1):
            cid = f"P{i}"
            chunk = passage.chunk
            out[cid] = Citation(
                citation_id=cid,
                text=passage.citation or f"{chunk.document_id} {chunk.locator.as_ref()}",
                document_title=chunk.document_id,
                tier=passage.tier.value,
                locator=chunk.locator.as_ref(),
            )

    disc = arts.discovery
    if disc is not None and disc.status is DiscoveryStatus.OK and disc.businesses:
        _source_label = {"osm": "OpenStreetMap"}
        srcs = (
            ", ".join(
                _source_label.get(s.value, s.value)
                for s in sorted(disc.sources_queried, key=lambda s: s.value)
            )
            or "OpenStreetMap"
        )
        endpoint = ""
        if disc.coverage.per_source:
            endpoint = disc.coverage.per_source[0].endpoint_used or ""
        out["DS"] = Citation(
            citation_id="DS",
            text=(
                f"Nearby-business sample from {srcs}"
                + (f" via {endpoint}" if endpoint else "")
                + f" — {disc.coverage.total_after_dedup} record(s) after de-duplication. "
                "A partial sample, not the complete set of local businesses."
            ),
            publisher=srcs,
            url=endpoint,
        )

    dem = arts.demand
    if dem is not None and dem.catchment.persons is not None:
        years = ", ".join(str(y) for y in dem.catchment.reference_years) or "2011"
        out["CEN"] = Citation(
            citation_id="CEN",
            text=(
                f"Catchment population from the Census {years} village extract "
                f"(coverage {(dem.catchment.population_coverage or 0):.0%}"
                + (", reported as a floor" if dem.catchment.is_floor else "")
                + ")."
            ),
            publisher="Office of the Registrar General & Census Commissioner, India",
            reference_date=years.split(",")[0].strip(),
        )

    return out


__all__ = ["build_citations"]
