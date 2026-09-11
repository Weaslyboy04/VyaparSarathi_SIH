"""`scripts/phase6_chat.py`'s structured `key: value` shortcut parsing —
`_money_norm`, `_parse_input` (CLAUDE.md §25 Phase 6). Pure, offline; no
network, no session, no LLM."""

from __future__ import annotations

import scripts.phase6_chat as chat

from vyaparsarathi.conversation.session_models import SlotName
from vyaparsarathi.models.parameters import ValueNormalization


def test_money_norm_thousand_word() -> None:
    assert chat._money_norm("90 thousand") is ValueNormalization.THOUSAND_TO_INR


def test_money_norm_k_suffix() -> None:
    assert chat._money_norm("90k") is ValueNormalization.THOUSAND_TO_INR


def test_money_norm_k_suffix_with_space() -> None:
    assert chat._money_norm("90 k") is ValueNormalization.THOUSAND_TO_INR


def test_money_norm_lakh_and_lac() -> None:
    assert chat._money_norm("6.5 lakh") is ValueNormalization.LAKH_TO_INR
    assert chat._money_norm("6.5 lac") is ValueNormalization.LAKH_TO_INR


def test_money_norm_crore() -> None:
    assert chat._money_norm("1.2 crore") is ValueNormalization.CRORE_TO_INR


def test_money_norm_bare_number_is_as_stated() -> None:
    """No unit word/suffix -> as_stated; never inflated to thousands/lakhs."""
    assert chat._money_norm("90000") is ValueNormalization.AS_STATED
    assert chat._money_norm("90") is ValueNormalization.AS_STATED


def test_money_norm_does_not_false_positive_on_k_inside_a_word() -> None:
    """A word ending in 'k' that isn't a thousand-suffix (e.g. a stray 'bike')
    must not be misread as a thousand marker."""
    assert chat._money_norm("I have a bike") is ValueNormalization.AS_STATED


def test_parse_input_money_key_with_thousand() -> None:
    parsed = chat._parse_input("cash: 90 thousand")
    assert parsed.slot_updates
    update = parsed.slot_updates[0]
    assert update.slot is SlotName.LIQUID_CASH_INR
    assert update.normalization is ValueNormalization.THOUSAND_TO_INR
    assert update.value_token == "90 thousand"


def test_parse_input_plain_number_is_a_choice() -> None:
    parsed = chat._parse_input("1")
    assert parsed.selected_choice == 1
    assert parsed.slot_updates == ()


def test_parse_input_pick_n_is_a_choice() -> None:
    parsed = chat._parse_input("pick 2")
    assert parsed.selected_choice == 2
