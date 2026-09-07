"""`FinancialInput` provenance rules (CLAUDE.md §14, §15, §22, §23). Pure &
offline: no fixture touches the network."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.models.finance import (
    AssetSpendOffset,
    FinancialInput,
    FinancingInput,
    InputKind,
    Unit,
)
from vyaparsarathi.models.profile import AssetKind

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _fi(**kw: object) -> FinancialInput:
    base = {"label": "x", "value": Decimal("1"), "unit": Unit.RATIO}
    base.update(kw)
    return FinancialInput(**base)  # type: ignore[arg-type]


# ======================================================================
# value type — no float ever
# ======================================================================


def test_financial_input_value_rejects_float() -> None:
    with pytest.raises(FinancialInputError):
        _fi(value=1.5, kind=InputKind.CALCULATED, calculated_from=("a",))


def test_financial_input_value_accepts_int_and_decimal() -> None:
    a = _fi(value=100, kind=InputKind.USER_PROVIDED, source="profile")
    b = _fi(value=Decimal("100.50"), kind=InputKind.USER_PROVIDED, source="profile")
    assert a.value == 100
    assert b.value == Decimal("100.50")


# ======================================================================
# ASSUMED — needs a rationale and a config: source
# ======================================================================


def test_assumed_requires_nonempty_rationale() -> None:
    with pytest.raises(ValueError, match="rationale"):
        _fi(kind=InputKind.ASSUMED, rationale="", source="config:finance")


def test_assumed_requires_config_prefixed_source() -> None:
    with pytest.raises(ValueError, match="config"):
        _fi(kind=InputKind.ASSUMED, rationale="a stated MVP convention", source="profile")


def test_assumed_with_rationale_and_config_source_is_valid() -> None:
    fi = _fi(
        kind=InputKind.ASSUMED, rationale="a stated MVP convention", source="config:finance v1"
    )
    assert fi.kind is InputKind.ASSUMED


# ======================================================================
# SOURCED — needs source, source_ref and retrieved_at
# ======================================================================


def test_sourced_requires_source() -> None:
    with pytest.raises(ValueError, match="source"):
        _fi(kind=InputKind.SOURCED, source_ref="s.4.2", retrieved_at=_NOW)


def test_sourced_requires_source_ref() -> None:
    with pytest.raises(ValueError, match="source_ref"):
        _fi(kind=InputKind.SOURCED, source="scheme:PMEGP", retrieved_at=_NOW)


def test_sourced_requires_retrieved_at() -> None:
    with pytest.raises(ValueError, match="retrieved_at"):
        _fi(kind=InputKind.SOURCED, source="scheme:PMEGP", source_ref="s.4.2")


def test_sourced_with_all_three_is_valid() -> None:
    fi = _fi(kind=InputKind.SOURCED, source="scheme:PMEGP", source_ref="s.4.2", retrieved_at=_NOW)
    assert fi.kind is InputKind.SOURCED


# ======================================================================
# CALCULATED — needs calculated_from, forbids source_ref
# ======================================================================


def test_calculated_requires_calculated_from() -> None:
    with pytest.raises(ValueError, match="calculated_from"):
        _fi(kind=InputKind.CALCULATED)


def test_calculated_forbids_source_ref() -> None:
    with pytest.raises(ValueError, match="source_ref"):
        _fi(kind=InputKind.CALCULATED, calculated_from=("a",), source_ref="s.1")


def test_calculated_with_calculated_from_is_valid() -> None:
    fi = _fi(kind=InputKind.CALCULATED, calculated_from=("monthly_revenue", "cogs_pct"))
    assert fi.kind is InputKind.CALCULATED


# ======================================================================
# USER_PROVIDED — must be source='profile', no confidence
# ======================================================================


def test_user_provided_requires_source_profile() -> None:
    with pytest.raises(ValueError, match="profile"):
        _fi(kind=InputKind.USER_PROVIDED, source="somewhere-else")


def test_user_provided_forbids_confidence() -> None:
    with pytest.raises(ValueError, match="confidence"):
        _fi(kind=InputKind.USER_PROVIDED, source="profile", confidence=0.9)


def test_user_provided_with_profile_source_is_valid() -> None:
    fi = _fi(kind=InputKind.USER_PROVIDED, source="profile")
    assert fi.kind is InputKind.USER_PROVIDED


# ======================================================================
# AssetSpendOffset — never ASSUMED
# ======================================================================


def test_asset_spend_offset_rejects_assumed_amount() -> None:
    assumed = _fi(kind=InputKind.ASSUMED, rationale="a guess", source="config:finance")
    with pytest.raises(ValueError, match="ASSUMED"):
        AssetSpendOffset(
            asset=AssetKind.STOREFRONT, reduces_line="shop fit-out", amount_avoided=assumed
        )


def test_asset_spend_offset_accepts_user_provided_amount() -> None:
    provided = _fi(value=80_000, unit=Unit.INR, kind=InputKind.USER_PROVIDED, source="profile")
    offset = AssetSpendOffset(
        asset=AssetKind.STOREFRONT, reduces_line="shop fit-out", amount_avoided=provided
    )
    assert offset.amount_avoided.kind is InputKind.USER_PROVIDED


# ======================================================================
# FinancingInput.declared_margin_requirement — never ASSUMED
# ======================================================================


def test_declared_margin_requirement_rejects_assumed() -> None:
    assumed = _fi(kind=InputKind.ASSUMED, rationale="a guess", source="config:finance")
    with pytest.raises(ValueError, match="ASSUMED"):
        FinancingInput(declared_margin_requirement=assumed)


def test_declared_margin_requirement_accepts_sourced() -> None:
    sourced = _fi(
        kind=InputKind.SOURCED, source="scheme:PMEGP", source_ref="s.4.2", retrieved_at=_NOW
    )
    financing = FinancingInput(declared_margin_requirement=sourced)
    assert financing.declared_margin_requirement is not None
    assert financing.declared_margin_requirement.kind is InputKind.SOURCED


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
