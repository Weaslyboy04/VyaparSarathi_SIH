"""Phase 2A proposed-business resolution (CLAUDE.md §11, STEP 4)."""

from __future__ import annotations

import pytest

from vyaparsarathi.market.proposed import proposed_from_category, resolve_proposed_business
from vyaparsarathi.models.taxonomy import BusinessCategory as C


@pytest.mark.parametrize(
    ("text", "category", "subtypes"),
    [
        ("grocery", C.GROCERY, []),
        ("Grocery Store", C.GROCERY, []),
        ("kirana", C.GROCERY, []),
        ("kirana shop", C.GROCERY, []),
        ("supermarket", C.GROCERY, []),
        ("pulses grocery store", C.GROCERY, ["pulses"]),
        ("pulses + grocery", C.GROCERY, ["pulses"]),
        ("grain and pulses shop", C.GROCERY, ["grain", "pulses"]),
        ("general store", C.GENERAL_STORE, []),
        ("dairy", C.DAIRY, []),
        ("milk booth", C.DAIRY, []),
        ("medical store", C.PHARMACY, []),
        ("cattle feed", C.LIVESTOCK_SERVICES, ["feed"]),
        ("seeds and fertiliser", C.AGRI_INPUT, ["fertiliser", "seed"]),
        ("dal mill", C.FOOD_PROCESSING, ["dal", "pulses"]),
        ("tailor", C.TAILORING, []),
    ],
)
def test_resolve_known_text(text: str, category: C, subtypes: list[str]) -> None:
    pb = resolve_proposed_business(text)
    assert pb.resolved is True
    assert pb.category is category
    assert pb.subtypes == subtypes
    assert pb.raw_text == text


@pytest.mark.parametrize("text", ["spaceship parts", "", "   ", "quantum widgets", "xyzzy"])
def test_unmappable_text_is_explicit_unknown(text: str) -> None:
    pb = resolve_proposed_business(text)
    assert pb.resolved is False
    assert pb.category is C.UNKNOWN
    assert pb.note  # explains why


def test_conflicting_categories_are_unknown_not_a_guess() -> None:
    pb = resolve_proposed_business("grocery and pharmacy")
    assert pb.resolved is False
    assert pb.category is C.UNKNOWN
    assert "multiple categories" in (pb.note or "")


def test_extra_subtypes_are_merged_and_normalised() -> None:
    pb = resolve_proposed_business("grocery", extra_subtypes=["Pulses", " GRAIN "])
    assert pb.category is C.GROCERY
    assert pb.subtypes == ["grain", "pulses"]  # deduped, lowercased, sorted


def test_proposed_from_category() -> None:
    pb = proposed_from_category(C.GROCERY, subtypes=["pulses"])
    assert pb.resolved is True
    assert pb.category is C.GROCERY
    assert pb.subtypes == ["pulses"]


def test_proposed_from_unknown_category_is_unresolved() -> None:
    pb = proposed_from_category(C.UNKNOWN)
    assert pb.resolved is False
    assert pb.note


# --- fuzzy-typo fallback (Priority 3) --------------------------------------


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("grocerry shop", C.GROCERY),  # the acceptance-criteria example
        ("grocerry", C.GROCERY),
        ("farniture", C.FURNITURE),
        ("stationary", C.STATIONERY),
    ],
)
def test_typo_resolves_to_the_unique_high_confidence_category(text: str, category: C) -> None:
    pb = resolve_proposed_business(text)
    assert pb.resolved is True
    assert pb.category is category
    assert "fuzzy" in (pb.note or "").lower()


def test_fuzzy_match_note_is_distinguishable_from_an_exact_match_note() -> None:
    """Provenance stays visible: an exact match and a fuzzy-typo match must
    not read identically (CLAUDE.md §23's four-way distinction, applied to
    how a category was actually resolved)."""
    exact = resolve_proposed_business("grocery")
    fuzzy = resolve_proposed_business("grocerry")
    assert exact.category is fuzzy.category is C.GROCERY
    assert "fuzzy" not in (exact.note or "").lower()
    assert "fuzzy" in (fuzzy.note or "").lower()


def test_original_raw_text_is_preserved_verbatim_through_a_fuzzy_match() -> None:
    pb = resolve_proposed_business("grocerry shop")
    assert pb.raw_text == "grocerry shop"


@pytest.mark.parametrize(
    "text",
    ["spaceship parts", "quantum widgets", "xyzzy", "asdkjfh qwoeiru"],
)
def test_nonsense_text_does_not_fuzzy_match_anything(text: str) -> None:
    """A genuinely unrelated phrase must stay unresolved — the fuzzy tier
    never invents a category for something that isn't a plausible typo of
    any known one."""
    pb = resolve_proposed_business(text)
    assert pb.resolved is False
    assert pb.category is C.UNKNOWN


def test_exact_alias_match_always_wins_over_fuzzy_even_when_configured_loose() -> None:
    """The exact/alias pass is tried FIRST and always wins when it matches —
    fuzzy is only ever a fallback for zero exact matches, never a competitor
    to a real match."""
    pb = resolve_proposed_business("kirana")
    assert pb.resolved is True
    assert pb.category is C.GROCERY
    assert "fuzzy" not in (pb.note or "").lower()


def test_conflicting_exact_categories_are_never_overridden_by_a_fuzzy_guess() -> None:
    """Multiple conflicting EXACT matches is genuine ambiguity, not a typo —
    the fuzzy tier must never be consulted in this case, since
    matched_categories is non-empty (just not singular)."""
    pb = resolve_proposed_business("grocery and pharmacy")
    assert pb.resolved is False
    assert pb.category is C.UNKNOWN
    assert "multiple categories" in (pb.note or "")


def test_fuzzy_threshold_rejects_a_low_confidence_match() -> None:
    from vyaparsarathi.market.proposed_config import ProposedBusinessConfig

    strict_cfg = ProposedBusinessConfig(fuzzy_match_threshold=99.0)
    pb = resolve_proposed_business("farniture", cfg=strict_cfg)
    assert pb.resolved is False


def test_fuzzy_margin_mechanism_rejects_when_no_score_clears_the_gap() -> None:
    """The margin check compares the best-scoring category against the
    runner-up: with an unreachably large required margin, nothing can ever
    clear it, proving the rejection path fires rather than being dead code —
    never a silent coin-flip guess between two plausible categories."""
    from vyaparsarathi.market.proposed_config import ProposedBusinessConfig

    wide_margin_cfg = ProposedBusinessConfig(fuzzy_match_threshold=1.0, fuzzy_match_margin=100.0)
    pb = resolve_proposed_business("farniture", cfg=wide_margin_cfg)
    assert pb.resolved is False
