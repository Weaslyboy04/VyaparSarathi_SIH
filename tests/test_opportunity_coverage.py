"""Phase 3 — targeted coverage for behaviours not already pinned by
``test_opportunity_scoring.py`` / ``test_opportunity_acquisition.py`` /
``test_opportunity_demos.py``. Pure & offline; shared fixture factories are
imported from ``test_opportunity_scoring`` to avoid duplication."""

from __future__ import annotations

import pytest

from vyaparsarathi.categories.osm_query_tags import selectors_for, selectors_for_many
from vyaparsarathi.config import Settings
from vyaparsarathi.database import InMemoryBusinessRepository
from vyaparsarathi.market.assessment_models import MarketAssessmentLabel
from vyaparsarathi.market.opportunity import score_opportunities
from vyaparsarathi.market.opportunity_config import DEFAULT_OPPORTUNITY_CONFIG
from vyaparsarathi.market.opportunity_models import CapitalFit, Stance
from vyaparsarathi.models.place import PlaceCandidate
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.results import DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.sources.osm.adapter import OverpassFetch

from .test_opportunity_scoring import (
    _SHORTLIST,
    _demand,
    _discovery,
    _evidence,
    _profile,
)

_CFG = DEFAULT_OPPORTUNITY_CONFIG


# ======================================================================
# A.1  candidate generation
# ======================================================================


def test_shortlist_is_the_approved_eight_categories_in_order() -> None:
    assert _CFG.candidate_categories == (
        "grocery",
        "general_store",
        "dairy",
        "agri_input",
        "livestock_services",
        "food_processing",
        "pharmacy",
        "food_stall",
    )


def test_unknown_proposed_category_does_not_break_scoring() -> None:
    res = score_opportunities(
        _evidence(_discovery({C.GROCERY: 3}), _demand()),
        _profile(proposed_category=C.UNKNOWN),
    )
    assert res.proposed_category is None
    assert res.stance is Stance.NO_PROPOSAL_TO_COMPARE
    assert {c.category for c in res.candidates} == set(_SHORTLIST)  # full shortlist still scored
    assert len(res.candidates) == len(_SHORTLIST)


def test_ranking_is_config_order_when_every_score_ties() -> None:
    # No businesses, no population -> every candidate `insufficient_evidence`,
    # no profile -> every score == 40. Ties must resolve by category.value.
    res = score_opportunities(
        _evidence(_discovery({}), _demand(persons_each=0, settlements=0)),
        _profile(proposed_category=C.GROCERY),
    )
    scores = {c.opportunity_score for c in res.candidates}
    assert scores == {40}
    assert [c.category.value for c in res.candidates] == sorted(c.value for c in _SHORTLIST)


# ======================================================================
# A.2  union acquisition — selectors_for_many + discover(also_fetch=)
# ======================================================================


def test_selectors_for_many_single_category_equals_selectors_for() -> None:
    for cat in _SHORTLIST:
        assert selectors_for_many([cat]) == selectors_for(cat)


def test_selectors_for_many_is_order_stable_union_without_duplicates() -> None:
    union = selectors_for_many([C.GROCERY, C.GENERAL_STORE, C.DAIRY, C.GROCERY])
    assert len(union) == len(set(union))  # de-duplicated
    # grocery's selectors come first and in their original order
    assert union[: len(selectors_for(C.GROCERY))] == selectors_for(C.GROCERY)
    # every candidate selector is present
    for cat in (C.GROCERY, C.GENERAL_STORE, C.DAIRY):
        for sel in selectors_for(cat):
            assert sel in union


class _RecordingGeocoder:
    def __init__(self) -> None:
        self.calls = 0

    def geocode(self, query: str, *, limit: int = 5) -> list[PlaceCandidate]:
        self.calls += 1
        return [
            PlaceCandidate(
                display_name="Demo, Bihar",
                latitude=25.7,
                longitude=85.23,
                importance=0.5,
                district="Vaishali",
                state="Bihar",
                country="India",
            )
        ]


