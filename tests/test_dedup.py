"""Conservative deduplication (CLAUDE.md §9)."""

from __future__ import annotations

from vyaparsarathi.config import Settings
from vyaparsarathi.dedup import Deduplicator
from vyaparsarathi.models.business import NormalizedBusiness, ProvenanceEntry
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.normalization.text import normalize_name

from .conftest import FROZEN_NOW


def _biz(
    name: str,
    lat: float,
    lon: float,
    source_id: str,
    category: BusinessCategory = BusinessCategory.GROCERY,
    quality: float = 0.8,
) -> NormalizedBusiness:
    return NormalizedBusiness(
        name=name or None,
        normalized_name=normalize_name(name),
        category=category,
        latitude=lat,
        longitude=lon,
        source=SourceName.OSM,
        source_id=source_id,
        data_quality=quality,
        first_seen=FROZEN_NOW,
        last_updated=FROZEN_NOW,
        provenance=[
            ProvenanceEntry(source=SourceName.OSM, source_id=source_id, retrieved_at=FROZEN_NOW)
        ],
    )


def _dedup() -> Deduplicator:
    return Deduplicator(Settings(cache_enabled=False))


def test_obvious_same_business_is_merged() -> None:
    a = _biz("Maa Vaishno General Store", 25.75000, 84.55000, "node/1")
    b = _biz("Maa Vaishno General Store", 25.75030, 84.55020, "node/2")  # ~35 m
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 1
    assert result.merged_count == 1
    merged = result.businesses[0]
    # provenance from both observations retained
    assert {p.source_id for p in merged.provenance} == {"node/1", "node/2"}


def test_same_name_far_apart_not_merged() -> None:
    a = _biz("Sharma Kirana", 25.7500, 84.5500, "node/1")
    b = _biz("Sharma Kirana", 25.7800, 84.5900, "node/2")  # kilometres away
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 2
    assert result.merged_count == 0


def test_nearby_different_names_not_merged() -> None:
    a = _biz("Sharma Kirana", 25.75000, 84.55000, "node/1")
    b = _biz("Verma Provision Store", 25.75010, 84.55010, "node/2")
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 2


def test_compatible_categories_can_merge() -> None:
    a = _biz("Sharma Store", 25.75000, 84.55000, "node/1", category=BusinessCategory.GROCERY)
    b = _biz("Sharma Store", 25.75020, 84.55000, "node/2", category=BusinessCategory.GENERAL_STORE)
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 1


def test_incompatible_categories_block_merge() -> None:
    a = _biz("City Center", 25.75000, 84.55000, "node/1", category=BusinessCategory.GROCERY)
    b = _biz("City Center", 25.75020, 84.55000, "node/2", category=BusinessCategory.PHARMACY)
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 2


def test_unnamed_records_are_never_merged_on_geometry_alone() -> None:
    a = _biz("", 25.75000, 84.55000, "node/1")
    b = _biz("", 25.75001, 84.55001, "node/2")  # ~1.5 m apart
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 2


def test_suggestive_pair_is_flagged_uncertain_not_merged() -> None:
    # ~160 m apart: beyond the 120 m merge band, inside the 200 m uncertain band.
    a = _biz("Bhagwanpur Bazaar", 25.75000, 84.55000, "node/1")
    b = _biz("Bhagwanpur Bazar", 25.75144, 84.55000, "node/2")
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 2
    assert len(result.uncertain_pairs) == 1
    assert {result.uncertain_pairs[0].a_source_id, result.uncertain_pairs[0].b_source_id} == {
        "node/1",
        "node/2",
    }


def test_no_transitive_chain_merging() -> None:
    # A~B (close) and B~C (close) but A and C are ~180 m apart -> {A,B} and {C}.
    a = _biz("Shree Ganesh Store", 25.750000, 84.550000, "node/A")
    b = _biz("Shree Ganesh Store", 25.750450, 84.550000, "node/B")  # ~50 m from A
    c = _biz("Shree Ganesh Store", 25.751350, 84.550000, "node/C")  # ~100 m from B, ~150 m from A
    # widen so A-B and B-C individually merge but A-C does not
    result = _dedup().dedupe([a, b, c])
    sizes = sorted(len(p.provenance) for p in result.businesses)
    assert result.businesses  # something survived
    assert sizes == [1, 2]  # a cluster of 2 and a singleton, never a 3-cluster
    assert len(result.businesses) == 2


def test_merge_decisions_are_recorded_and_reversible() -> None:
    a = _biz("Maa Vaishno General Store", 25.75000, 84.55000, "node/1")
    b = _biz("Maa Vaishno General Store", 25.75030, 84.55020, "node/2")
    result = _dedup().dedupe([a, b])
    assert len(result.merges) == 1
    decision = result.merges[0]
    assert decision.absorbed_source_id == "node/2"
    assert decision.kept_source_id == "node/1"
    assert decision.name_similarity >= 87
    assert decision.distance_m <= 120
    assert "compatible" in decision.reason


def test_merged_quality_gets_multi_source_bonus() -> None:
    a = _biz("Kirana Point", 25.75000, 84.55000, "node/1", quality=0.7)
    b = NormalizedBusiness(
        name="Kirana Point",
        normalized_name="kirana point",
        category=BusinessCategory.GROCERY,
        latitude=25.75020,
        longitude=84.55000,
        source=SourceName.GOOGLE_PLACES,
        source_id="place/xyz",
        data_quality=0.7,
        first_seen=FROZEN_NOW,
        last_updated=FROZEN_NOW,
        provenance=[
            ProvenanceEntry(
                source=SourceName.GOOGLE_PLACES, source_id="place/xyz", retrieved_at=FROZEN_NOW
            )
        ],
    )
    result = _dedup().dedupe([a, b])
    assert len(result.businesses) == 1
    assert result.businesses[0].data_quality > 0.7  # corroboration bonus


def test_empty_and_single_inputs() -> None:
    assert _dedup().dedupe([]).businesses == []
    one = _biz("Solo Shop", 25.75, 84.55, "node/1")
    out = _dedup().dedupe([one])
    assert len(out.businesses) == 1
    assert out.merged_count == 0
