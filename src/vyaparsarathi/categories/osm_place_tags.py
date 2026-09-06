"""OSM place / amenity tags <-> Phase 2C settlement and activity vocabularies.

Mirrors the ``osm_map`` / ``osm_query_tags`` pair for businesses (CLAUDE.md §8):
this module is the *only* place that knows the OSM tag names for settlements and
activity anchors. Two directions:

* ``map_place_tags`` / ``map_activity_tags`` — an element's tags -> our enum
  (used during normalization).
* ``DEMAND_QUERY_SELECTORS`` — the single tag-selector list for the one Overpass
  union query the demand acquisition layer issues.
"""

from __future__ import annotations

from vyaparsarathi.models.demand import ActivityKind, SettlementType

# --- settlements -----------------------------------------------------------

# OSM `place=*` -> settlement class. Ordered roughly largest to smallest.
PLACE_TAG_TO_SETTLEMENT: dict[str, SettlementType] = {
    "city": SettlementType.CITY,
    "town": SettlementType.TOWN,
    "village": SettlementType.VILLAGE,
    "hamlet": SettlementType.HAMLET,
    "isolated_dwelling": SettlementType.HAMLET,
    "farm": SettlementType.HAMLET,
    "suburb": SettlementType.SUBURB,
    "quarter": SettlementType.SUBURB,
    "neighbourhood": SettlementType.NEIGHBOURHOOD,
}

# `place` values we deliberately ignore (not habitation): regions, localities,
# plots, the sea, etc. Kept explicit so a genuinely new value is logged, not
# silently dropped.
PLACE_TAG_IGNORED: frozenset[str] = frozenset(
    {"locality", "region", "province", "state", "district", "county", "municipality", "plot"}
)


def map_place_tags(tags: dict[str, str]) -> tuple[SettlementType | None, str | None]:
    """Return ``(settlement_type, hint)``.

    ``settlement_type`` is ``None`` when the element is not habitation we model;
    ``hint`` is the ``"place=value"`` seen, for logging unmapped values.
    """
    value = tags.get("place")
    if value is None:
        return None, None
    mapped = PLACE_TAG_TO_SETTLEMENT.get(value)
    if mapped is not None:
        return mapped, f"place={value}"
    if value in PLACE_TAG_IGNORED:
        return None, None
    return None, f"place={value}"


# --- activity anchors ----------------------------------------------------

# (key, value) -> activity kind. Four kinds only (CLAUDE.md §11 STEP 6).
ACTIVITY_TAG_TO_KIND: dict[tuple[str, str], ActivityKind] = {
    ("amenity", "school"): ActivityKind.SCHOOL,
    ("amenity", "college"): ActivityKind.SCHOOL,
    ("amenity", "marketplace"): ActivityKind.MARKETPLACE,
    ("amenity", "bank"): ActivityKind.BANK,
    ("amenity", "atm"): ActivityKind.BANK,
    ("amenity", "bus_station"): ActivityKind.TRANSPORT_STOP,
    ("highway", "bus_stop"): ActivityKind.TRANSPORT_STOP,
    ("railway", "station"): ActivityKind.TRANSPORT_STOP,
    ("railway", "halt"): ActivityKind.TRANSPORT_STOP,
    ("public_transport", "station"): ActivityKind.TRANSPORT_STOP,
}

_ACTIVITY_KEY_PRIORITY: tuple[str, ...] = (
    "amenity",
    "railway",
    "highway",
    "public_transport",
)


def map_activity_tags(tags: dict[str, str]) -> tuple[ActivityKind | None, str | None]:
    """Return ``(activity_kind, hint)`` — ``kind`` is ``None`` when the element is
    not one of the four anchor kinds."""
    for key in _ACTIVITY_KEY_PRIORITY:
        value = tags.get(key)
        if value is None:
            continue
        kind = ACTIVITY_TAG_TO_KIND.get((key, value))
        if kind is not None:
            return kind, f"{key}={value}"
    return None, None


# --- the single Overpass union query -----------------------------------

# One request covers settlements + all four activity kinds (CLAUDE.md §11: one
# HTTP call, one cache key). Rendered as nwr["key"="value"](around:...).
DEMAND_QUERY_SELECTORS: list[tuple[str, str]] = [
    ("place", "city"),
    ("place", "town"),
    ("place", "village"),
    ("place", "hamlet"),
    ("place", "isolated_dwelling"),
    ("place", "suburb"),
    ("place", "neighbourhood"),
    *ACTIVITY_TAG_TO_KIND.keys(),
]
