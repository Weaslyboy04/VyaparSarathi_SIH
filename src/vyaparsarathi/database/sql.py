"""SQLAlchemy-backed repository (CLAUDE.md §26 STEP 11)."""

from __future__ import annotations

import math
from uuid import UUID

from sqlalchemy import Engine, create_engine, delete, func, select
from sqlalchemy.orm import Session

from vyaparsarathi.config import get_settings
from vyaparsarathi.database.schema import Base, BusinessRow, SourceRecordRow
from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.utils.geo import haversine_m
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_METRES_PER_DEG_LAT = 111_320.0


def _row_to_business(row: BusinessRow) -> NormalizedBusiness:
    return NormalizedBusiness(
        internal_id=UUID(row.internal_id),
        name=row.name,
        normalized_name=row.normalized_name,
        category=BusinessCategory(row.category),
        latitude=row.latitude,
        longitude=row.longitude,
        address=row.address,
        source=SourceName(row.primary_source),
        source_id=row.primary_source_id,
        raw=row.raw or {},
        first_seen=row.first_seen,
        last_updated=row.last_updated,
        data_quality=row.data_quality,
        provenance=[
            ProvenanceEntry(
                source=SourceName(sr.source),
                source_id=sr.source_id,
                retrieved_at=sr.retrieved_at,
            )
            for sr in sorted(row.source_records, key=lambda s: (s.source, s.source_id))
        ],
    )


class SqlBusinessRepository:
    def __init__(self, engine: Engine, create_schema: bool = True) -> None:
        self._engine = engine
        if create_schema:
            Base.metadata.create_all(engine)

    # -- writes -----------------------------------------------------------

    def save_business(self, business: NormalizedBusiness) -> None:
        self.save_businesses([business])

    def save_businesses(self, businesses: list[NormalizedBusiness]) -> None:
        with Session(self._engine) as session, session.begin():
            for business in businesses:
                self._upsert(session, business)

    def _upsert(self, session: Session, business: NormalizedBusiness) -> None:
        key = str(business.internal_id)
        row = session.get(BusinessRow, key)
        if row is None:
            row = BusinessRow(internal_id=key)
            session.add(row)

        row.name = business.name
        row.normalized_name = business.normalized_name
        row.category = business.category.value
        row.latitude = business.latitude
        row.longitude = business.longitude
        row.address = business.address
        row.primary_source = business.source.value
        row.primary_source_id = business.source_id
        row.raw = business.raw or {}
        row.first_seen = business.first_seen
        row.last_updated = business.last_updated
        row.data_quality = business.data_quality

        entries = list(business.provenance) or [
            ProvenanceEntry(
                source=business.source,
                source_id=business.source_id,
                retrieved_at=business.last_updated,
            )
        ]
        # Rebuild this business's source records; reassign any owned by another
        # row (a merge absorbed them).
        session.flush()
        session.execute(delete(SourceRecordRow).where(SourceRecordRow.business_internal_id == key))
        donor_ids: set[str] = set()
        for entry in entries:
            existing = session.scalar(
                select(SourceRecordRow).where(
                    SourceRecordRow.source == entry.source.value,
                    SourceRecordRow.source_id == entry.source_id,
                )
            )
            if existing is not None:
                if existing.business_internal_id != key:
                    donor_ids.add(existing.business_internal_id)
                existing.business_internal_id = key
                existing.retrieved_at = entry.retrieved_at
            else:
                session.add(
                    SourceRecordRow(
                        business_internal_id=key,
                        source=entry.source.value,
                        source_id=entry.source_id,
                        retrieved_at=entry.retrieved_at,
                    )
                )

        # A merge may have taken the last source record from another business
        # row (e.g. re-running discovery). Drop rows left with no observations.
        session.flush()
        for donor_id in donor_ids - {key}:
            remaining = session.scalar(
                select(func.count())
                .select_from(SourceRecordRow)
                .where(SourceRecordRow.business_internal_id == donor_id)
            )
            if not remaining:
                donor = session.get(BusinessRow, donor_id)
                if donor is not None:
                    session.delete(donor)

    # -- reads ----------------------------------------------------------

    def get_by_internal_id(self, internal_id: UUID) -> NormalizedBusiness | None:
        with Session(self._engine) as session:
            row = session.get(BusinessRow, str(internal_id))
            return _row_to_business(row) if row is not None else None

    def businesses_by_category(self, category: BusinessCategory) -> list[NormalizedBusiness]:
        with Session(self._engine) as session:
            rows = session.scalars(
                select(BusinessRow).where(BusinessRow.category == category.value)
            ).all()
            return [_row_to_business(r) for r in rows]

    def businesses_near(
        self,
        latitude: float,
        longitude: float,
        radius_m: float,
        category: BusinessCategory | None = None,
    ) -> list[BusinessHit]:
        # Bounding-box prefilter in SQL, exact haversine filter in Python.
        d_lat = radius_m / _METRES_PER_DEG_LAT
        cos_lat = max(math.cos(math.radians(latitude)), 1e-6)
        d_lon = radius_m / (_METRES_PER_DEG_LAT * cos_lat)

        stmt = select(BusinessRow).where(
            BusinessRow.latitude.between(latitude - d_lat, latitude + d_lat),
            BusinessRow.longitude.between(longitude - d_lon, longitude + d_lon),
        )
        if category is not None:
            stmt = stmt.where(BusinessRow.category == category.value)

        hits: list[BusinessHit] = []
        with Session(self._engine) as session:
            for row in session.scalars(stmt):
                distance = haversine_m(latitude, longitude, row.latitude, row.longitude)
                if distance <= radius_m:
                    hits.append(BusinessHit(business=_row_to_business(row), distance_m=distance))
        hits.sort(key=lambda h: h.distance_m)
        return hits

    def count(self) -> int:
        with Session(self._engine) as session:
            return int(session.scalar(select(func.count()).select_from(BusinessRow)) or 0)


def create_repository(
    db_url: str | None = None, create_schema: bool = True
) -> SqlBusinessRepository:
    """Build a :class:`SqlBusinessRepository` from a URL (default: settings)."""
    url = db_url or get_settings().db_url
    engine = create_engine(url, future=True)
    logger.info("using SQL repository at %s", engine.url.render_as_string(hide_password=True))
    return SqlBusinessRepository(engine, create_schema=create_schema)
