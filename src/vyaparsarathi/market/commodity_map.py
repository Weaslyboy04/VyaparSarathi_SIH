"""Category/subtype -> AGMARKNET commodity name mapping (CLAUDE.md §30's
"never invent missing data" applied the other way round: never force-fit an
irrelevant commodity onto a business that doesn't trade in one).

Lives in `market/`, not `categories/` — CLAUDE.md §8 reserves
`categories/<source>_map.py` for the opposite direction (an external
source's own tag vocabulary -> this taxonomy). This mapping runs
taxonomy/subtype -> an external vocabulary (AGMARKNET's commodity names),
which is domain lookup logic in the same class as `market/relationships.py`.

Subtype keys reuse the exact vocabulary `market/relationships.py::
KNOWN_SUBTYPES` already recognises from free text (e.g. "pulses grocery
store" -> `subtypes=("pulses",)`, per `market/proposed.py`) — no new
extraction work. A subtype hit is more specific than the coarse
category-level fallback and wins over it. Most of the taxonomy has no row
at either level and returns `None` on purpose: AGMARKNET tracks wholesale
mandi prices for raw farm produce, which is irrelevant to a pharmacy, a
salon, a hardware shop, etc.

[assumption] The exact commodity name strings still need a live-API
verification pass before being treated as final — see
`tests/fixtures/agmarknet/README.md`. A single wrong character silently
returns zero records, indistinguishable from "genuinely no data."
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.models.taxonomy import BusinessCategory as C

# subtype token -> real AGMARKNET commodity name string(s), most specific
# first. Deliberately excludes subtypes with no honest AGMARKNET analogue
# (manufactured agri-inputs like "feed"/"seed"/"fertiliser"/"pesticide", and
# "tea" as a retail/branded good rather than a raw mandi commodity) — those
# fall through to the coarse category-level table below, or to `None`.
SUBTYPE_COMMODITY_MAP: dict[str, tuple[str, ...]] = {
    "pulses": (
        "Arhar (Tur/Red Gram)(Whole)",
        "Bengal Gram(Gram)(Whole)",
        "Green Gram (Moong)(Whole)",
        "Black Gram (Urd Beans)(Whole)",
        "Masur Dal",
    ),
    "dal": (
        "Arhar (Tur/Red Gram)(Whole)",
        "Bengal Gram(Gram)(Whole)",
        "Green Gram (Moong)(Whole)",
        "Black Gram (Urd Beans)(Whole)",
        "Masur Dal",
    ),
    "grain": ("Wheat", "Rice", "Maize", "Bajra"),
    "wheat": ("Wheat",),
    "atta": ("Wheat Atta",),
    "flour": ("Wheat Atta",),
    "rice": ("Rice", "Paddy(Dhan)(Common)"),
    "oil": ("Mustard Oil", "Groundnut Oil", "Soyabean Oil"),
    "spices": ("Chilli Red", "Turmeric", "Coriander seed"),
    "masala": ("Chilli Red", "Turmeric", "Coriander seed"),
    "sugar": ("Sugar", "Gur(Jaggery)"),
}

# category -> coarse fallback commodities, used only when no subtype-level
# match fires. Absent category -> `None` at the `commodities_for` level.
# [assumption] Only the categories that genuinely trade in raw/bulk
# agricultural commodities get a row here — see the module docstring for why
# DAIRY (milk isn't an AGMARKNET mandi commodity) and AGRI_INPUT (mostly
# manufactured inputs, not mandi produce) are deliberately absent.
CATEGORY_COMMODITY_MAP: dict[C, tuple[str, ...]] = {
    C.GROCERY: ("Rice", "Wheat", "Bengal Gram(Gram)(Whole)"),
    C.GENERAL_STORE: ("Rice", "Wheat"),
    C.FOOD_PROCESSING: ("Wheat", "Paddy(Dhan)(Common)", "Groundnut"),
}


def commodities_for(category: C, subtypes: Iterable[str]) -> tuple[str, ...] | None:
    """`None` => AGMARKNET has no relevant signal for this business at all;
    never force-fit an irrelevant commodity (CLAUDE.md §30). A subtype match
    is more specific and wins over the coarse category-level fallback;
    results are deduplicated, order-preserved."""
    seen: dict[str, None] = {}
    for subtype in subtypes:
        for commodity in SUBTYPE_COMMODITY_MAP.get(subtype, ()):
            seen.setdefault(commodity, None)
    if seen:
        return tuple(seen)
    return CATEGORY_COMMODITY_MAP.get(category)


__all__ = ["CATEGORY_COMMODITY_MAP", "SUBTYPE_COMMODITY_MAP", "commodities_for"]
