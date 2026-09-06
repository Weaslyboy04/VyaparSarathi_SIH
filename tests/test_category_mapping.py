"""OSM tag <-> internal taxonomy mapping (CLAUDE.md §8)."""

from __future__ import annotations

import pytest

from vyaparsarathi.categories.osm_map import OSM_TAG_TO_CATEGORY, map_osm_tags
from vyaparsarathi.categories.osm_query_tags import OSM_QUERY_SELECTORS, selectors_for
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.models.taxonomy import categories_compatible


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ({"shop": "convenience"}, C.GROCERY),
        ({"shop": "supermarket"}, C.GROCERY),
        ({"shop": "grocer"}, C.GROCERY),
        ({"shop": "general"}, C.GENERAL_STORE),
        ({"shop": "kiosk"}, C.GENERAL_STORE),
        ({"shop": "dairy"}, C.DAIRY),
        ({"amenity": "pharmacy"}, C.PHARMACY),
        ({"shop": "chemist"}, C.PHARMACY),
        ({"craft": "tailor"}, C.TAILORING),
        ({"shop": "tailor"}, C.TAILORING),
        ({"amenity": "restaurant"}, C.RESTAURANT),
        ({"amenity": "fast_food"}, C.FOOD_STALL),
        ({"shop": "hardware"}, C.HARDWARE),
        ({"shop": "agrarian"}, C.AGRI_INPUT),
        ({"amenity": "veterinary"}, C.LIVESTOCK_SERVICES),
        ({"shop": "mobile_phone"}, C.MOBILE_ELECTRONICS),
        ({"shop": "hairdresser"}, C.SALON),
        ({"shop": "stationery"}, C.STATIONERY),
        ({"shop": "furniture"}, C.FURNITURE),
    ],
)
def test_known_mappings(tags: dict[str, str], expected: C) -> None:
    category, matched = map_osm_tags(tags)
    assert category is expected
    assert matched == f"{next(iter(tags))}={next(iter(tags.values()))}"


def test_shop_tag_wins_over_amenity() -> None:
    # priority order: shop before amenity
    category, matched = map_osm_tags({"amenity": "cafe", "shop": "convenience"})
    assert category is C.GROCERY
    assert matched == "shop=convenience"


def test_unmapped_value_becomes_unknown_with_hint() -> None:
    category, matched = map_osm_tags({"shop": "e-cigarette", "name": "Vape Point"})
    assert category is C.UNKNOWN
    assert matched == "shop=e-cigarette"  # hint retained for logging


def test_no_classifying_tag_returns_unknown_none() -> None:
    category, matched = map_osm_tags({"name": "Something", "opening_hours": "24/7"})
    assert category is C.UNKNOWN
    assert matched is None


def test_every_mapping_value_is_a_real_category() -> None:
    for value in OSM_TAG_TO_CATEGORY.values():
        assert isinstance(value, C)


def test_selectors_for_grocery_are_conservative() -> None:
    selectors = selectors_for(C.GROCERY)
    assert ("shop", "convenience") in selectors
    assert ("shop", "supermarket") in selectors
    assert ("shop", "general") in selectors
    # not every food-related tag: restaurants / bakeries are not grocery
    assert ("amenity", "restaurant") not in selectors
    assert ("shop", "bakery") not in selectors


def test_selectors_for_unknown_category_is_empty() -> None:
    assert selectors_for(C.UNKNOWN) == []


def test_all_query_selectors_round_trip_or_are_documented() -> None:
    # Every selector we query for should map back to *some* category (possibly a
    # compatible one), never to a nonsense classification.
    for category, selectors in OSM_QUERY_SELECTORS.items():
        for key, value in selectors:
            mapped, _ = map_osm_tags({key: value})
            assert mapped is not C.UNKNOWN, f"{key}={value} queried for {category} but unmapped"


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        (C.GROCERY, C.GROCERY, True),
        (C.GROCERY, C.GENERAL_STORE, True),
        (C.GROCERY, C.DAIRY, True),
        (C.RESTAURANT, C.FOOD_STALL, True),
        (C.GROCERY, C.PHARMACY, False),
        (C.TAILORING, C.HARDWARE, False),
        (C.UNKNOWN, C.PHARMACY, True),  # unknown never blocks an otherwise strong merge
    ],
)
def test_category_compatibility(a: C, b: C, expected: bool) -> None:
    assert categories_compatible(a, b) is expected
    assert categories_compatible(b, a) is expected  # symmetric