class _RecordingSource:
    def __init__(self) -> None:
        self.calls = 0
        self.selectors_seen: list[list[tuple[str, str]]] = []

    def fetch(self, query: object, selectors: list[tuple[str, str]]) -> OverpassFetch:
        self.calls += 1
        self.selectors_seen.append(list(selectors))
        return OverpassFetch(
            elements=[], raw_count=0, dropped_no_coordinates=0, endpoint_used="x", query_ql="x"
        )


def _service(geocoder: object, source: object, settings: Settings):
    from vyaparsarathi.discovery.service import DiscoveryService

    return DiscoveryService(geocoder, source, InMemoryBusinessRepository(), settings)  # type: ignore[arg-type]


def test_discover_without_also_fetch_is_unchanged(settings: Settings) -> None:
    src = _RecordingSource()
    svc = _service(_RecordingGeocoder(), src, settings)
    svc.discover("Demo, Bihar", C.GROCERY, 8_000)
    assert src.calls == 1
    assert src.selectors_seen[0] == selectors_for(C.GROCERY)


def test_discover_also_fetch_issues_one_union_business_request(settings: Settings) -> None:
    src = _RecordingSource()
    svc = _service(_RecordingGeocoder(), src, settings)
    result = svc.discover(
        "Demo, Bihar", C.GROCERY, 8_000, also_fetch=[C.DAIRY, C.PHARMACY, C.GROCERY]
    )
    assert src.calls == 1  # exactly ONE business Overpass request for all candidates
    assert src.selectors_seen[0] == selectors_for_many([C.GROCERY, C.DAIRY, C.PHARMACY, C.GROCERY])
    # every candidate's selectors made it into the single query
    for cat in (C.GROCERY, C.DAIRY, C.PHARMACY):
        for sel in selectors_for(cat):
            assert sel in src.selectors_seen[0]
    assert result.status in {DiscoveryStatus.OK, DiscoveryStatus.NO_RESULTS}


# ======================================================================
# A.3  per-candidate confidence beats union confidence
# ======================================================================


def test_engine_uses_per_candidate_confidence_not_union_confidence() -> None:
    # Union confidence is high (0.95) but grocery has zero relevant businesses;
    # its per-candidate confidence is 0.0, so 2D's absence rung must still fire.
    disc = _discovery({C.CLOTHING: 10}, confidence=0.95, raw_records=10)
    ev = _evidence(disc, _demand(persons_each=6_000))
    assert ev.per_category_confidence[C.GROCERY] == 0.0
    res = score_opportunities(ev, _profile(proposed_category=C.GROCERY))
    grocery = next(c for c in res.candidates if c.category is C.GROCERY)
    assert grocery.market_label is MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert grocery.coverage_confidence == 0.0


# ======================================================================
# A.4  market component == Phase 2D label points
# ======================================================================


def test_label_points_are_the_approved_values() -> None:
    assert _CFG.label_points == {
        "underserved": 85,
        "mixed": 60,
        "served": 55,
        "insufficient_evidence": 40,
        "crowded": 25,
        "thin_market": 20,
    }


@pytest.mark.parametrize(
    ("groceries", "persons_each", "settlements", "expected_label"),
    [
        (2, 6_000, 15, "underserved"),  # large catchment, low competition
        (4, 6_000, 15, "served"),  # large catchment, moderate competition
        (4, 4_000, 1, "crowded"),  # small catchment, moderate competition
        (1, 4_000, 1, "thin_market"),  # small catchment, low competition
        (2, 10_000, 1, "mixed"),  # moderate catchment, low competition
        (0, 6_000, 15, "insufficient_evidence"),  # zero relevant -> absence rung
    ],
)
def test_market_component_value_tracks_the_2d_label(
    groceries: int, persons_each: int, settlements: int, expected_label: str
) -> None:
    counts = {C.GROCERY: groceries} if groceries else {C.CLOTHING: 6}
    ev = _evidence(_discovery(counts), _demand(persons_each=persons_each, settlements=settlements))
    res = score_opportunities(ev, _profile(proposed_category=C.GROCERY))
    grocery = next(c for c in res.candidates if c.category is C.GROCERY)
    assert grocery.market_label.value == expected_label
    market = next(comp for comp in grocery.components if comp.name == "market_opportunity")
    assert market.value == float(_CFG.label_points[expected_label])


