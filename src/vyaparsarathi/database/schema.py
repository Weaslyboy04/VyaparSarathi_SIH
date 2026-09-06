"""SQLAlchemy 2.x ORM schema (CLAUDE.md §26 STEP 12).

Two tables, deliberately separated:

* ``businesses`` — the normalized (possibly merged) business entity.
* ``source_records`` — one row per source observation ``(source, source_id)``,
  pointing at its business. Merges therefore stay reversible and a merged
  business retains every contributing observation.

No tables for market scoring, finance, RAG, etc. (out of scope, CLAUDE.md §26.3).
Spatial columns are plain floats with a btree index for the bounding-box
prefilter; a PostGIS geometry column + GiST index is a later drop-in.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class BusinessRow(Base):
    __tablename__ = "businesses"

    internal_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    normalized_name: Mapped[str] = mapped_column(String(512), default="", index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    address: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    primary_source: Mapped[str] = mapped_column(String(64))
    primary_source_id: Mapped[str] = mapped_column(String(128))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data_quality: Mapped[float] = mapped_column(Float, default=0.0)

    source_records: Mapped[list[SourceRecordRow]] = relationship(
        back_populates="business", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (Index("ix_businesses_lat_lon", "latitude", "longitude"),)


class SourceRecordRow(Base):
    __tablename__ = "source_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    business_internal_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("businesses.internal_id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(128))
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    business: Mapped[BusinessRow] = relationship(back_populates="source_records")

    __table_args__ = (
        UniqueConstraint("source", "source_id", name="uq_source_records_source_sourceid"),
    )
