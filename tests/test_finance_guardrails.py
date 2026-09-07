"""Negative invariants for the Phase 4 financial engine (CLAUDE.md §12, §13,
§14, §15, §30). Pure & offline — mirrors the guardrail style in
``tests/test_opportunity_coverage.py`` / ``tests/test_opportunity_scoring.py``.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialAssessmentResult
from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG
from vyaparsarathi.models.finance import (
    AssetSpendOffset,
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import AssetKind, EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_BANNED_KEY_SUBSTRINGS = ("score", "probability", "success", "guarantee")
_BANNED_TEXT_SUBSTRINGS = (
    "eligible",
    "qualifies",
    "sanctioned",
    "approved",
    "guaranteed",
    "will earn",
    "impossible",
    "unaffordable",
)


def _assumed(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.ASSUMED, rationale="t", source="config:t"
    )


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _plan(**overrides: object) -> FinancialPlanInput:
    base = {
        "category": C.GROCERY,
        "profile": EntrepreneurProfile(liquid_cash_inr=650_000, assets={AssetKind.STOREFRONT}),
        "project_cost": ProjectCostInput(
            lines=[
                CostLine(
                    label="fit-out",
                    kind=CostLineKind.CIVIL_WORK,
                    amount=_assumed(200_000, Unit.INR),
                )
            ],
            offsets=[
                AssetSpendOffset(
                    asset=AssetKind.STOREFRONT,
                    reduces_line="fit-out",
                    amount_avoided=_provided(150_000, Unit.INR),
                )
            ],
        ),
        "working_capital": WorkingCapitalInput(),
        "revenue": RevenueInput(monthly_revenue=_assumed(100_000, Unit.INR_PER_MONTH)),
        "operating_costs": OperatingCostInput(
            gross_margin_pct=_assumed(Decimal("0.4"), Unit.RATIO),
            fixed_lines=[OpexLine(label="rent", amount=_assumed(20_000, Unit.INR_PER_MONTH))],
        ),
        "financing": FinancingInput(promoter_cash_contribution=_provided(300_000, Unit.INR)),
        "horizon_months": 12,
    }
    base.update(overrides)
    return FinancialPlanInput(**base)  # type: ignore[arg-type]


def _all_keys(obj: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(str(k))
            keys |= _all_keys(v)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _all_keys(item)
    return keys


def _text_blob(res: FinancialAssessmentResult) -> str:
    parts: list[str] = list(res.caveats) + list(res.warnings) + [res.breaking_point]
    parts += [f.message for f in res.findings]
    parts += [s.description for s in res.stress_results]
    return " ".join(parts).lower()


# ======================================================================
# 23: no physical-asset monetary valuation unless explicitly supplied
# ======================================================================


def test_asset_spend_offset_amount_must_come_from_the_caller_not_the_engine() -> None:
    res = assess_financials(_plan())
    assert res.project_cost is not None
    fit_out = next(line for line in res.project_cost.lines if line.label == "fit-out")
    # the offset applied is EXACTLY the caller-supplied figure, never derived
    assert fit_out.offset_applied_inr == Decimal("150000.00")


def test_no_asset_kind_ever_gets_a_monetary_value_from_the_engine() -> None:
    res = assess_financials(_plan())
    dumped = res.model_dump(mode="json")
    keys = {k.lower() for k in _all_keys(dumped)}
    # "asset" never appears alongside a money-like key produced by this engine
    assert not any("asset" in k and k.endswith("_inr") for k in keys)


# ======================================================================
# 24: no scheme eligibility claims
# ======================================================================


def test_no_finding_or_caveat_claims_scheme_eligibility() -> None:
    res = assess_financials(_plan())
    blob = _text_blob(res)
    for banned in _BANNED_TEXT_SUBSTRINGS:
        assert banned not in blob, banned


def test_no_finding_or_caveat_claims_scheme_eligibility_under_financing_gap() -> None:
    res = assess_financials(
        _plan(
            project_cost=ProjectCostInput(
                lines=[
                    CostLine(
                        label="fit-out",
                        kind=CostLineKind.CIVIL_WORK,
                        amount=_assumed(5_000_000, Unit.INR),
                    )
                ]
            ),
            financing=FinancingInput(promoter_cash_contribution=_provided(1_000, Unit.INR)),
        )
    )
    blob = _text_blob(res)
    for banned in _BANNED_TEXT_SUBSTRINGS:
        assert banned not in blob, banned


def test_promoter_contribution_note_explicitly_disclaims_scheme_determination() -> None:
    res = assess_financials(_plan())
    assert "NOT determined here" in res.promoter_contribution_note


def test_required_promoter_margin_is_none_absent_an_explicit_declaration() -> None:
    from vyaparsarathi.finance.fit import to_financial_fit

    res = assess_financials(_plan())
    fit = to_financial_fit(res)
    assert fit.required_promoter_margin_inr is None


# ======================================================================
# 25: no unsupported profitability claims / no financial score
# ======================================================================


def test_no_result_key_implies_a_score_probability_or_guarantee() -> None:
    res = assess_financials(_plan())
    dumped = res.model_dump(mode="json")
    keys = {k.lower() for k in _all_keys(dumped)}
    for banned in _BANNED_KEY_SUBSTRINGS:
        assert not any(banned in k for k in keys), banned


def test_operating_profit_is_never_confused_with_ebitda_or_net_profit() -> None:
    res = assess_financials(_plan())
    dumped = res.model_dump(mode="json")
    keys = {k.lower() for k in _all_keys(dumped)}
    assert not any(k in ("ebitda", "pat", "net_profit") for k in keys)
    assert res.operating_costs is not None
    assert res.operating_costs.depreciation_modelled is False
    assert res.operating_costs.tax_modelled is False


def test_dscr_definition_explicitly_excludes_depreciation_and_tax() -> None:
    res = assess_financials(_plan())
    assert res.dscr is not None
    assert "no depreciation" in res.dscr.definition
    assert "no tax" in res.dscr.definition


# ======================================================================
# money is never a raw float anywhere in the dumped result
# ======================================================================


def _walk_values(obj: object) -> list[object]:
    values: list[object] = []
    if isinstance(obj, dict):
        for v in obj.values():
            values.extend(_walk_values(v))
    elif isinstance(obj, list):
        for item in obj:
            values.extend(_walk_values(item))
    else:
        values.append(obj)
    return values


def test_money_named_fields_never_hold_a_python_float_in_the_model() -> None:
    res = assess_financials(_plan())
    # model_dump WITHOUT mode="json" keeps Decimal as Decimal, not str -- confirm
    # no field typed for money is a float.
    raw = res.model_dump(mode="python")

    def check(obj: object, path: str = "") -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                new_path = f"{path}.{k}"
                if isinstance(k, str) and k.endswith("_inr") and v is not None:
                    assert not isinstance(v, float), f"{new_path} is a float: {v!r}"
                check(v, new_path)
        elif isinstance(obj, list):
            for item in obj:
                check(item, path)

    check(raw)


# ======================================================================
# caveats are the fixed, config-authored set
# ======================================================================


def test_caveats_are_exactly_the_configured_set() -> None:
    res = assess_financials(_plan())
    assert res.caveats == list(DEFAULT_FINANCE_CONFIG.caveats)


# ======================================================================
# determinism (repeat-run byte identity, the repo's standard invariant)
# ======================================================================


def test_repeat_runs_are_byte_identical() -> None:
    plan = _plan()
    a = assess_financials(plan)
    b = assess_financials(plan)
    assert a.model_dump(mode="json") == b.model_dump(mode="json")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