# ======================================================================
# A.5  asset fit
# ======================================================================


def _asset_value(profile: EntrepreneurProfile, category: C) -> float | None:
    # dairy always assessable here (grocery ADJACENT gives it coverage)
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(ev, profile)
    cand = next(c for c in res.candidates if c.category is category)
    comp = next(c for c in cand.components if c.name == "asset_fit")
    return comp.value


def test_asset_fit_essential_and_helpful_weighting() -> None:
    # dairy: essential {storefront, cold_storage}, helpful {vehicle}
    # own storefront + vehicle -> ess 1/2=0.5, help 1/1=1.0 -> 100*(.7*.5 + .3*1) = 65
    v = _asset_value(
        _profile(
            proposed_category=C.DAIRY,
            assets={AssetKind.STOREFRONT, AssetKind.VEHICLE},
        ),
        C.DAIRY,
    )
    assert v == pytest.approx(65.0)


def test_asset_fit_all_essential_no_helpful() -> None:
    # own both essential, no helpful -> ess 1.0, help 0.0 -> 100*.7 = 70
    v = _asset_value(
        _profile(
            proposed_category=C.DAIRY,
            assets={AssetKind.STOREFRONT, AssetKind.COLD_STORAGE},
        ),
        C.DAIRY,
    )
    assert v == pytest.approx(70.0)


def test_asset_fit_everything_relevant_owned_is_full_marks() -> None:
    v = _asset_value(
        _profile(
            proposed_category=C.DAIRY,
            assets={AssetKind.STOREFRONT, AssetKind.COLD_STORAGE, AssetKind.VEHICLE},
        ),
        C.DAIRY,
    )
    assert v == pytest.approx(100.0)


def test_asset_fit_is_none_when_no_assets_recorded() -> None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(ev, _profile(proposed_category=C.GROCERY))
    grocery = next(c for c in res.candidates if c.category is C.GROCERY)
    asset = next(c for c in grocery.components if c.name == "asset_fit")
    assert asset.available is False
    assert asset.value is None
    assert asset.unavailable_kind == "no_profile_input"


def test_no_model_field_puts_a_money_value_on_a_physical_asset() -> None:
    fields = EntrepreneurProfile.model_fields
    # `assets` is a bare set of kinds — no per-asset value carrier.
    assert fields["assets"].annotation == set[AssetKind]
    assert "inr" not in str(fields["assets"].annotation).lower()
    for name in fields:
        if name == "liquid_cash_inr":
            continue
        low = name.lower()
        assert not (("asset" in low) and any(t in low for t in ("inr", "value", "worth", "price")))
    # AssetKind carries only string identifiers
    assert all(isinstance(k.value, str) for k in AssetKind)


# ======================================================================
# A.6  experience fit
# ======================================================================


def _exp_value(experience: set[C], candidate: C) -> float | None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(
        ev, _profile(proposed_category=candidate, experience_categories=experience)
    )
    cand = next(c for c in res.candidates if c.category is candidate)
    comp = next(c for c in cand.components if c.name == "experience_fit")
    return comp.value


def test_experience_fit_exact_direct_adjacent_unrelated() -> None:
    assert _exp_value({C.GROCERY}, C.GROCERY) == 100  # same category
    assert _exp_value({C.GENERAL_STORE}, C.GROCERY) == 80  # DIRECT per relationships.py
    assert _exp_value({C.DAIRY}, C.GROCERY) == 60  # ADJACENT
    assert _exp_value({C.PHARMACY}, C.GROCERY) == 40  # unrelated


def test_experience_fit_takes_the_best_of_several() -> None:
    assert _exp_value({C.PHARMACY, C.GENERAL_STORE, C.SALON}, C.GROCERY) == 80


def test_experience_fit_missing_field_is_none() -> None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(ev, _profile(proposed_category=C.GROCERY))
    grocery = next(c for c in res.candidates if c.category is C.GROCERY)
    comp = next(c for c in grocery.components if c.name == "experience_fit")
    assert comp.value is None and comp.unavailable_kind == "no_profile_input"


