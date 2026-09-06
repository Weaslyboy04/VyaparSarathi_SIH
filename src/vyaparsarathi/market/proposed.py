"""Map the entrepreneur's proposed business onto the internal taxonomy (STEP 4).

Deliberately not NLP: an exact category name, or a small alias table, plus
recognition of known product subtype tokens. If the text maps to zero known
categories, or to more than one conflicting category, the result is an explicit
``resolved=False`` / ``UNKNOWN`` — never a silent guess.
"""

from __future__ import annotations

from collections.abc import Iterable

from vyaparsarathi.market.models import ProposedBusiness
from vyaparsarathi.market.relationships import KNOWN_SUBTYPES
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.normalization.text import normalize_name
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

# Free-text token / phrase -> (internal category, implied subtype tokens).
# Keys are matched against the normalized (lowercased, de-punctuated) input as
# whole words (single-word keys) or substrings (multi-word keys).
PROPOSED_ALIASES: dict[str, tuple[C, tuple[str, ...]]] = {
    "grocery": (C.GROCERY, ()),
    "kirana": (C.GROCERY, ()),
    "provision": (C.GROCERY, ()),
    "provisions": (C.GROCERY, ()),
    "supermarket": (C.GROCERY, ()),
    "grocer": (C.GROCERY, ()),
    "pulses": (C.GROCERY, ("pulses",)),
    "dal": (C.GROCERY, ("pulses",)),
    "grain": (C.GROCERY, ("grain",)),
    "grains": (C.GROCERY, ("grain",)),
    "rice": (C.GROCERY, ("rice",)),
    "atta": (C.GROCERY, ("flour",)),
    "flour": (C.GROCERY, ("flour",)),
    "spices": (C.GROCERY, ("spices",)),
    "masala": (C.GROCERY, ("masala",)),
    "general store": (C.GENERAL_STORE, ()),
    "general merchant": (C.GENERAL_STORE, ()),
    "variety store": (C.GENERAL_STORE, ()),
    "dairy": (C.DAIRY, ()),
    "milk": (C.DAIRY, ()),
    "pharmacy": (C.PHARMACY, ()),
    "chemist": (C.PHARMACY, ()),
    "medical": (C.PHARMACY, ()),
    "medicine": (C.PHARMACY, ()),
    "restaurant": (C.RESTAURANT, ()),
    "hotel": (C.RESTAURANT, ()),
    "dhaba": (C.RESTAURANT, ()),
    "cafe": (C.RESTAURANT, ()),
    "food stall": (C.FOOD_STALL, ()),
    "snacks": (C.FOOD_STALL, ()),
    "tea stall": (C.FOOD_STALL, ()),
    "bakery": (C.FOOD_STALL, ()),
    "hardware": (C.HARDWARE, ()),
    "building material": (C.BUILDING_MATERIALS, ()),
    "building materials": (C.BUILDING_MATERIALS, ()),
    "cement": (C.BUILDING_MATERIALS, ()),
    "agri input": (C.AGRI_INPUT, ()),
    "agri inputs": (C.AGRI_INPUT, ()),
    "agriculture input": (C.AGRI_INPUT, ()),
    "seed": (C.AGRI_INPUT, ("seed",)),
    "seeds": (C.AGRI_INPUT, ("seed",)),
    "fertiliser": (C.AGRI_INPUT, ("fertiliser",)),
    "fertilizer": (C.AGRI_INPUT, ("fertiliser",)),
    "pesticide": (C.AGRI_INPUT, ("pesticide",)),
    "cattle feed": (C.LIVESTOCK_SERVICES, ("feed",)),
    "animal feed": (C.LIVESTOCK_SERVICES, ("feed",)),
    "poultry": (C.LIVESTOCK_SERVICES, ()),
    "veterinary": (C.LIVESTOCK_SERVICES, ()),
    "food processing": (C.FOOD_PROCESSING, ()),
    "flour mill": (C.FOOD_PROCESSING, ("flour",)),
    "oil mill": (C.FOOD_PROCESSING, ("oil",)),
    "dal mill": (C.FOOD_PROCESSING, ("pulses",)),
    "rice mill": (C.FOOD_PROCESSING, ("rice",)),
    "tailor": (C.TAILORING, ()),
    "tailoring": (C.TAILORING, ()),
    "boutique": (C.CLOTHING, ()),
    "clothing": (C.CLOTHING, ()),
    "garments": (C.CLOTHING, ()),
    "readymade": (C.CLOTHING, ()),
    "salon": (C.SALON, ()),
    "parlour": (C.SALON, ()),
    "parlor": (C.SALON, ()),
    "barber": (C.SALON, ()),
    "mobile": (C.MOBILE_ELECTRONICS, ()),
    "electronics": (C.MOBILE_ELECTRONICS, ()),
    "stationery": (C.STATIONERY, ()),
    "books": (C.STATIONERY, ()),
    "furniture": (C.FURNITURE, ()),
    "carpenter": (C.FURNITURE, ()),
    "garage": (C.AUTOMOBILE_REPAIR, ()),
    "auto repair": (C.AUTOMOBILE_REPAIR, ()),
    "puncture": (C.AUTOMOBILE_REPAIR, ()),
}

