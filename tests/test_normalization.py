"""Raw OSM element -> NormalizedBusiness (CLAUDE.md §7)."""

from __future__ import annotations

import pytest

from vyaparsarathi.errors import NormalizationError
from vyaparsarathi.models.taxonomy import BusinessCategory, SourceName
from vyaparsarathi.normalization.business import normalize_osm_element
from vyaparsarathi.sources.osm.models import RawOsmElement

from .conftest import FROZEN_NOW


def _node(**tags: str) -> RawOsmElement:
    return RawOsmElement(
        element_type="node",
        element_id=42,
        latitude=25.75,
        longitude=84.55,
        tags=tags,
        raw={"type": "node", "id": 42, "tags": tags},
    )


def test_basic_fields_and_provenance() -> None:
    element = _node(shop="convenience", name="Sharma Kirana Store")
    result = normalize_osm_element(element, now=FROZEN_NOW)
    b = result.business

    assert b.name == "Sharma Kirana Store"
    assert b.normalized_name == "sharma kirana store"
    assert b.category is BusinessCategory.GROCERY
    assert (b.latitude, b.longitude) == (25.75, 84.55)
    assert b.source is SourceName.OSM
    assert b.source_id == "node/42"
    assert b.first_seen == FROZEN_NOW == b.last_updated
    assert b.raw == element.raw
    assert len(b.provenance) == 1
    assert b.provenance[0].source is SourceName.OSM
    assert b.provenance[0].source_id == "node/42"
    assert b.provenance[0].retrieved_at == FROZEN_NOW
    assert result.unmapped_tag is None


def test_name_falls_back_through_tag_priority() -> None:
    assert normalize_osm_element(_node(shop="convenience", operator="R. Sharma")).business.name == (
        "R. Sharma"
    )
    assert normalize_osm_element(_node(shop="convenience")).business.name is None


def test_address_assembled_from_addr_parts() -> None:
    element = _node(
        shop="convenience",
        name="X",
        **{"addr:housenumber": "12", "addr:street": "Main Road", "addr:village": "Bhagwanpur"},
    )
    assert normalize_osm_element(element).business.address == "12, Main Road, Bhagwanpur"


def test_addr_full_preferred() -> None:
    element = _node(shop="grocer", name="X", **{"addr:full": "Near Bus Stand, Bhagwanpur"})
    assert normalize_osm_element(element).business.address == "Near Bus Stand, Bhagwanpur"


def test_unmapped_tag_is_reported_not_dropped() -> None:
    result = normalize_osm_element(_node(shop="e-cigarette", name="Vape Point"))
    assert result.business.category is BusinessCategory.UNKNOWN
    assert result.unmapped_tag == "shop=e-cigarette"


def test_data_quality_rewards_name_category_address() -> None:
    rich = normalize_osm_element(
        _node(shop="convenience", name="Full Shop", **{"addr:street": "Main Road"})
    ).business
    poor = normalize_osm_element(_node(shop="e-cigarette")).business  # no name, unknown, no address
    assert rich.data_quality > poor.data_quality
    assert 0.0 <= poor.data_quality <= 1.0
    assert 0.0 <= rich.data_quality <= 1.0


def test_way_centroid_scores_slightly_lower_than_node() -> None:
    tags = {"shop": "convenience", "name": "Corner Shop"}
    node = RawOsmElement(
        element_type="node", element_id=1, latitude=25.75, longitude=84.55, tags=tags, raw={}
    )
    way = RawOsmElement(
        element_type="way", element_id=2, latitude=25.75, longitude=84.55, tags=tags, raw={}
    )
    assert (
        normalize_osm_element(node).business.data_quality
        > normalize_osm_element(way).business.data_quality
    )


def test_missing_coordinates_raises() -> None:
    element = RawOsmElement(
        element_type="node",
        element_id=9,
        latitude=None,
        longitude=None,
        tags={"shop": "convenience"},
    )
    with pytest.raises(NormalizationError):
        normalize_osm_element(element)


def test_raw_payload_is_preserved_verbatim() -> None:
    raw = {"type": "node", "id": 42, "tags": {"shop": "convenience"}, "extra": [1, 2, 3]}
    element = RawOsmElement(
        element_type="node",
        element_id=42,
        latitude=25.75,
        longitude=84.55,
        tags={"shop": "convenience"},
        raw=raw,
    )
    assert normalize_osm_element(element).business.raw == raw
