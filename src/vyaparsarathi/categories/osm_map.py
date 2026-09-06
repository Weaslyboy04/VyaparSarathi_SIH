"""OSM tags -> internal :class:`BusinessCategory` (CLAUDE.md §8).

Only this module knows OSM's vocabulary. An element whose tags match nothing here
is classified ``UNKNOWN`` and the most informative raw tag is reported back to
the caller for logging / taxonomy expansion — it is never silently dropped.

Mapping notes for choices that are not one-to-one:

* ``shop=general`` / ``shop=kiosk`` / ``shop=variety_store`` -> ``general_store``
  (mixed-goods village shop), distinct from ``shop=convenience`` -> ``grocery``.
* ``shop=chemist`` -> ``pharmacy``. In OSM ``shop=chemist`` is toiletries/OTC and
  ``amenity=pharmacy`` is prescription; for a rural advisory both read as
  "medical shop", so both map to ``pharmacy``.
* ``amenity=fuel`` -> ``automobile_repair`` is a deliberate coarsening: a rural
  fuel point is the nearest "vehicle services" proxy we model today. Refine when
  a dedicated fuel/energy category exists.
* ``shop=farm`` -> ``agri_input`` is approximate (farm-gate sales vs input
  supply); revisit with a dedicated produce category.
* ``craft=*`` food producers -> ``food_processing``.
"""

from __future__ import annotations

from vyaparsarathi.models.taxonomy import BusinessCategory as C

