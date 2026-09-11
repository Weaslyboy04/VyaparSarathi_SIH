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
    "sports goods": (C.SPORTS_GOODS, ()),
    "sporting goods": (C.SPORTS_GOODS, ()),
    "sports equipment": (C.SPORTS_GOODS, ()),
    "sports shop": (C.SPORTS_GOODS, ()),
    "cricket kit": (C.SPORTS_GOODS, ("cricket",)),
    "gym equipment": (C.SPORTS_GOODS, ("gym equipment",)),
    "gym": (C.GYM_FITNESS, ()),
    "fitness center": (C.GYM_FITNESS, ()),
    "fitness centre": (C.GYM_FITNESS, ()),
    "gymnasium": (C.GYM_FITNESS, ()),
    "yoga center": (C.GYM_FITNESS, ()),
    "yoga centre": (C.GYM_FITNESS, ()),
    "akhada": (C.GYM_FITNESS, ()),
    "xerox": (C.PRINTING_XEROX, ()),
    "photocopy": (C.PRINTING_XEROX, ()),
    "printing": (C.PRINTING_XEROX, ()),
    "print shop": (C.PRINTING_XEROX, ()),
    "flex printing": (C.PRINTING_XEROX, ()),
    "computer center": (C.COMPUTER_SERVICES, ()),
    "computer centre": (C.COMPUTER_SERVICES, ()),
    "cyber cafe": (C.COMPUTER_SERVICES, ()),
    "internet cafe": (C.COMPUTER_SERVICES, ()),
    "computer repair": (C.COMPUTER_SERVICES, ()),
    "laptop repair": (C.COMPUTER_SERVICES, ()),
    "welding": (C.WELDING_FABRICATION, ()),
    "fabrication": (C.WELDING_FABRICATION, ()),
    "welder": (C.WELDING_FABRICATION, ()),
    "steel fabrication": (C.WELDING_FABRICATION, ()),
    "catering": (C.CATERING, ()),
    "caterer": (C.CATERING, ()),
    "tiffin service": (C.CATERING, ()),
    "tiffin": (C.CATERING, ()),
    "mess": (C.CATERING, ()),
    "tent house": (C.EVENT_SERVICES, ()),
    "event decor": (C.EVENT_SERVICES, ()),
    "decoration": (C.EVENT_SERVICES, ()),
    "wedding decor": (C.EVENT_SERVICES, ()),
    "banquet": (C.EVENT_SERVICES, ()),
    "lpg agency": (C.UTILITY_AGENCY, ()),
    "gas agency": (C.UTILITY_AGENCY, ()),
    "recharge shop": (C.UTILITY_AGENCY, ()),
    "mobile recharge": (C.UTILITY_AGENCY, ()),
    "banking correspondent": (C.UTILITY_AGENCY, ()),
    "bc agent": (C.UTILITY_AGENCY, ()),
    "csc center": (C.UTILITY_AGENCY, ()),
    "csc centre": (C.UTILITY_AGENCY, ()),
    "common service center": (C.UTILITY_AGENCY, ()),
    "tuition": (C.EDUCATION_SERVICES, ()),
    "coaching center": (C.EDUCATION_SERVICES, ()),
    "coaching centre": (C.EDUCATION_SERVICES, ()),
    "driving school": (C.EDUCATION_SERVICES, ()),
    "training institute": (C.EDUCATION_SERVICES, ()),
    "laundry": (C.LAUNDRY, ()),
    "dry cleaning": (C.LAUNDRY, ()),
    "dry cleaner": (C.LAUNDRY, ()),
    "cycle shop": (C.CYCLE_REPAIR, ()),
    "bicycle shop": (C.CYCLE_REPAIR, ()),
    "cycle repair": (C.CYCLE_REPAIR, ()),
    "bicycle repair": (C.CYCLE_REPAIR, ()),
    "footwear": (C.FOOTWEAR, ()),
    "shoe shop": (C.FOOTWEAR, ()),
    "shoes": (C.FOOTWEAR, ()),
    "chappal": (C.FOOTWEAR, ()),
    "sandal shop": (C.FOOTWEAR, ()),
}

# Generic words dropped before looking for leftover subtype tokens.
_STOPWORDS: frozenset[str] = frozenset(
    {"store", "shop", "stall", "centre", "center", "point", "and", "of", "for", "the", "a", "an"}
)


