"""Business-name normalization for matching (CLAUDE.md §7, §8)."""

from __future__ import annotations

import pytest

from vyaparsarathi.normalization.text import normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sharma Kirana Store", "sharma kirana store"),
        ("  SHARMA   KIRANA  ", "sharma kirana"),
        ("Sharma & Sons", "sharma and sons"),
        ("Maa Vaishno General Store", "maa vaishno general store"),
        ("R.K. Provision Stores", "r k provision stores"),
        ("The Village Shop", "village shop"),
        ("Krishnä Traders", "krishna traders"),  # transliteration of ä -> a
        ("श्री राम किराना", "shrii raam kiraanaa"),  # Devanagari -> ASCII via unidecode
        ("café coffee day", "cafe coffee day"),
        ("", ""),
        (None, ""),
        ("!!!", ""),  # nothing survives -> empty
        ("The", "the"),  # all-noise name is kept rather than emptied
    ],
)
def test_normalize_name(raw: str | None, expected: str) -> None:
    assert normalize_name(raw) == expected


def test_normalization_is_idempotent() -> None:
    once = normalize_name("Shri  Balaji &  Co.")
    assert normalize_name(once) == once


def test_distinct_names_stay_distinct() -> None:
    assert normalize_name("Sharma Kirana") != normalize_name("Verma Kirana")
