"""`finance/scheme_router.py` (CLAUDE.md §4, §15; Tier 1). Pure & offline."""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.finance.scheme_router import SchemeRoutingStatus, route_scheme
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _cfg(**overrides: object) -> SihSchemeConfig:
    base = {
        "scheme_name": "Test Declared Structure",
        "promoter_contribution_pct": Decimal("0.10"),
        "loan_pct": Decimal("0.90"),
        "rationale": "SIH26091 problem statement — test fixture value.",
    }
    base.update(overrides)
    return SihSchemeConfig(**base)  # type: ignore[arg-type]


def test_no_config_routes_to_not_configured() -> None:
    result = route_scheme(C.GROCERY, None)
    assert result.status is SchemeRoutingStatus.NOT_CONFIGURED
    assert result.selected is None
    assert result.reasons


def test_declared_config_is_selected() -> None:
    cfg = _cfg()
    result = route_scheme(C.GROCERY, cfg)
    assert result.status is SchemeRoutingStatus.SELECTED
    assert result.selected is cfg
    assert result.reasons


def test_selection_is_category_independent_for_a_single_declared_structure() -> None:
    cfg = _cfg()
    a = route_scheme(C.GROCERY, cfg)
    b = route_scheme(C.DAIRY, cfg)
    assert a.selected == b.selected


def test_split_must_sum_to_one() -> None:
    import pytest

    with pytest.raises(ValueError, match="must equal 1"):
        _cfg(promoter_contribution_pct=Decimal("0.10"), loan_pct=Decimal("0.80"))


def test_min_cost_may_not_exceed_max_cost() -> None:
    import pytest

    with pytest.raises(ValueError, match="min_project_cost_inr"):
        _cfg(
            min_project_cost_inr=Decimal("500000"),
            max_project_cost_inr=Decimal("100000"),
        )


def test_ambiguous_construction_needs_all_of_rationale_and_split() -> None:
    # A config with no rationale is rejected at construction (pydantic
    # min_length=1) — a declared structure must always name its origin.
    import pytest
    from pydantic import ValidationError

    with pytest.raises((ValidationError, ValueError)):
        _cfg(rationale="")


# --- band routing by project cost (Tier 1: two declared SIH bands) ---------


def _micro() -> SihSchemeConfig:
    return _cfg(
        scheme_name="Micro Finance",
        max_project_cost_inr=Decimal("140000"),
        max_loan_inr=Decimal("125000"),
    )


def _term() -> SihSchemeConfig:
    return _cfg(
        scheme_name="Term Loan",
        min_project_cost_inr=Decimal("140000"),
        max_project_cost_inr=Decimal("5000000"),
        max_loan_inr=Decimal("4500000"),
    )


def test_no_project_cost_selects_the_first_declared_scheme() -> None:
    """The presence-only gate (`structure_financing`'s first call, before a
    cost is derivable): picks the first declared scheme, never NOT_CONFIGURED
    when something IS declared."""
    result = route_scheme(C.GROCERY, (_micro(), _term()))
    assert result.status is SchemeRoutingStatus.SELECTED
    assert result.selected is not None
    assert result.selected.scheme_name == "Micro Finance"


def test_project_cost_within_the_micro_band_selects_micro() -> None:
    result = route_scheme(C.GROCERY, (_micro(), _term()), project_cost_inr=Decimal("100000"))
    assert result.selected is not None
    assert result.selected.scheme_name == "Micro Finance"


def test_project_cost_within_the_term_band_selects_term() -> None:
    result = route_scheme(C.GROCERY, (_micro(), _term()), project_cost_inr=Decimal("1000000"))
    assert result.selected is not None
    assert result.selected.scheme_name == "Term Loan"


def test_project_cost_at_the_exact_boundary_selects_the_band_that_declares_it() -> None:
    """Rs 1,40,000 is Micro's inclusive ceiling AND Term's inclusive floor —
    both cover it; the first declared (table order) wins deterministically."""
    result = route_scheme(C.GROCERY, (_micro(), _term()), project_cost_inr=Decimal("140000"))
    assert result.selected is not None
    assert result.selected.scheme_name == "Micro Finance"


def test_project_cost_above_every_band_falls_to_the_closest_one() -> None:
    """Rs 60,00,000 exceeds Term's Rs 50L ceiling — no declared band covers
    it, so the nearest one (Term, the only one with an upper bound near this
    figure) is selected, letting the caller's own ceiling finding fire
    against a real, named scheme rather than silently picking nothing."""
    result = route_scheme(C.GROCERY, (_micro(), _term()), project_cost_inr=Decimal("6000000"))
    assert result.selected is not None
    assert result.selected.scheme_name == "Term Loan"


def test_project_cost_routing_is_category_independent() -> None:
    a = route_scheme(C.GROCERY, (_micro(), _term()), project_cost_inr=Decimal("1000000"))
    b = route_scheme(C.DAIRY, (_micro(), _term()), project_cost_inr=Decimal("1000000"))
    assert a.selected == b.selected


def test_a_single_config_is_accepted_the_same_as_a_one_tuple() -> None:
    cfg = _cfg()
    a = route_scheme(C.GROCERY, cfg, project_cost_inr=Decimal("50000"))
    b = route_scheme(C.GROCERY, (cfg,), project_cost_inr=Decimal("50000"))
    assert a.selected == b.selected