def _clean_subtypes(tokens: Iterable[str]) -> list[str]:
    return sorted({t.strip().lower() for t in tokens if t and t.strip()})


def _rank_categories(text: str) -> list[tuple[C, float, tuple[str, ...]]]:
    """Every internal category (excluding `UNKNOWN`/`OTHER_TRADE`, neither of
    which is a real thing a user described), scored against `text` by the
    BEST match of its alias keys or its own category name, sorted
    descending. Shared by the strict auto-resolve pass and the looser
    suggestion pass below."""
    best_per_category: dict[C, tuple[float, tuple[str, ...]]] = {}
    for key, (cat, implied) in PROPOSED_ALIASES.items():
        score = fuzz.token_sort_ratio(text, key)
        current = best_per_category.get(cat)
        if current is None or score > current[0]:
            best_per_category[cat] = (score, implied)
    for cat in C:
        if cat in (C.UNKNOWN, C.OTHER_TRADE):
            continue
        score = fuzz.token_sort_ratio(text, cat.value)
        current = best_per_category.get(cat)
        if current is None or score > current[0]:
            best_per_category[cat] = (score, ())
    return sorted(
        ((cat, score, subtypes) for cat, (score, subtypes) in best_per_category.items()),
        key=lambda t: t[1],
        reverse=True,
    )


def _best_fuzzy_category(
    text: str, cfg: ProposedBusinessConfig
) -> tuple[C, tuple[str, ...]] | None:
    """The single category `text` fuzzy-matches, if — and only if — it beats
    every other candidate category by `cfg.fuzzy_match_margin` and clears
    `cfg.fuzzy_match_threshold`. Returns `None` — never a guess — the moment
    two distinct categories are both plausible."""
    ranked = _rank_categories(text)
    if not ranked:  # pragma: no cover — PROPOSED_ALIASES is never empty
        return None
    top_cat, top_score, top_subtypes = ranked[0]
    if top_score < cfg.fuzzy_match_threshold:
        return None
    if len(ranked) > 1:
        runner_up_score = ranked[1][1]
        if top_score - runner_up_score < cfg.fuzzy_match_margin:
            return None
    return top_cat, top_subtypes


def _suggest_fuzzy_categories(text: str, cfg: ProposedBusinessConfig) -> list[C]:
    """Best-effort category suggestions for a CLARIFYING question — never
    for auto-resolution. Looser than `_best_fuzzy_category`: takes the top
    `cfg.suggestion_top_n` distinct categories clearing only
    `cfg.suggestion_threshold`, so the entrepreneur has something concrete
    to pick from instead of a flat "unknown" even when no single category
    was clearly ahead."""
    ranked = _rank_categories(text)
    return [cat for cat, score, _ in ranked if score >= cfg.suggestion_threshold][
        : cfg.suggestion_top_n
    ]


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

        # No confident auto-resolve. Look for weaker-but-plausible
        # candidates to offer as a clarifying question BEFORE giving up —
        # never silently drop the business (CLAUDE.md: "ask more" beats
        # "insufficient evidence").
        suggestions = _suggest_fuzzy_categories(leftover, cfg) if leftover else []
        if suggestions:
            note = (
                "Could not confidently map the proposed business to one category; "
                f"closest possibilities: {', '.join(c.value for c in suggestions)}."
            )
            logger.info("proposed %r -> ambiguous, suggesting %s", raw, suggestions)
            return ProposedBusiness(
                category=C.UNKNOWN,
                subtypes=_clean_subtypes(subtypes),
                raw_text=raw,
                resolved=False,
                note=note,
                candidate_categories=[c.value for c in suggestions],
            )

        # Genuinely novel text, no suggestion clears even the loose bar —
        # still enter scoring/discovery as a generic trade rather than
        # vanishing from the report.
        pb = ProposedBusiness(
            category=C.OTHER_TRADE,
            subtypes=_clean_subtypes(subtypes),
            raw_text=raw,
            resolved=True,
            note=(
                "Could not map this to a specific known category; treated as a "
                "generic trade/service — market and capital comparisons for it are "
                "less detailed than for a named category."
            ),
        )
        logger.info("proposed %r -> other_trade (no match, no suggestion)", raw)
        return pb

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
        candidate_categories=sorted(c.value for c in matched_categories),
    )
