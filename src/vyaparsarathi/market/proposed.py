"""Map the entrepreneur's proposed business onto the internal taxonomy (STEP 4).

Deliberately not NLP: an exact category name, or a small alias table, plus
recognition of known product subtype tokens, is tried FIRST and always wins
when it succeeds. Only when that pass finds ZERO matches does a deterministic
fuzzy-typo fallback run (`rapidfuzz`, CLAUDE.md §33 — already a project
dependency, used by `dedup/`) — and only when exactly one category is
clearly ahead of every other (`market/proposed_config.py`'s threshold and
margin). If the text maps to zero known categories (exactly or fuzzily), or
to more than one conflicting category, the result is an explicit
``resolved=False`` / ``UNKNOWN`` — never a silent guess.
"""

from __future__ import annotations

from collections.abc import Iterable

from rapidfuzz import fuzz

from vyaparsarathi.market.models import ProposedBusiness
from vyaparsarathi.market.proposed_config import (
    DEFAULT_PROPOSED_BUSINESS_CONFIG,
    ProposedBusinessConfig,
)
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


def _best_fuzzy_category(
    text: str, cfg: ProposedBusinessConfig
) -> tuple[C, tuple[str, ...]] | None:
    """The single category `text` fuzzy-matches, if — and only if — it beats
    every other candidate category by `cfg.fuzzy_match_margin` and clears
    `cfg.fuzzy_match_threshold`. Scores every alias key AND every internal
    category name, then takes the BEST score per resulting category (an
    alias and its own category name are the same candidate, never double
    counted as "competing"). Returns `None` — never a guess — the moment two
    distinct categories are both plausible."""
    best_per_category: dict[C, tuple[float, tuple[str, ...]]] = {}
    for key, (cat, implied) in PROPOSED_ALIASES.items():
        score = fuzz.token_sort_ratio(text, key)
        current = best_per_category.get(cat)
        if current is None or score > current[0]:
            best_per_category[cat] = (score, implied)
    for cat in C:
        if cat is C.UNKNOWN:
            continue
        score = fuzz.token_sort_ratio(text, cat.value)
        current = best_per_category.get(cat)
        if current is None or score > current[0]:
            best_per_category[cat] = (score, ())

    if not best_per_category:  # pragma: no cover — PROPOSED_ALIASES is never empty
        return None
    ranked = sorted(best_per_category.items(), key=lambda kv: kv[1][0], reverse=True)
    top_cat, (top_score, top_subtypes) = ranked[0]
    if top_score < cfg.fuzzy_match_threshold:
        return None
    if len(ranked) > 1:
        runner_up_score = ranked[1][1][0]
        if top_score - runner_up_score < cfg.fuzzy_match_margin:
            return None
    return top_cat, top_subtypes


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


def resolve_proposed_business(
    text: str,
    extra_subtypes: Iterable[str] = (),
    *,
    cfg: ProposedBusinessConfig = DEFAULT_PROPOSED_BUSINESS_CONFIG,
) -> ProposedBusiness:
    """Map free text (e.g. ``"pulses grocery store"``) onto the taxonomy.

    Returns ``resolved=False`` with category ``UNKNOWN`` when the text matches no
    known category (exactly, by alias, or by a clear, unique fuzzy match) or
    matches several conflicting ones.
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
        leftover = " ".join(t for t in tokens if t not in _STOPWORDS)
        fuzzy_match = _best_fuzzy_category(leftover, cfg) if leftover else None
        if fuzzy_match is not None:
            category, implied = fuzzy_match
            subtypes.update(implied)
            pb = ProposedBusiness(
                category=category,
                subtypes=_clean_subtypes(subtypes),
                raw_text=raw,
                resolved=True,
                note=(
                    f"Resolved from text to category '{category.value}' via a fuzzy "
                    "match (likely typo) — verify this is what was meant."
                ),
            )
            logger.info(
                "proposed %r -> %s subtypes=%s (fuzzy match)", raw, category.value, pb.subtypes
            )
            return pb
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
