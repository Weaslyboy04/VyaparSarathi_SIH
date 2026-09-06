"""Phase 2A competitor classification (CLAUDE.md §11, STEP 5/8/9/11)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from vyaparsarathi.market import (
    analyze_competitors,
    analyze_from_discovery,
    proposed_from_category,
    resolve_proposed_business,
)
from vyaparsarathi.market.models import CompetitorAnalysisStatus, Relationship
from vyaparsarathi.models.business import BusinessHit, NormalizedBusiness
from vyaparsarathi.models.results import DiscoveryQuery, DiscoveryResult, DiscoveryStatus
from vyaparsarathi.models.taxonomy import BusinessCategory as C
from vyaparsarathi.models.taxonomy import SourceName

_NOW = datetime(2024, 5, 1, tzinfo=UTC)


def _biz(name: str, category: C, sid: str) -> NormalizedBusiness:
    return NormalizedBusiness(
        name=name,
        normalized_name=name.lower(),
        category=category,
        latitude=25.7,
        longitude=85.2,
        source=SourceName.OSM,
        source_id=sid,
        first_seen=_NOW,
        last_updated=_NOW,
        data_quality=0.8,
    )


def _discovery(
    *businesses: tuple[NormalizedBusiness, float], status: DiscoveryStatus | None = None
) -> DiscoveryResult:
    hits = [BusinessHit(business=b, distance_m=d) for b, d in businesses]
    return DiscoveryResult(
        status=status or DiscoveryStatus.OK,
        query_text="Hajipur, Vaishali, Bihar",
        category=C.GROCERY,
        requested_radius_m=5000,
        query=DiscoveryQuery(
            location_text="Hajipur",
            latitude=25.7,
            longitude=85.2,
            radius_m=5000,
            category=C.GROCERY,
        ),
        businesses=hits,
    )


# -- STEP 8 canonical example -------------------------------------------------


def test_step8_example_grocery_classification() -> None:
    discovery = _discovery(
        (_biz("Shukla Market", C.GROCERY, "node/1"), 132.0),
        (_biz("Balaji Mart", C.GROCERY, "node/2"), 203.0),
        (_biz("XYZ Pharmacy", C.PHARMACY, "node/3"), 90.0),
        (_biz("ABC Dairy", C.DAIRY, "node/4"), 500.0),
        (_biz("Grain Traders", C.FOOD_PROCESSING, "node/5"), 800.0),
    )
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))

    assert result.status is CompetitorAnalysisStatus.OK
    assert [c.business.name for c in result.direct_competitors] == ["Shukla Market", "Balaji Mart"]
    assert {c.business.name for c in result.adjacent_competitors} == {"ABC Dairy", "Grain Traders"}
    assert [c.business.name for c in result.irrelevant] == ["XYZ Pharmacy"]
    assert result.counts == {"discovered": 5, "direct": 2, "adjacent": 2, "irrelevant": 1}


def test_direct_bucket_is_sorted_by_distance() -> None:
    discovery = _discovery(
        (_biz("Far Grocery", C.GROCERY, "node/1"), 900.0),
        (_biz("Near Grocery", C.GROCERY, "node/2"), 100.0),
    )
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))
    assert [c.business.name for c in result.direct_competitors] == ["Near Grocery", "Far Grocery"]


# -- proposed dairy / agri_input -------------------------------------------


def test_proposed_dairy() -> None:
    discovery = _discovery(
        (_biz("Local Dairy", C.DAIRY, "n/1"), 50.0),
        (_biz("Kirana", C.GROCERY, "n/2"), 60.0),
        (_biz("Village General Store", C.GENERAL_STORE, "n/3"), 70.0),
        (_biz("Medical Hall", C.PHARMACY, "n/4"), 80.0),
    )
    result = analyze_competitors(discovery, proposed_from_category(C.DAIRY))
    assert [c.business.name for c in result.direct_competitors] == ["Local Dairy"]
    assert {c.business.name for c in result.adjacent_competitors} == {
        "Kirana",
        "Village General Store",
    }
    assert [c.business.name for c in result.irrelevant] == ["Medical Hall"]


def test_proposed_agri_input_has_direct_adjacent_irrelevant() -> None:
    discovery = _discovery(
        (_biz("Krishi Kendra", C.AGRI_INPUT, "n/1"), 10.0),
        (_biz("Pashu Aahar", C.LIVESTOCK_SERVICES, "n/2"), 20.0),
        (_biz("Sweet Corner", C.RESTAURANT, "n/3"), 30.0),
    )
    result = analyze_competitors(discovery, proposed_from_category(C.AGRI_INPUT))
    assert [c.business.name for c in result.direct_competitors] == ["Krishi Kendra"]
    assert [c.business.name for c in result.adjacent_competitors] == ["Pashu Aahar"]
    assert [c.business.name for c in result.irrelevant] == ["Sweet Corner"]


# -- subtypes -------------------------------------------------------------


def test_subtype_category_overlap_upgrades_relationship() -> None:
    # proposed cattle-feed shop (livestock_services + "feed"); an agri-input shop
    # is adjacent by category but the "feed" subtype makes it... still adjacent
    # (feed -> agri_input is ADJACENT). A livestock_services shop stays direct.
    discovery = _discovery(
        (_biz("Agro Center", C.AGRI_INPUT, "n/1"), 100.0),
    )
    proposed = resolve_proposed_business("cattle feed")  # -> livestock_services + ["feed"]
    result = analyze_competitors(discovery, proposed)
    # base livestock->agri_input is ADJACENT; feed subtype keeps it ADJACENT.
    assert [c.business.name for c in result.adjacent_competitors] == ["Agro Center"]

    # now with an existing agri-input shop and proposed agri_input + "feed":
    discovery2 = _discovery((_biz("Feed Depot", C.LIVESTOCK_SERVICES, "n/2"), 100.0))
    proposed2 = proposed_from_category(C.AGRI_INPUT, subtypes=["feed"])
    result2 = analyze_competitors(discovery2, proposed2)
    # base agri_input->livestock is ADJACENT; SUBTYPE feed->livestock is DIRECT -> upgraded.
    assert [c.business.name for c in result2.direct_competitors] == ["Feed Depot"]
    assert result2.direct_competitors[0].matched_subtypes == ["feed"]
    assert "feed" in result2.direct_competitors[0].reason


def test_subtype_name_reference_promotes_irrelevant_to_adjacent() -> None:
    # A pharmacy is irrelevant to a grocery, but if its name explicitly says
    # "pulses" and the proposal has that subtype, it becomes adjacent.
    discovery = _discovery((_biz("Pulses Medical Corner", C.PHARMACY, "n/1"), 40.0))
    proposed = proposed_from_category(C.GROCERY, subtypes=["pulses"])
    result = analyze_competitors(discovery, proposed)
    assert [c.business.name for c in result.adjacent_competitors] == ["Pulses Medical Corner"]
    cc = result.adjacent_competitors[0]
    assert cc.relationship is Relationship.ADJACENT
    assert cc.matched_subtypes == ["pulses"]
    assert "references 'pulses'" in cc.reason


# -- unknown / empty / degraded ------------------------------------------


def test_unknown_proposed_category_returns_clarification() -> None:
    discovery = _discovery((_biz("Some Shop", C.GROCERY, "n/1"), 10.0))
    result = analyze_competitors(discovery, resolve_proposed_business("spaceship parts"))
    assert result.status is CompetitorAnalysisStatus.UNKNOWN_CATEGORY
    assert result.direct_competitors == []
    assert result.adjacent_competitors == []
    assert result.irrelevant == []
    assert result.counts["discovered"] == 1
    assert result.warnings
    assert "specific" in result.warnings[0].lower()


def test_empty_discovery_returns_zero_competitors_without_error() -> None:
    discovery = _discovery(status=DiscoveryStatus.NO_RESULTS)
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))
    assert result.status is CompetitorAnalysisStatus.OK
    assert result.counts == {"discovered": 0, "direct": 0, "adjacent": 0, "irrelevant": 0}
    assert any("no competitors" in w.lower() for w in result.warnings)


def test_degraded_discovery_status_is_flagged() -> None:
    discovery = _discovery(
        (_biz("Kirana", C.GROCERY, "n/1"), 10.0), status=DiscoveryStatus.SOURCE_UNAVAILABLE
    )
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))
    assert result.status is CompetitorAnalysisStatus.OK
    assert any("discovery status" in w for w in result.warnings)


# -- explainability + determinism + integration ------------------------


def test_every_non_irrelevant_classification_has_a_reason() -> None:
    discovery = _discovery(
        (_biz("A", C.GROCERY, "n/1"), 1.0),
        (_biz("B", C.DAIRY, "n/2"), 2.0),
        (_biz("C", C.GENERAL_STORE, "n/3"), 3.0),
        (_biz("D", C.PHARMACY, "n/4"), 4.0),
    )
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))
    for cc in (*result.direct_competitors, *result.adjacent_competitors, *result.irrelevant):
        assert cc.reason.strip()
        assert cc.reason.endswith(".")


def test_classification_is_deterministic() -> None:
    discovery = _discovery(
        (_biz("A", C.GROCERY, "n/1"), 1.0),
        (_biz("B", C.FOOD_PROCESSING, "n/2"), 2.0),
    )
    proposed = resolve_proposed_business("pulses grocery store")
    a = analyze_competitors(discovery, proposed).model_dump()
    b = analyze_competitors(discovery, proposed).model_dump()
    assert a == b


def test_result_is_json_serializable() -> None:
    discovery = _discovery((_biz("A", C.GROCERY, "n/1"), 1.0))
    result = analyze_competitors(discovery, proposed_from_category(C.GROCERY))
    reloaded = json.loads(result.model_dump_json())
    assert reloaded["direct_competitors"][0]["relationship"] == "direct"
    assert reloaded["direct_competitors"][0]["business"]["source_id"] == "n/1"


def test_analyze_from_discovery_consumes_phase1_result_directly() -> None:
    # Phase 2A only reads the DiscoveryResult object — it never imports a source,
    # geocoder, HTTP client, or repository. This test does not mock HTTP; a real
    # network call here would fail or hang, proving the point if it passes fast.
    discovery = _discovery(
        (_biz("Kirana", C.GROCERY, "n/1"), 10.0),
        (_biz("Dairy", C.DAIRY, "n/2"), 20.0),
    )
    result = analyze_from_discovery(discovery)
    assert result.proposed.category is C.GROCERY
    assert result.counts["direct"] == 1
    assert result.counts["adjacent"] == 1


def test_phase2a_accepts_discoveryresult_without_query_object() -> None:
    # A degraded DiscoveryResult may have query=None; analyze_from_discovery must
    # fall back to the top-level category field.
    discovery = DiscoveryResult(
        status=DiscoveryStatus.NO_RESULTS,
        query_text="somewhere",
        category=C.DAIRY,
        requested_radius_m=5000,
    )
    result = analyze_from_discovery(discovery)
    assert result.proposed.category is C.DAIRY
    assert result.status is CompetitorAnalysisStatus.OK
