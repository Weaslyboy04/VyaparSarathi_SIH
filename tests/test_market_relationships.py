"""Phase 2A category-relationship configuration (CLAUDE.md §11, §12)."""

from __future__ import annotations

import pytest

from vyaparsarathi.market.models import Relationship
from vyaparsarathi.market.relationships import (
    CATEGORY_RELATIONSHIPS,
    SUBTYPE_CATEGORY_OVERLAPS,
    relationship_for,
    strongest,
)
from vyaparsarathi.models.taxonomy import BusinessCategory as C

DIRECT = Relationship.DIRECT
ADJ = Relationship.ADJACENT
IRR = Relationship.IRRELEVANT


@pytest.mark.parametrize(
    ("proposed", "existing", "expected"),
    [
        # proposed grocery
        (C.GROCERY, C.GROCERY, DIRECT),
        (C.GROCERY, C.GENERAL_STORE, DIRECT),  # configured high-overlap
        (C.GROCERY, C.DAIRY, ADJ),
        (C.GROCERY, C.FOOD_PROCESSING, ADJ),
        (C.GROCERY, C.AGRI_INPUT, ADJ),
        (C.GROCERY, C.PHARMACY, IRR),
        (C.GROCERY, C.TAILORING, IRR),
        (C.GROCERY, C.AUTOMOBILE_REPAIR, IRR),
        # proposed dairy
        (C.DAIRY, C.DAIRY, DIRECT),
        (C.DAIRY, C.GROCERY, ADJ),
        (C.DAIRY, C.GENERAL_STORE, ADJ),
        (C.DAIRY, C.PHARMACY, IRR),
        # proposed agri_input
        (C.AGRI_INPUT, C.AGRI_INPUT, DIRECT),
        (C.AGRI_INPUT, C.LIVESTOCK_SERVICES, ADJ),
        (C.AGRI_INPUT, C.HARDWARE, ADJ),
        (C.AGRI_INPUT, C.PHARMACY, IRR),
        (C.AGRI_INPUT, C.RESTAURANT, IRR),
        # proposed pharmacy — narrow
        (C.PHARMACY, C.PHARMACY, DIRECT),
        (C.PHARMACY, C.GROCERY, IRR),
        # proposed restaurant / food_stall are mutually direct
        (C.RESTAURANT, C.FOOD_STALL, DIRECT),
        (C.FOOD_STALL, C.RESTAURANT, DIRECT),
    ],
)
def test_relationship_for(proposed: C, existing: C, expected: Relationship) -> None:
    assert relationship_for(proposed, existing) is expected


def test_self_is_always_direct_even_without_a_table_entry() -> None:
    assert relationship_for(C.SALON, C.SALON) is DIRECT
    assert relationship_for(C.FURNITURE, C.FURNITURE) is DIRECT


def test_unlisted_pair_defaults_to_irrelevant() -> None:
    assert relationship_for(C.SALON, C.GROCERY) is IRR


def test_relationships_are_directional_not_symmetric() -> None:
    assert relationship_for(C.GROCERY, C.AGRI_INPUT) is ADJ
    assert relationship_for(C.LIVESTOCK_SERVICES, C.GROCERY) is IRR  # not in livestock's row


def test_strongest_ordering() -> None:
    assert strongest(IRR, ADJ) is ADJ
    assert strongest(ADJ, DIRECT) is DIRECT
    assert strongest(IRR, IRR) is IRR
    assert strongest(DIRECT, ADJ, IRR) is DIRECT


def test_config_tables_only_reference_real_categories() -> None:
    for proposed, row in CATEGORY_RELATIONSHIPS.items():
        assert isinstance(proposed, C)
        for existing, rel in row.items():
            assert isinstance(existing, C)
            assert rel in (DIRECT, ADJ)  # tables list only direct/adjacent
    for subtype, row in SUBTYPE_CATEGORY_OVERLAPS.items():
        assert subtype.islower()
        for existing, rel in row.items():
            assert isinstance(existing, C)
            assert rel in (DIRECT, ADJ)