def test_experience_fit_empty_set_behaves_as_not_supplied() -> None:
    # NOTE: `experience_categories` is a plain set, so "explicitly supplied but
    # empty" is indistinguishable from "never supplied" — both yield None
    # (component renormalised away), not 20. Documented in docs/phase-3.md.
    assert _exp_value(set(), C.GROCERY) is None


# ======================================================================
# A.7  score combination
# ======================================================================


def test_every_opportunity_score_is_int_0_to_100() -> None:
    res = score_opportunities(
        _evidence(_discovery({C.GROCERY: 5, C.DAIRY: 2}), _demand(persons_each=6_000)),
        _profile(
            proposed_category=C.GROCERY,
            assets={AssetKind.STOREFRONT},
            experience_categories={C.DAIRY},
            liquid_cash_inr=500_000,
        ),
    )
    for c in res.candidates:
        if c.opportunity_score is not None:
            assert isinstance(c.opportunity_score, int)
            assert 0 <= c.opportunity_score <= 100


# ======================================================================
# A.8  capital gate
# ======================================================================


def _capital_for(cash: int | None, category: C) -> CapitalFit:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(ev, _profile(proposed_category=category, liquid_cash_inr=cash))
    return next(c for c in res.candidates if c.category is category).capital_fit


def test_capital_fit_four_way_table() -> None:
    # grocery band = (150_000, 400_000)
    assert _capital_for(None, C.GROCERY) is CapitalFit.UNKNOWN
    assert _capital_for(400_000, C.GROCERY) is CapitalFit.AFFORDABLE
    assert _capital_for(1_000_000, C.GROCERY) is CapitalFit.AFFORDABLE
    assert _capital_for(250_000, C.GROCERY) is CapitalFit.STRETCH
    assert _capital_for(150_000, C.GROCERY) is CapitalFit.STRETCH  # == minimum
    assert _capital_for(100_000, C.GROCERY) is CapitalFit.OUT_OF_REACH


def test_out_of_reach_sorts_after_affordable_regardless_of_score() -> None:
    # pharmacy (underserved, high score) unaffordable; grocery (served) affordable.
    ev = _evidence(
        _discovery({C.PHARMACY: 2, C.CLOTHING: 8}), _demand(persons_each=6_000, settlements=15)
    )
    res = score_opportunities(ev, _profile(proposed_category=C.GROCERY, liquid_cash_inr=450_000))
    pharmacy = next(c for c in res.candidates if c.category is C.PHARMACY)
    assert pharmacy.capital_fit is CapitalFit.OUT_OF_REACH
    assert pharmacy.opportunity_score == max(c.opportunity_score or 0 for c in res.candidates)
    assert pharmacy.rank == len(res.candidates)  # last, despite the top score


# ======================================================================
# A.10  stance gates
# ======================================================================


def test_material_margin_is_config_driven() -> None:
    from scripts.phase3_demo import DEMOS

    _t, _l, ev, prof = DEMOS[3]()
    base = score_opportunities(ev, prof)
    assert base.stance is Stance.ALTERNATIVE_MATERIALLY_BETTER  # gap ~24 >= 8

    strict = score_opportunities(ev, prof, config=_CFG.model_copy(update={"material_margin": 50}))
    assert strict.stance is Stance.ALTERNATIVES_COMPARABLE  # 24 < 50 -> no pivot
    assert strict.recommended_pivot is None


@pytest.mark.parametrize("index", [1, 2, 3, 4, 5, 6])
def test_any_recommended_pivot_is_sufficient_and_affordable(index: int) -> None:
    from scripts.phase3_demo import DEMOS

    _t, _l, ev, prof = DEMOS[index]()
    res = score_opportunities(ev, prof)
    if res.recommended_pivot is None:
        return
    pivot = next(c for c in res.candidates if c.category is res.recommended_pivot)
    assert pivot.evidence_sufficient is True
    assert pivot.market_label is not MarketAssessmentLabel.INSUFFICIENT_EVIDENCE
    assert pivot.capability_incomplete is False
    assert pivot.capital_fit in {CapitalFit.AFFORDABLE, CapitalFit.STRETCH}


