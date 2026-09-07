"""Phase 3 demo scenarios as gate tests (CLAUDE.md §12).

The six fixture builders and their per-demo expectation checks live in
``scripts/phase3_demo.py`` so the PPT/video script and the gate assert exactly
the same behaviour. Each test here runs one demo through the shipped engine and
asserts (a) the shared invariants and (b) that demo's specific expectations.
"""

from __future__ import annotations

import pytest
from scripts.phase3_demo import _DEMO_CHECKS, DEMOS, check_common

from vyaparsarathi.market.opportunity import score_opportunities


@pytest.mark.parametrize("index", sorted(DEMOS))
def test_demo_matches_expected_phase3_behaviour(index: int) -> None:
    _title, _location, evidence, profile = DEMOS[index]()
    res = score_opportunities(evidence, profile)

    # determinism: a second run is byte-identical
    again = score_opportunities(evidence, profile)
    assert res.model_dump(mode="json") == again.model_dump(mode="json")

    common = check_common(res)
    specific = _DEMO_CHECKS[index](res, evidence)
    assert not common, f"demo {index} shared-invariant failures: {common}"
    assert not specific, f"demo {index} expectation failures: {specific}"


def test_demo_3_recommends_the_stronger_affordable_alternative() -> None:
    _t, _l, ev, prof = DEMOS[3]()
    res = score_opportunities(ev, prof)
    assert res.stance.value == "alternative_materially_better"
    assert res.recommended_pivot is not None
    pivot = next(c for c in res.candidates if c.category is res.recommended_pivot)
    assert pivot.evidence_sufficient and pivot.capital_fit.value in {"affordable", "stretch"}


def test_demo_4_has_a_higher_alternative_but_no_pivot() -> None:
    _t, _l, ev, prof = DEMOS[4]()
    res = score_opportunities(ev, prof)
    assert res.stance.value == "alternatives_comparable"
    assert res.recommended_pivot is None


def test_demo_5_is_no_recommendation_and_no_evidence() -> None:
    _t, _l, ev, prof = DEMOS[5]()
    res = score_opportunities(ev, prof)
    assert res.status.value == "no_evidence"
    assert res.stance.value == "no_recommendation"
    assert all(c.market_label.value == "insufficient_evidence" for c in res.candidates)


def test_demo_6_capital_gate_holds_score_and_demotes_rank() -> None:
    _t, _l, ev, prof = DEMOS[6]()
    res = score_opportunities(ev, prof)
    pharmacy = next(c for c in res.candidates if c.category.value == "pharmacy")
    assert pharmacy.capital_fit.value == "out_of_reach"
    assert pharmacy.opportunity_score == max(c.opportunity_score or 0 for c in res.candidates)
    assert pharmacy.rank == len(res.candidates)  # sorted last despite the top score
    # score unchanged once capital is no longer binding
    from vyaparsarathi.models.profile import EntrepreneurProfile
    from vyaparsarathi.models.taxonomy import BusinessCategory

    rich = score_opportunities(
        ev,
        EntrepreneurProfile(liquid_cash_inr=5_000_000, proposed_category=BusinessCategory.GROCERY),
    )
    rich_pharmacy = next(c for c in rich.candidates if c.category.value == "pharmacy")
    assert rich_pharmacy.opportunity_score == pharmacy.opportunity_score
    assert rich_pharmacy.rank == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
