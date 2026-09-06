"""Persistence: in-memory and SQLAlchemy repositories (CLAUDE.md §10, STEP 11-12)."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine

from vyaparsarathi.database import InMemoryBusinessRepository, SqlBusinessRepository
from vyaparsarathi.models.business import NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName

from .conftest import FROZEN_NOW


def _biz(
    source_id: str,
    lat: float,
    lon: float,
    name: str = "Shop",
    category: BusinessCategory = BusinessCategory.GROCERY,
    provenance_ids: list[str] | None = None,
) -> NormalizedBusiness:
    ids = provenance_ids or [source_id]
    return NormalizedBusiness(
        name=name,
        normalized_name=name.lower(),
        category=category,
        latitude=lat,
        longitude=lon,
        address="Main Road, Bhagwanpur",
        source=SourceName.OSM,
        source_id=source_id,
        first_seen=FROZEN_NOW,
        last_updated=FROZEN_NOW,
        data_quality=0.8,
        provenance=[
            ProvenanceEntry(source=SourceName.OSM, source_id=i, retrieved_at=FROZEN_NOW)
            for i in ids
        ],
    )


@pytest.fixture(params=["memory", "sql"])
def repo(request: pytest.FixtureRequest):  # noqa: ANN201
    if request.param == "memory":
        return InMemoryBusinessRepository()
    engine = create_engine("sqlite://", future=True)  # in-memory SQLite
    return SqlBusinessRepository(engine, create_schema=True)


def test_save_and_get_by_internal_id(repo) -> None:  # noqa: ANN001
    b = _biz("node/1", 25.75, 84.55)
    repo.save_business(b)
    got = repo.get_by_internal_id(b.internal_id)
    assert got is not None
    assert got.internal_id == b.internal_id
    assert got.name == "Shop"
    assert got.source_id == "node/1"
    assert {p.source_id for p in got.provenance} == {"node/1"}


def test_count_and_upsert(repo) -> None:  # noqa: ANN001
    b = _biz("node/1", 25.75, 84.55, name="First")
    repo.save_business(b)
    assert repo.count() == 1
    updated = b.model_copy(update={"name": "Renamed"})
    repo.save_business(updated)
    assert repo.count() == 1
    assert repo.get_by_internal_id(b.internal_id).name == "Renamed"


def test_businesses_near_filters_and_sorts_by_distance(repo) -> None:  # noqa: ANN001
    near = _biz("node/1", 25.7501, 84.5501, name="Near")
    mid = _biz("node/2", 25.7600, 84.5600, name="Mid")
    far = _biz("node/3", 26.5000, 85.5000, name="Far")
    repo.save_businesses([far, near, mid])

    hits = repo.businesses_near(25.7500, 84.5500, radius_m=5_000)
    names = [h.business.name for h in hits]
    assert names == ["Near", "Mid"]  # far excluded, sorted nearest-first
    assert hits[0].distance_m < hits[1].distance_m


def test_businesses_near_category_filter(repo) -> None:  # noqa: ANN001
    grocery = _biz("node/1", 25.7501, 84.5501, category=BusinessCategory.GROCERY)
    pharmacy = _biz("node/2", 25.7502, 84.5502, category=BusinessCategory.PHARMACY)
    repo.save_businesses([grocery, pharmacy])

    hits = repo.businesses_near(25.75, 84.55, radius_m=5_000, category=BusinessCategory.PHARMACY)
    assert [h.business.category for h in hits] == [BusinessCategory.PHARMACY]


def test_by_category(repo) -> None:  # noqa: ANN001
    repo.save_businesses(
        [
            _biz("node/1", 25.75, 84.55, category=BusinessCategory.GROCERY),
            _biz("node/2", 25.76, 84.56, category=BusinessCategory.GROCERY),
            _biz("node/3", 25.77, 84.57, category=BusinessCategory.DAIRY),
        ]
    )
    assert len(repo.businesses_by_category(BusinessCategory.GROCERY)) == 2
    assert len(repo.businesses_by_category(BusinessCategory.DAIRY)) == 1


def test_merged_business_keeps_multiple_source_records(repo) -> None:  # noqa: ANN001
    merged = _biz("node/1", 25.75, 84.55, provenance_ids=["node/1", "node/2"])
    repo.save_business(merged)
    got = repo.get_by_internal_id(merged.internal_id)
    assert {p.source_id for p in got.provenance} == {"node/1", "node/2"}


def test_sql_source_record_reassigned_on_merge() -> None:
    """A later merge that absorbs an existing source_id must move it, not duplicate."""
    engine = create_engine("sqlite://", future=True)
    repo = SqlBusinessRepository(engine, create_schema=True)

    standalone = _biz("node/2", 25.7503, 84.5503, name="Standalone")
    repo.save_business(standalone)
    assert repo.count() == 1

    merged = _biz("node/1", 25.75, 84.55, name="Merged", provenance_ids=["node/1", "node/2"])
    repo.save_business(merged)

    # node/2's observation now belongs to the merged record; the now-empty
    # standalone row is gone.
    assert repo.count() == 1
    got = repo.get_by_internal_id(merged.internal_id)
    assert {p.source_id for p in got.provenance} == {"node/1", "node/2"}
    # and the source_records table still has exactly one row for node/2
    from sqlalchemy import func, select

    from vyaparsarathi.database.schema import SourceRecordRow

    with engine.connect() as conn:
        n = conn.scalar(
            select(func.count())
            .select_from(SourceRecordRow)
            .where(SourceRecordRow.source_id == "node/2")
        )
    assert n == 1
