"""Category/subtype -> AGMARKNET commodity mapping (CLAUDE.md §30: never
force-fit an irrelevant commodity onto a business category). Pure & offline.

The exact commodity name strings here still need a live-API verification
pass before being treated as final (see `tests/fixtures/agmarknet/README.md`
and the implementation plan) — these tests exercise the mapping/fallback
*logic*, not the literal string correctness of every commodity name.
"""

from __future__ import annotations

from vyaparsarathi.market.commodity_map import commodities_for
from vyaparsarathi.models.taxonomy import BusinessCategory as C

# Categories with genuinely no AGMARKNET signal — pharmacy sells manufactured
# medicine, a salon sells a service, etc. This must stay the DEFAULT outcome
# for most of the 30-member taxonomy, not the exception.
_NON_AGRI_CATEGORIES = (
    C.PHARMACY,
    C.RESTAURANT,
    C.HARDWARE,
    C.CLOTHING,
    C.MOBILE_ELECTRONICS,
    C.AUTOMOBILE_REPAIR,
    C.TAILORING,
    C.SALON,
    C.STATIONERY,
    C.FURNITURE,
    C.BUILDING_MATERIALS,
    C.SPORTS_GOODS,
    C.GYM_FITNESS,
    C.PRINTING_XEROX,
    C.COMPUTER_SERVICES,
    C.WELDING_FABRICATION,
    C.CATERING,
    C.EVENT_SERVICES,
    C.UTILITY_AGENCY,
    C.EDUCATION_SERVICES,
    C.LAUNDRY,
    C.CYCLE_REPAIR,
    C.FOOTWEAR,
    C.OTHER_TRADE,
    C.UNKNOWN,
)


def test_pulses_subtype_resolves_to_real_commodities() -> None:
    result = commodities_for(C.GROCERY, ("pulses",))
    assert result is not None
    assert len(result) > 0
    # never a duplicate within one result
    assert len(result) == len(set(result))


def test_dal_subtype_resolves_the_same_as_pulses() -> None:
    """`market/proposed.py` can populate either literal token depending on
    which match path fired; both must resolve to the same commodity set."""
    assert commodities_for(C.GROCERY, ("dal",)) == commodities_for(C.GROCERY, ("pulses",))


def test_subtype_wins_over_the_coarse_category_fallback() -> None:
    generic = commodities_for(C.GROCERY, ())
    specific = commodities_for(C.GROCERY, ("pulses",))
    assert specific != generic


def test_bare_grocery_with_no_subtype_still_has_a_coarse_fallback() -> None:
    result = commodities_for(C.GROCERY, ())
    assert result is not None
    assert len(result) > 0


def test_unrecognised_subtype_falls_back_to_category() -> None:
    assert commodities_for(C.GROCERY, ("something-nobody-mapped",)) == commodities_for(
        C.GROCERY, ()
    )


def test_every_non_agri_category_is_not_applicable() -> None:
    """The concrete proof "never force-fit" isn't just a docstring: these
    categories have no AGMARKNET signal at all, with no subtype given."""
    for cat in _NON_AGRI_CATEGORIES:
        assert commodities_for(cat, ()) is None, f"{cat} unexpectedly has a commodity mapping"


def test_no_subtype_result_ever_contains_a_duplicate() -> None:
    for cat in C:
        result = commodities_for(cat, ())
        if result is not None:
            assert len(result) == len(set(result)), f"{cat} has a duplicate commodity"
