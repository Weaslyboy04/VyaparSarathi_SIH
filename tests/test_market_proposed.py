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