# ======================================================================
# A.11  confidence separate from score
# ======================================================================


def test_market_data_confidence_moves_without_moving_any_score() -> None:
    # Same total population (LARGE) + same competition, but different settlement
    # counts -> different demand-data confidence, identical labels & scores.
    thin = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=15_000, settlements=4))
    thick = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=5_000, settlements=12))
    prof = _profile(proposed_category=C.GROCERY, experience_categories={C.DAIRY})
    a = score_opportunities(thin, prof)
    b = score_opportunities(thick, prof)
    assert a.market_data_confidence != b.market_data_confidence
    a_scores = {c.category: c.opportunity_score for c in a.candidates}
    b_scores = {c.category: c.opportunity_score for c in b.candidates}
    a_labels = {c.category: c.market_label for c in a.candidates}
    b_labels = {c.category: c.market_label for c in b.candidates}
    assert a_scores == b_scores
    assert a_labels == b_labels


def test_profile_completeness_is_a_pure_count_of_provided_signals() -> None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    assert (
        score_opportunities(ev, _profile(proposed_category=C.GROCERY)).profile_completeness == 0.0
    )
    only_cash = score_opportunities(ev, _profile(proposed_category=C.GROCERY, liquid_cash_inr=1))
    assert only_cash.profile_completeness == pytest.approx(1 / 3, abs=1e-3)
    full = score_opportunities(
        ev,
        _profile(
            proposed_category=C.GROCERY,
            liquid_cash_inr=1,
            assets={AssetKind.STOREFRONT},
            experience_categories={C.DAIRY},
        ),
    )
    assert full.profile_completeness == pytest.approx(1.0)
    # market-data confidence is unchanged by profile completeness
    assert only_cash.market_data_confidence == full.market_data_confidence


# ======================================================================
# A.12  explainability — reasons cite real structured values
# ======================================================================


def test_component_reasons_quote_the_values_they_rest_on() -> None:
    ev = _evidence(_discovery({C.GROCERY: 4}), _demand(persons_each=6_000))
    res = score_opportunities(
        ev,
        _profile(
            proposed_category=C.DAIRY,
            assets={AssetKind.STOREFRONT, AssetKind.COLD_STORAGE},
            experience_categories={C.DAIRY},
        ),
    )
    dairy = next(c for c in res.candidates if c.category is C.DAIRY)
    by_name = {comp.name: comp for comp in dairy.components}

    assert dairy.market_label.value in by_name["market_opportunity"].reason
    assert by_name["market_opportunity"].evidence[0].value == dairy.market_label.value

    asset = by_name["asset_fit"]
    assert "storefront" in asset.reason and "cold_storage" in asset.reason
    assert f"{asset.value:.0f}/100" in asset.reason

    exp = by_name["experience_fit"]
    assert "dairy" in exp.reason and "100/100" in exp.reason

    for comp in dairy.components:
        assert comp.evidence, f"{comp.name} has no evidence references"


def test_engine_never_asserts_a_local_livestock_or_agri_fact() -> None:
    # A livestock-heavy profile must only produce claims about the USER's own
    # assets/experience — never about local livestock/agri conditions.
    ev = _evidence(
        _discovery({C.LIVESTOCK_SERVICES: 4, C.CLOTHING: 4}),
        _demand(persons_each=6_000, settlements=15),
    )
    res = score_opportunities(
        ev,
        _profile(
            proposed_category=C.LIVESTOCK_SERVICES,
            assets={AssetKind.LIVESTOCK, AssetKind.WAREHOUSE},
            experience_categories={C.LIVESTOCK_SERVICES},
        ),
    )
    blob = " ".join(
        [res.stance_reason, *res.warnings]
        + [r for c in res.candidates for r in c.reasons]
        + [c.capital_fit_reason for c in res.candidates]
    ).lower()
    for phrase in (
        "livestock activity is",
        "local livestock",
        "livestock is strong",
        "livestock is common",
        "agricultural conditions",
        "farming is strong",
        "cattle population",
    ):
        assert phrase not in blob, phrase


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
