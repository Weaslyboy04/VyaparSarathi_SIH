"""Internal :class:`BusinessCategory` -> OSM tag selectors to query (CLAUDE.md §8, §26.1 STEP 6).

We do not fetch every POI in the radius. For the requested category the discovery
service asks Overpass only for the tag combinations below. Each entry is an
``(key, value)`` pair rendered as ``nwr["key"="value"](around:...)``.

Keep this list explicit and conservative — do not equate every food-related tag
with ``grocery`` (CLAUDE.md STEP 6). Adjacent-but-distinct tags (e.g.
``shop=general`` for a ``grocery`` query) are included on purpose: they are the
substitutes a rural buyer actually uses, and category-compatibility in dedup /
Phase 2 competitor filtering handles the overlap.
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.models.taxonomy import BusinessCategory as C

OSM_QUERY_SELECTORS: dict[C, list[tuple[str, str]]] = {
    C.GROCERY: [
        ("shop", "convenience"),
        ("shop", "supermarket"),
        ("shop", "grocer"),
        ("shop", "greengrocer"),
        ("shop", "general"),  # village mixed-goods shop; a real grocery substitute
    ],
    C.GENERAL_STORE: [
        ("shop", "general"),
        ("shop", "kiosk"),
        ("shop", "variety_store"),
        ("shop", "department_store"),
        ("shop", "convenience"),
    ],
    C.DAIRY: [
        ("shop", "dairy"),
        ("shop", "cheese"),
    ],
    C.PHARMACY: [
        ("amenity", "pharmacy"),
        ("shop", "chemist"),
        ("healthcare", "pharmacy"),
    ],
    C.RESTAURANT: [
        ("amenity", "restaurant"),
        ("amenity", "cafe"),
        ("amenity", "food_court"),
    ],
    C.FOOD_STALL: [
        ("amenity", "fast_food"),
        ("amenity", "ice_cream"),
        ("shop", "bakery"),
    ],
    C.HARDWARE: [
        ("shop", "hardware"),
        ("shop", "doityourself"),
    ],
    C.BUILDING_MATERIALS: [
        ("shop", "trade"),
        ("shop", "building_materials"),
        ("shop", "paint"),
    ],
    C.CLOTHING: [
        ("shop", "clothes"),
        ("shop", "fashion"),
        ("shop", "boutique"),
        ("shop", "fabric"),
        ("shop", "sari"),
        ("shop", "shoes"),
    ],
    C.MOBILE_ELECTRONICS: [
        ("shop", "mobile_phone"),
        ("shop", "electronics"),
        ("shop", "computer"),
    ],
    C.AUTOMOBILE_REPAIR: [
        ("shop", "car_repair"),
        ("shop", "motorcycle_repair"),
        ("shop", "tyres"),
        ("shop", "car_parts"),
    ],
    C.TAILORING: [
        ("craft", "tailor"),
        ("shop", "tailor"),
        ("craft", "dressmaker"),
    ],
    C.AGRI_INPUT: [
        ("shop", "agrarian"),
        ("shop", "farm"),
        ("shop", "garden_centre"),
    ],
    C.LIVESTOCK_SERVICES: [
        ("shop", "animal_feed"),
        ("amenity", "veterinary"),
        ("shop", "pet"),
    ],
    C.FOOD_PROCESSING: [
        ("craft", "oil_mill"),
        ("craft", "grinding_mill"),
        ("man_made", "flour_mill"),
        ("craft", "confectionery"),
    ],
    C.SALON: [
        ("shop", "hairdresser"),
        ("shop", "beauty"),
        ("shop", "barber"),
    ],
    C.STATIONERY: [
        ("shop", "stationery"),
        ("shop", "books"),
        ("shop", "newsagent"),
        ("shop", "copyshop"),
    ],
    C.FURNITURE: [
        ("shop", "furniture"),
        ("shop", "bed"),
        ("craft", "carpenter"),
    ],
}


def selectors_for(category: C) -> list[tuple[str, str]]:
    """Tag selectors for a category. Empty list => not queryable in Phase 1."""
    return OSM_QUERY_SELECTORS.get(category, [])


def selectors_for_many(categories: Iterable[C]) -> list[tuple[str, str]]:
    """The union of :func:`selectors_for` over ``categories``, order-stable and
    de-duplicated.

    Used by Phase 3 to cover several candidate categories in **one** Overpass
    union query. Selectors keep first-seen order: category order, then the order
    within each category's list. ``selectors_for_many([X])`` is exactly
    ``selectors_for(X)``, so widening the fetch never changes a single-category
    run. Categories with no mapping contribute nothing (they are simply skipped).
    """
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for category in categories:
        for selector in OSM_QUERY_SELECTORS.get(category, []):
            if selector not in seen:
                seen.add(selector)
                out.append(selector)
    return out