# Generic words dropped before looking for leftover subtype tokens.
_STOPWORDS: frozenset[str] = frozenset(
    {"store", "shop", "stall", "centre", "center", "point", "and", "of", "for", "the", "a", "an"}
)


def _clean_subtypes(tokens: Iterable[str]) -> list[str]:
    return sorted({t.strip().lower() for t in tokens if t and t.strip()})


def proposed_from_category(
    category: C, subtypes: Iterable[str] = (), raw_text: str | None = None
) -> ProposedBusiness:
    """Build a :class:`ProposedBusiness` from an already-known internal category."""
    resolved = category is not C.UNKNOWN
    return ProposedBusiness(
        category=category,
        subtypes=_clean_subtypes(subtypes),
        raw_text=raw_text,
        resolved=resolved,
        note=None if resolved else "Proposed category is 'unknown'.",
    )


def resolve_proposed_business(text: str, extra_subtypes: Iterable[str] = ()) -> ProposedBusiness:
    """Map free text (e.g. ``"pulses grocery store"``) onto the taxonomy.

    Returns ``resolved=False`` with category ``UNKNOWN`` when the text matches no
    known category or matches several conflicting ones.
    """
    raw = text
    norm = normalize_name(text)
    if not norm:
        return ProposedBusiness(
            category=C.UNKNOWN,
            raw_text=raw,
            resolved=False,
            note="Empty proposed-business description.",
        )

    tokens = norm.split(" ")
    matched_categories: set[C] = set()
    subtypes: set[str] = set(_clean_subtypes(extra_subtypes))

    # 1. exact internal category name
    try:
        matched_categories.add(C(norm))
    except ValueError:
        pass

    # 2. alias table, longest phrase first, consuming matched spans so that a
    #    phrase ("dal mill") is not also counted as its parts ("dal").
    remaining = f" {norm} "
    for key in sorted(PROPOSED_ALIASES, key=len, reverse=True):
        cat, implied = PROPOSED_ALIASES[key]
        span = f" {key} "
        if span in remaining:
            matched_categories.add(cat)
            subtypes.update(implied)
            remaining = remaining.replace(span, "  ")

    # 3. bare known subtype tokens present in the original text
    for token in tokens:
        if token in KNOWN_SUBTYPES and token not in _STOPWORDS:
            subtypes.add(token)

    if len(matched_categories) == 1:
        category = next(iter(matched_categories))
        pb = ProposedBusiness(
            category=category,
            subtypes=_clean_subtypes(subtypes),
            raw_text=raw,
            resolved=True,
            note=f"Resolved from text to category '{category.value}'.",
        )
        logger.info("proposed %r -> %s subtypes=%s", raw, category.value, pb.subtypes)
        return pb

    if not matched_categories:
        note = "Could not map the proposed business to any known category."
    else:
        note = (
            "Proposed business mapped to multiple categories "
            f"({', '.join(sorted(c.value for c in matched_categories))}); needs clarification."
        )
    logger.info("proposed %r -> unknown (%s)", raw, note)
    return ProposedBusiness(
        category=C.UNKNOWN,
        subtypes=_clean_subtypes(subtypes),
        raw_text=raw,
        resolved=False,
        note=note,
    )
