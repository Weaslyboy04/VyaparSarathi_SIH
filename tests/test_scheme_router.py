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
