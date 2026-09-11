"""`llm/language_guard.py` — Devanagari-numeral detection (CLAUDE.md §3.5,
§30 Phase 6 Priority 3). Pure; no I/O, no provider."""

from __future__ import annotations

import pytest

from vyaparsarathi.llm.language_guard import contains_unsupported_devanagari_numeral


def test_devanagari_digits_are_detected() -> None:
    assert contains_unsupported_devanagari_numeral("मेरे पास ५०,००० हैं") is True


def test_hindi_number_word_is_detected() -> None:
    assert contains_unsupported_devanagari_numeral("मुझे पचास हज़ार चाहिए") is True


def test_ascii_only_message_is_not_flagged() -> None:
    assert contains_unsupported_devanagari_numeral("I have 90000 rupees") is False


def test_romanized_hinglish_is_not_flagged() -> None:
    """Romanized Hindi/English code-mixing is fully supported (it reaches the
    LLM extractor like any other message) — only actual Devanagari script
    numerals trigger this guard."""
    message = "mere paas 5 lakh hain aur kirana ki dukan kholni hai"
    assert contains_unsupported_devanagari_numeral(message) is False


def test_devanagari_business_name_without_a_numeral_is_not_flagged() -> None:
    """Devanagari script itself is not the trigger — only a numeral/number-word
    that this system cannot yet safely parse."""
    assert contains_unsupported_devanagari_numeral("मुझे किराना की दुकान खोलनी है") is False


@pytest.mark.parametrize("text", ["", "   ", "hello", "12345"])
def test_ordinary_text_never_false_positives(text: str) -> None:
    assert contains_unsupported_devanagari_numeral(text) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
