"""`finance/capacity.py` (CLAUDE.md §12, §14, §15, §18; Tier 1 "SIH
10%/90% Financial Structuring", the SIH26091 headline calculation). Pure &
offline — the inverse direction from `test_finance_structuring.py`: margin
capital alone -> feasible project capacity -> loan, needing none of the four
core financial drivers.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.config.sih_scheme import DEFAULT_SIH_SCHEME_TABLE, SihSchemeConfig
from vyaparsarathi.finance.capacity import SchemeCapacityStatus, compute_scheme_capacity
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _cfg(**overrides: object) -> SihSchemeConfig:
    base: dict[str, object] = {
        "scheme_name": "Test Declared Structure",
        "promoter_contribution_pct": Decimal("0.10"),
        "loan_pct": Decimal("0.90"),
        "rationale": "SIH26091 problem statement — test fixture value.",
    }
    base.update(overrides)
    return SihSchemeConfig(**base)  # type: ignore[arg-type]


# --- the SIH26091 worked examples, to the rupee -----------------------------


def test_worked_example_one_lakh_margin_selects_term_loan() -> None:
    """SIH26091's own worked example: Rs 1,00,000 margin -> Rs 10,00,000
    project capacity -> Rs 9,00,000 loan, under the Term Loan band."""
    result = compute_scheme_capacity(C.GROCERY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.scheme_name == "Term Loan"
    assert result.feasible_project_cost_inr == Decimal("1000000.00")
    assert result.required_promoter_margin_inr == Decimal("100000.00")
    assert result.indicated_loan_inr == Decimal("900000.00")
    assert (
        result.required_promoter_margin_inr + result.indicated_loan_inr
        == result.feasible_project_cost_inr
    )
    assert result.loan_terms is not None
    assert result.loan_terms.interest_rate_pct.value == Decimal("8")
    assert result.loan_terms.tenure_months.value == 84
    assert result.loan_terms.moratorium_months.value == 6
    assert result.monthly_emi_inr is not None


def test_worked_example_ten_thousand_margin_selects_micro_finance() -> None:
    """A small margin (Rs 10,000) that Term Loan's floor excludes falls to
    Micro Finance instead: Rs 1,00,000 project, Rs 90,000 loan."""
    result = compute_scheme_capacity(C.GROCERY, 10_000, DEFAULT_SIH_SCHEME_TABLE)
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.scheme_name == "Micro Finance"
    assert result.feasible_project_cost_inr == Decimal("100000.00")
    assert result.required_promoter_margin_inr == Decimal("10000.00")
    assert result.indicated_loan_inr == Decimal("90000.00")
    assert result.loan_terms is not None
    assert result.loan_terms.interest_rate_pct.value == Decimal("6.5")
    assert result.loan_terms.tenure_months.value == 36
    assert result.loan_terms.moratorium_months.value == 3


# --- best-outcome selection: max() picks the larger capacity, not table order


def test_margin_at_exactly_the_band_boundary_prefers_term_loan() -> None:
    """Margin = Rs 14,000: Micro's own ceiling caps its feasible cost at Rs
    1,38,888.89 (its Rs 1.25L loan ceiling binds before its Rs 1.40L cost
    ceiling does); Term Loan's floor (Rs 1.40L) is inclusive at exactly this
    margin's Rs 1,40,000 and is not yet ceiling-capped itself, so it offers
    strictly MORE capacity. `max()` selects Term Loan even though it is
    listed second in the declared table — proof this is a real comparison,
    not a table-order tie-break."""
    result = compute_scheme_capacity(C.GROCERY, 14_000, DEFAULT_SIH_SCHEME_TABLE)
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.scheme_name == "Term Loan"
    assert result.feasible_project_cost_inr == Decimal("140000.00")


def test_margin_just_below_the_band_boundary_falls_to_micro_finance() -> None:
    """Margin = Rs 13,000: Term Loan's Rs 1.40L floor excludes the resulting
    Rs 1,30,000 feasible cost entirely (it is not a candidate at all), so
    Micro Finance is the only eligible band."""
    result = compute_scheme_capacity(C.GROCERY, 13_000, DEFAULT_SIH_SCHEME_TABLE)
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.scheme_name == "Micro Finance"
    assert result.feasible_project_cost_inr == Decimal("130000.00")


# --- degrade paths ------------------------------------------------------


def test_no_scheme_table_is_not_configured() -> None:
    result = compute_scheme_capacity(C.GROCERY, 100_000, None)
    assert result.status is SchemeCapacityStatus.NOT_CONFIGURED
    assert result.feasible_project_cost_inr is None
    assert any(f.code == "scheme_not_configured" for f in result.findings)


def test_empty_scheme_table_is_not_configured() -> None:
    result = compute_scheme_capacity(C.GROCERY, 100_000, ())
    assert result.status is SchemeCapacityStatus.NOT_CONFIGURED


def test_margin_capital_unknown_is_insufficient_evidence() -> None:
    result = compute_scheme_capacity(C.GROCERY, None, DEFAULT_SIH_SCHEME_TABLE)
    assert result.status is SchemeCapacityStatus.INSUFFICIENT_EVIDENCE
    assert result.feasible_project_cost_inr is None
    assert any(f.code == "margin_capital_unknown" for f in result.findings)


def test_margin_below_every_declared_bands_floor_is_no_eligible_scheme() -> None:
    """A synthetic table where the only declared band's floor is far above
    what this margin could ever fund (never happens with the real SIH26091
    table, whose Micro band has no floor — constructed here to exercise the
    degrade path itself)."""
    cfg = _cfg(
        min_project_cost_inr=Decimal("1000000"),
        max_project_cost_inr=Decimal("2000000"),
        promoter_contribution_pct=Decimal("0.50"),
        loan_pct=Decimal("0.50"),
    )
    result = compute_scheme_capacity(C.GROCERY, 1, (cfg,))
    assert result.status is SchemeCapacityStatus.NO_ELIGIBLE_SCHEME
    assert any(f.code == "no_eligible_scheme" for f in result.findings)


def test_zero_percent_promoter_contribution_is_skipped_not_a_crash() -> None:
    """A scheme declaring a 0% promoter margin cannot bound a feasible cost
    from margin capital alone (division by zero) — it is skipped, not a
    crash; a real declared scheme covers the same table so a valid answer
    still comes back."""
    zero_cfg = _cfg(
        scheme_name="Zero-margin (malformed for capacity purposes)",
        promoter_contribution_pct=Decimal("0"),
        loan_pct=Decimal("1"),
    )
    result = compute_scheme_capacity(C.GROCERY, 100_000, (zero_cfg, *DEFAULT_SIH_SCHEME_TABLE))
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.scheme_name != zero_cfg.scheme_name


# --- loan ceiling clipping within capacity ---------------------------------


def test_loan_ceiling_caps_the_feasible_cost_below_the_pure_margin_ratio() -> None:
    """A scheme with a max_loan_inr tighter than margin/pct alone would
    imply must cap feasible cost via the loan ceiling, not just the
    project-cost ceiling."""
    cfg = _cfg(max_loan_inr=Decimal("50000"))  # binds before the (absent) cost ceiling would
    result = compute_scheme_capacity(C.GROCERY, 100_000, (cfg,))
    assert result.status is SchemeCapacityStatus.CALCULATED
    # cost-from-margin = 1,000,000; cost-from-loan-ceiling = 50,000 / 0.90 = 55,555.56
    assert result.feasible_project_cost_inr == Decimal("55555.56")
    assert result.indicated_loan_inr <= Decimal("50000.00")


# --- no full loan terms declared --------------------------------------------


def test_scheme_declaring_only_the_split_yields_no_loan_terms_and_a_warning() -> None:
    cfg = _cfg()  # interest/tenure/moratorium all None
    result = compute_scheme_capacity(C.GROCERY, 100_000, (cfg,))
    assert result.status is SchemeCapacityStatus.CALCULATED
    assert result.loan_terms is None
    assert result.monthly_emi_inr is None
    assert result.warnings


# --- provenance / category / determinism ------------------------------------


def test_loan_terms_are_assumed_config_sourced() -> None:
    result = compute_scheme_capacity(C.GROCERY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    assert result.loan_terms is not None
    from vyaparsarathi.models.finance import InputKind

    for fi in (
        result.loan_terms.principal_requested,
        result.loan_terms.interest_rate_pct,
        result.loan_terms.tenure_months,
        result.loan_terms.moratorium_months,
    ):
        assert fi.kind is InputKind.ASSUMED
        assert fi.source == "config:sih_scheme"
        assert fi.rationale


def test_category_is_echoed_but_never_affects_the_answer() -> None:
    grocery = compute_scheme_capacity(C.GROCERY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    dairy = compute_scheme_capacity(C.DAIRY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    assert grocery.category is C.GROCERY
    assert dairy.category is C.DAIRY
    assert grocery.feasible_project_cost_inr == dairy.feasible_project_cost_inr
    assert grocery.scheme_name == dairy.scheme_name


def test_two_runs_are_byte_identical() -> None:
    a = compute_scheme_capacity(C.GROCERY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    b = compute_scheme_capacity(C.GROCERY, 100_000, DEFAULT_SIH_SCHEME_TABLE)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_a_single_config_is_accepted_the_same_as_a_one_tuple() -> None:
    cfg = _cfg()
    a = compute_scheme_capacity(C.GROCERY, 50_000, cfg)
    b = compute_scheme_capacity(C.GROCERY, 50_000, (cfg,))
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
