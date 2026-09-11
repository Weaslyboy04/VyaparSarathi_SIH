"""Internal business taxonomy and source enum (CLAUDE.md §7, §8).

The taxonomy is deliberately independent of any external source's vocabulary.
Per-source tag mappings live in ``vyaparsarathi.categories`` and are the only
place that knows OSM / Google / NIC terms. Values are stored as strings, so new
categories can be added without breaking persisted data.
"""

from __future__ import annotations

from enum import StrEnum


class BusinessCategory(StrEnum):
    GROCERY = "grocery"
    DAIRY = "dairy"
    PHARMACY = "pharmacy"
    RESTAURANT = "restaurant"
    FOOD_STALL = "food_stall"
    HARDWARE = "hardware"
    CLOTHING = "clothing"
    MOBILE_ELECTRONICS = "mobile_electronics"
    AUTOMOBILE_REPAIR = "automobile_repair"
    TAILORING = "tailoring"
    AGRI_INPUT = "agri_input"
    FOOD_PROCESSING = "food_processing"
    LIVESTOCK_SERVICES = "livestock_services"
    GENERAL_STORE = "general_store"
    SALON = "salon"
    STATIONERY = "stationery"
    FURNITURE = "furniture"
    BUILDING_MATERIALS = "building_materials"
    SPORTS_GOODS = "sports_goods"
    GYM_FITNESS = "gym_fitness"
    PRINTING_XEROX = "printing_xerox"
    COMPUTER_SERVICES = "computer_services"
    WELDING_FABRICATION = "welding_fabrication"
    CATERING = "catering"
    EVENT_SERVICES = "event_services"
    UTILITY_AGENCY = "utility_agency"
    EDUCATION_SERVICES = "education_services"
    LAUNDRY = "laundry"
    CYCLE_REPAIR = "cycle_repair"
    FOOTWEAR = "footwear"
    # Generic fallback for a real, classified small trade/service this
    # taxonomy has no tailored data for. Distinct from UNKNOWN (= "not yet
    # classified") — OTHER_TRADE still enters scoring/discovery, just with
    # a degraded (no `asset_relevance`/`capital_bands` row), clearly-labelled
    # confidence rather than being dropped from the report entirely.
    OTHER_TRADE = "other_trade"
    UNKNOWN = "unknown"


class SourceName(StrEnum):
    """Data-source identifiers (CLAUDE.md §7).

    Phase 1 only produces ``OSM``. ``licensed:<name>`` / ``crawl:<name>`` style
    values are added as string members when those adapters land.
    """

    OSM = "osm"
    GOOGLE_PLACES = "google_places"
    GOVT = "govt"


# Groups of categories that may refer to the same real shop for dedup purposes
# (CLAUDE.md §9). Membership is symmetric and non-transitive across groups.
CATEGORY_COMPATIBILITY: tuple[frozenset[BusinessCategory], ...] = (
    frozenset({BusinessCategory.GROCERY, BusinessCategory.GENERAL_STORE, BusinessCategory.DAIRY}),
    frozenset({BusinessCategory.RESTAURANT, BusinessCategory.FOOD_STALL}),
    frozenset({BusinessCategory.HARDWARE, BusinessCategory.BUILDING_MATERIALS}),
    frozenset({BusinessCategory.AGRI_INPUT, BusinessCategory.LIVESTOCK_SERVICES}),
    frozenset({BusinessCategory.MOBILE_ELECTRONICS, BusinessCategory.STATIONERY}),
)


def categories_compatible(a: BusinessCategory, b: BusinessCategory) -> bool:
    """True when two categories are equal or share a compatibility group.

    ``UNKNOWN`` is treated as compatible with anything: a record we could not
    classify should not block an otherwise strong merge (CLAUDE.md §9 — a missed
    merge only slightly inflates counts, a false merge destroys a competitor,
    but name + distance still have to agree).
    """
    if a == b:
        return True
    if BusinessCategory.UNKNOWN in (a, b):
        return True
    return any(a in group and b in group for group in CATEGORY_COMPATIBILITY)