# Exact (key, value) -> category. Checked in OSM_KEY_PRIORITY order so that, e.g.,
# a element tagged both shop=* and amenity=* is classified by its shop tag first.
OSM_TAG_TO_CATEGORY: dict[tuple[str, str], C] = {
    # --- grocery / general retail ---
    ("shop", "convenience"): C.GROCERY,
    ("shop", "supermarket"): C.GROCERY,
    ("shop", "grocer"): C.GROCERY,
    ("shop", "greengrocer"): C.GROCERY,
    ("shop", "general"): C.GENERAL_STORE,
    ("shop", "kiosk"): C.GENERAL_STORE,
    ("shop", "variety_store"): C.GENERAL_STORE,
    ("shop", "department_store"): C.GENERAL_STORE,
    # --- dairy ---
    ("shop", "dairy"): C.DAIRY,
    ("shop", "cheese"): C.DAIRY,
    ("amenity", "vending_machine"): C.UNKNOWN,  # too generic; keep explicit
    # --- pharmacy / medical ---
    ("amenity", "pharmacy"): C.PHARMACY,
    ("shop", "chemist"): C.PHARMACY,
    ("healthcare", "pharmacy"): C.PHARMACY,
    # --- food service ---
    ("amenity", "restaurant"): C.RESTAURANT,
    ("amenity", "fast_food"): C.FOOD_STALL,
    ("amenity", "cafe"): C.RESTAURANT,
    ("amenity", "food_court"): C.RESTAURANT,
    ("amenity", "ice_cream"): C.FOOD_STALL,
    ("shop", "bakery"): C.FOOD_STALL,
    ("shop", "pastry"): C.FOOD_STALL,
    ("amenity", "canteen"): C.FOOD_STALL,
    # --- hardware / building ---
    ("shop", "hardware"): C.HARDWARE,
    ("shop", "doityourself"): C.HARDWARE,
    ("shop", "trade"): C.BUILDING_MATERIALS,
    ("shop", "building_materials"): C.BUILDING_MATERIALS,
    ("shop", "paint"): C.BUILDING_MATERIALS,
    ("shop", "hardware_store"): C.HARDWARE,
    # --- clothing / textiles ---
    ("shop", "clothes"): C.CLOTHING,
    ("shop", "fashion"): C.CLOTHING,
    ("shop", "boutique"): C.CLOTHING,
    ("shop", "fabric"): C.CLOTHING,
    ("shop", "sari"): C.CLOTHING,
    ("shop", "shoes"): C.CLOTHING,
    # --- mobile / electronics ---
    ("shop", "mobile_phone"): C.MOBILE_ELECTRONICS,
    ("shop", "electronics"): C.MOBILE_ELECTRONICS,
    ("shop", "computer"): C.MOBILE_ELECTRONICS,
    ("shop", "hifi"): C.MOBILE_ELECTRONICS,
    # --- automobile ---
    ("shop", "car_repair"): C.AUTOMOBILE_REPAIR,
    ("shop", "motorcycle_repair"): C.AUTOMOBILE_REPAIR,
    ("shop", "tyres"): C.AUTOMOBILE_REPAIR,
    ("shop", "car_parts"): C.AUTOMOBILE_REPAIR,
    ("amenity", "fuel"): C.AUTOMOBILE_REPAIR,
    ("shop", "motorcycle"): C.AUTOMOBILE_REPAIR,
    # --- tailoring ---
    ("craft", "tailor"): C.TAILORING,
    ("shop", "tailor"): C.TAILORING,
    ("craft", "dressmaker"): C.TAILORING,
    ("shop", "sewing"): C.TAILORING,
    # --- agri inputs / livestock ---
    ("shop", "agrarian"): C.AGRI_INPUT,
    ("shop", "farm"): C.AGRI_INPUT,
    ("shop", "garden_centre"): C.AGRI_INPUT,
    ("shop", "frozen_food"): C.UNKNOWN,
    ("shop", "pet"): C.LIVESTOCK_SERVICES,
    ("shop", "animal_feed"): C.LIVESTOCK_SERVICES,
    ("amenity", "veterinary"): C.LIVESTOCK_SERVICES,
    ("craft", "agricultural_engines"): C.AGRI_INPUT,
    # --- food processing ---
    ("craft", "bakery"): C.FOOD_PROCESSING,
    ("craft", "confectionery"): C.FOOD_PROCESSING,
    ("craft", "oil_mill"): C.FOOD_PROCESSING,
    ("craft", "grinding_mill"): C.FOOD_PROCESSING,
    ("man_made", "flour_mill"): C.FOOD_PROCESSING,
    # --- salon / personal care ---
    ("shop", "hairdresser"): C.SALON,
    ("shop", "beauty"): C.SALON,
    ("shop", "barber"): C.SALON,
    ("amenity", "spa"): C.SALON,
    # --- stationery / books ---
    ("shop", "stationery"): C.STATIONERY,
    ("shop", "books"): C.STATIONERY,
    ("shop", "newsagent"): C.STATIONERY,
    ("shop", "copyshop"): C.STATIONERY,
    # --- furniture ---
    ("shop", "furniture"): C.FURNITURE,
    ("shop", "bed"): C.FURNITURE,
    ("shop", "interior_decoration"): C.FURNITURE,
    ("craft", "carpenter"): C.FURNITURE,
}

# Which tag keys carry the primary business classification, most specific first.
OSM_KEY_PRIORITY: tuple[str, ...] = (
    "shop",
    "amenity",
    "craft",
    "healthcare",
    "office",
    "man_made",
)

# Keys we look at only to build a human-readable "raw tag" for logging when the
# element is otherwise unmapped.
_DESCRIPTIVE_KEYS: tuple[str, ...] = (*OSM_KEY_PRIORITY, "landuse", "building")


def map_osm_tags(tags: dict[str, str]) -> tuple[C, str | None]:
    """Return ``(category, matched_or_hint_tag)``.

    ``matched_or_hint_tag`` is the ``"key=value"`` that produced the category, or
    — when the result is ``UNKNOWN`` — the most informative raw tag seen (for
    logging), or ``None`` if the element carried no classifying tag at all.
    """
    for key in OSM_KEY_PRIORITY:
        value = tags.get(key)
        if value is None:
            continue
        category = OSM_TAG_TO_CATEGORY.get((key, value))
        if category is not None and category is not C.UNKNOWN:
            return category, f"{key}={value}"

    # No positive match: surface a hint tag for logging.
    for key in _DESCRIPTIVE_KEYS:
        value = tags.get(key)
        if value:
            return C.UNKNOWN, f"{key}={value}"
    return C.UNKNOWN, None
