"""The single configuration layer for competitor relationships (CLAUDE.md §11, §12).

Everything that says "how does category X relate to category Y for a business
proposing X" lives here and nowhere else. The classifier reads these tables; it
contains no hard-coded category pairs of its own. Edit this file as real rural
data teaches us better groupings.

Directional, not symmetric: ``relationship_for(GROCERY, DAIRY)`` (a proposed
grocery, an existing dairy) need not equal ``relationship_for(DAIRY, GROCERY)``.
Anything not listed for a proposed category defaults to ``IRRELEVANT``; the
proposed category is always ``DIRECT`` against itself.
"""

from __future__ import annotations

from vyaparsarathi.market.models import Relationship
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_D = Relationship.DIRECT
_A = Relationship.ADJACENT

# proposed category -> { existing category -> relationship }.
# List only DIRECT and ADJACENT entries; omitted pairs are IRRELEVANT.
CATEGORY_RELATIONSHIPS: dict[C, dict[C, Relationship]] = {
    C.GROCERY: {
        C.GROCERY: _D,
        C.GENERAL_STORE: _D,  # a village general store is a full grocery substitute
        C.DAIRY: _A,
        C.FOOD_STALL: _A,
        C.FOOD_PROCESSING: _A,  # flour / oil / dal mills sell staples
        C.AGRI_INPUT: _A,  # rural agrarian shops often also stock staples
    },
    C.GENERAL_STORE: {
        C.GENERAL_STORE: _D,
        C.GROCERY: _D,
        C.STATIONERY: _A,
        C.DAIRY: _A,
        C.FOOD_STALL: _A,
        C.MOBILE_ELECTRONICS: _A,
        C.AGRI_INPUT: _A,
    },
    C.DAIRY: {
        C.DAIRY: _D,
        C.GROCERY: _A,
        C.GENERAL_STORE: _A,
        C.FOOD_STALL: _A,
    },
    C.AGRI_INPUT: {
        C.AGRI_INPUT: _D,
        C.LIVESTOCK_SERVICES: _A,  # feed / veterinary overlap
        C.GROCERY: _A,
        C.GENERAL_STORE: _A,
        C.HARDWARE: _A,  # implements / sprayers / tools
        C.FOOD_PROCESSING: _A,
    },
    C.LIVESTOCK_SERVICES: {
        C.LIVESTOCK_SERVICES: _D,
        C.AGRI_INPUT: _A,
    },
    C.FOOD_PROCESSING: {
        C.FOOD_PROCESSING: _D,
        C.GROCERY: _A,
        C.GENERAL_STORE: _A,
        C.AGRI_INPUT: _A,
    },
    C.PHARMACY: {
        C.PHARMACY: _D,
    },
    C.RESTAURANT: {
        C.RESTAURANT: _D,
        C.FOOD_STALL: _D,
    },
    C.FOOD_STALL: {
        C.FOOD_STALL: _D,
        C.RESTAURANT: _D,
        C.DAIRY: _A,
        C.GROCERY: _A,
    },
    C.HARDWARE: {
        C.HARDWARE: _D,
        C.BUILDING_MATERIALS: _D,
        C.AGRI_INPUT: _A,
        C.FURNITURE: _A,
    },
    C.BUILDING_MATERIALS: {
        C.BUILDING_MATERIALS: _D,
        C.HARDWARE: _D,
        C.FURNITURE: _A,
    },
    C.CLOTHING: {
        C.CLOTHING: _D,
        C.TAILORING: _A,
    },
    C.TAILORING: {
        C.TAILORING: _D,
        C.CLOTHING: _A,
    },
    C.MOBILE_ELECTRONICS: {
        C.MOBILE_ELECTRONICS: _D,
        C.GENERAL_STORE: _A,
        C.STATIONERY: _A,
    },
    C.STATIONERY: {
        C.STATIONERY: _D,
        C.GENERAL_STORE: _A,
        C.MOBILE_ELECTRONICS: _A,
    },
    C.FURNITURE: {
        C.FURNITURE: _D,
        C.BUILDING_MATERIALS: _A,
        C.HARDWARE: _A,
    },
    C.AUTOMOBILE_REPAIR: {
        C.AUTOMOBILE_REPAIR: _D,
    },
    C.SALON: {
        C.SALON: _D,
    },
}

# Optional refinement by proposed subtype (e.g. a "pulses" grocery). A subtype
# rule can only *strengthen* the base category relationship, never weaken it.
# subtype token -> { existing category -> relationship the subtype implies }.
SUBTYPE_CATEGORY_OVERLAPS: dict[str, dict[C, Relationship]] = {
    "pulses": {C.GROCERY: _D, C.GENERAL_STORE: _D, C.FOOD_PROCESSING: _A, C.AGRI_INPUT: _A},
    "grain": {C.GROCERY: _D, C.GENERAL_STORE: _D, C.FOOD_PROCESSING: _A, C.AGRI_INPUT: _A},
    "flour": {C.GROCERY: _D, C.GENERAL_STORE: _D, C.FOOD_PROCESSING: _A},
    "rice": {C.GROCERY: _D, C.GENERAL_STORE: _D, C.FOOD_PROCESSING: _A},
    "oil": {C.GROCERY: _A, C.GENERAL_STORE: _A, C.FOOD_PROCESSING: _A},
    "spices": {C.GROCERY: _D, C.GENERAL_STORE: _D},
    "masala": {C.GROCERY: _D, C.GENERAL_STORE: _D},
    "feed": {C.LIVESTOCK_SERVICES: _D, C.AGRI_INPUT: _A},
    "seed": {C.AGRI_INPUT: _D},
    "fertiliser": {C.AGRI_INPUT: _D},
    "pesticide": {C.AGRI_INPUT: _D},
}

# Subtype tokens the resolver recognises when parsing free text (STEP 4). Tokens
# outside this set are ignored rather than guessed at.
KNOWN_SUBTYPES: frozenset[str] = frozenset(SUBTYPE_CATEGORY_OVERLAPS) | frozenset(
    {"dal", "atta", "wheat", "sugar", "tea"}
)

_STRENGTH: dict[Relationship, int] = {
    Relationship.IRRELEVANT: 0,
    Relationship.ADJACENT: 1,
    Relationship.DIRECT: 2,
}


def relationship_for(proposed: C, existing: C) -> Relationship:
    """Base (category-only) relationship of an ``existing`` business to a
    business proposing category ``proposed``."""
    if proposed == existing:
        return Relationship.DIRECT
    return CATEGORY_RELATIONSHIPS.get(proposed, {}).get(existing, Relationship.IRRELEVANT)


def strongest(*relationships: Relationship) -> Relationship:
    """The strongest of the given relationships (IRRELEVANT < ADJACENT < DIRECT)."""
    return max(relationships, key=lambda r: _STRENGTH[r])
