"""Priority 5 fix from the SIH26091 judge feedback pass (CLAUDE.md §8, §30):
category-generic distribution-channel guidance (primary/secondary customer
channels, B2B potential, supply channel, single-channel risk). Every string
is a generic claim about how businesses of this category typically operate —
never a claim about a specific real supplier or buyer at this location.
Offline; pure.
"""

from __future__ import annotations

from vyaparsarathi.dpr.distribution_channels import distribution_channels_for
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def test_grocery_has_specific_generic_guidance() -> None:
    g = distribution_channels_for(C.GROCERY)
    assert g.primary_channel.strip()
    assert g.secondary_channels
    assert g.supply_channel.strip()
    assert g.single_channel_risk_note.strip()


def test_every_category_has_non_empty_fields() -> None:
    """Even an unmapped category (falling back to the generic guidance) must
    never render a blank field — CLAUDE.md §30's "surface the gap" applies
    to advisory text too."""
    for cat in C:
        g = distribution_channels_for(cat)
        assert g.primary_channel.strip()
        assert g.supply_channel.strip()
        assert g.b2b_potential.strip()
        assert g.single_channel_risk_note.strip()


def test_unmapped_category_gets_the_shared_generic_fallback() -> None:
    # OTHER_TRADE and UNKNOWN are deliberately not given tailored rows —
    # both must resolve to the exact same fallback object, not two
    # independently-drifting "generic" guesses.
    assert distribution_channels_for(C.OTHER_TRADE) is distribution_channels_for(C.UNKNOWN)


def test_a_mapped_category_differs_from_the_generic_fallback() -> None:
    assert distribution_channels_for(C.GROCERY) != distribution_channels_for(C.UNKNOWN)


def test_guidance_never_names_a_specific_real_entity() -> None:
    """A loose heuristic guard (not full NLP): this content must stay
    category-generic, never naming a specific real company/supplier that
    would read as a discovered fact about this location."""
    banned_substrings = ("Pvt Ltd", "Limited", "Corporation", "Inc.")
    for cat in C:
        g = distribution_channels_for(cat)
        text = " ".join(
            (g.primary_channel, *g.secondary_channels, g.b2b_potential, g.supply_channel)
        )
        for bad in banned_substrings:
            assert bad not in text
