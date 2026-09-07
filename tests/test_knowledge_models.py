"""Phase 5 model contracts: `models/knowledge.py` + `models/parameters.py`
(CLAUDE.md §14, §15, §18, §22, §23). Pure & offline."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.models.finance import Unit
from vyaparsarathi.models.knowledge import (
    ChunkLocator,
    Jurisdiction,
    JurisdictionLevel,
    SourceTier,
)
from vyaparsarathi.models.parameters import (
    Applicability,
    ParameterName,
    ResolutionStatus,
    SourcedParameter,
    ValueNormalization,
    normalize_value,
)
from vyaparsarathi.models.taxonomy import BusinessCategory as C

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _jurisdiction(**kw: object) -> Jurisdiction:
    base: dict[str, object] = {"level": JurisdictionLevel.STATE, "state": "Bihar"}
    base.update(kw)
    return Jurisdiction(**base)  # type: ignore[arg-type]


def _applicability(**kw: object) -> Applicability:
    base: dict[str, object] = {"jurisdiction": _jurisdiction()}
    base.update(kw)
    return Applicability(**base)  # type: ignore[arg-type]


def _parameter(**kw: object) -> SourcedParameter:
    base: dict[str, object] = {
        "parameter_id": "test-doc:interest_rate_pct:1",
        "name": ParameterName.INTEREST_RATE_PCT,
        "value": Decimal("11.5"),
        "unit": Unit.PERCENT_PER_ANNUM,
        "value_token": "11.5%",
        "normalization": ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        "evidence_quote": "The rate of interest chargeable shall be 11.5% per annum.",
        "document_id": "test-doc",
        "chunk_id": "test-doc#p1",
        "locator": ChunkLocator(page_from=1),
        "tier": SourceTier.GOVT_PRIMARY,
        "applicability": _applicability(),
        "reviewed_by": "test-reviewer",
        "reviewed_on": date(2026, 1, 1),
    }
    base.update(kw)
    return SourcedParameter(**base)  # type: ignore[arg-type]


# ======================================================================
# normalize_value — pure conversion, exact
# ======================================================================


def test_normalize_value_percent_to_ratio() -> None:
    assert normalize_value("10%", ValueNormalization.PERCENT_TO_RATIO) == Decimal("0.1")


def test_normalize_value_percent_as_annual_rate() -> None:
    assert normalize_value("11.5%", ValueNormalization.PERCENT_AS_ANNUAL_RATE) == Decimal("11.5")


def test_normalize_value_lakh_to_inr() -> None:
    assert normalize_value("2.5 lakh", ValueNormalization.LAKH_TO_INR) == Decimal("250000")


def test_normalize_value_crore_to_inr() -> None:
    assert normalize_value("1.2 crore", ValueNormalization.CRORE_TO_INR) == Decimal("12000000")


def test_normalize_value_years_to_months() -> None:
    assert normalize_value("5 years", ValueNormalization.YEARS_TO_MONTHS) == 60


def test_normalize_value_as_stated() -> None:
    assert normalize_value("Rs. 5000", ValueNormalization.AS_STATED) == Decimal("5000")


def test_normalize_value_rejects_unparseable_token() -> None:
    with pytest.raises(FinancialInputError):
        normalize_value("no numeral here", ValueNormalization.AS_STATED)


def test_normalize_value_handles_thousands_separators() -> None:
    assert normalize_value("Rs. 1,50,000", ValueNormalization.AS_STATED) == Decimal("150000")


# ======================================================================
# SourcedParameter — value type + the two structural "never fabricate" checks
# ======================================================================


def test_sourced_parameter_rejects_float() -> None:
    with pytest.raises(FinancialInputError):
        _parameter(value=11.5)


def test_sourced_parameter_accepts_decimal_and_int() -> None:
    a = _parameter(value=Decimal("11.5"))
    assert a.value == Decimal("11.5")
    b = _parameter(
        name=ParameterName.LOAN_TENURE_MONTHS,
        value=60,
        unit=Unit.MONTHS,
        value_token="5 years",
        normalization=ValueNormalization.YEARS_TO_MONTHS,
        evidence_quote="Repayment tenure shall not exceed 5 years from disbursement.",
    )
    assert b.value == 60


def test_value_token_must_occur_in_evidence_quote() -> None:
    with pytest.raises(ValueError, match="does not occur in evidence_quote"):
        _parameter(value_token="9.9%", evidence_quote="The rate shall be 11.5% per annum.")


def test_value_must_match_its_declared_normalization() -> None:
    with pytest.raises(ValueError, match="does not match its own declared normalization"):
        _parameter(value=Decimal("999"))


def test_negative_value_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        _parameter(
            value=Decimal("-1"),
            value_token="-1%",
            evidence_quote="The rate shall be -1% per annum.",
        )


# ======================================================================
# Jurisdiction — level-shape validation
# ======================================================================


def test_jurisdiction_state_requires_a_state() -> None:
    with pytest.raises(ValueError, match="requires state"):
        Jurisdiction(level=JurisdictionLevel.STATE)


def test_jurisdiction_district_requires_state_and_district() -> None:
    with pytest.raises(ValueError, match="requires both state and district"):
        Jurisdiction(level=JurisdictionLevel.DISTRICT, state="Bihar")


def test_jurisdiction_national_must_not_carry_state() -> None:
    with pytest.raises(ValueError, match="must not carry state"):
        Jurisdiction(level=JurisdictionLevel.NATIONAL, state="Bihar")


def test_jurisdiction_national_is_valid_with_no_state_or_district() -> None:
    j = Jurisdiction(level=JurisdictionLevel.NATIONAL)
    assert j.state is None
    assert j.district is None


def test_jurisdiction_district_is_valid_with_both() -> None:
    j = Jurisdiction(level=JurisdictionLevel.DISTRICT, state="Bihar", district="Vaishali")
    assert j.district == "Vaishali"


# ======================================================================
# ChunkLocator — ASCII, stable rendering
# ======================================================================


def test_chunk_locator_as_ref_is_ascii_and_stable() -> None:
    loc = ChunkLocator(page_from=12, page_to=13, section="4.2", paragraph_index=3)
    ref = loc.as_ref()
    assert ref == "p12-13/s4.2/para3"
    assert ref.isascii()
    assert ref == loc.as_ref()  # stable across repeated calls


def test_chunk_locator_as_ref_single_page_omits_range() -> None:
    loc = ChunkLocator(page_from=5)
    assert loc.as_ref() == "p5"


def test_chunk_locator_as_ref_falls_back_when_empty() -> None:
    assert ChunkLocator().as_ref() == "loc"


def test_chunk_locator_page_to_without_page_from_is_rejected() -> None:
    with pytest.raises(ValueError, match="page_to requires page_from"):
        ChunkLocator(page_to=5)


def test_chunk_locator_page_to_before_page_from_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        ChunkLocator(page_from=10, page_to=5)


# ======================================================================
# Applicability.specificity() — the deterministic ordering key
# ======================================================================


def test_specificity_district_beats_state_beats_national() -> None:
    national = _applicability(jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL))
    state = _applicability(jurisdiction=_jurisdiction())
    district = _applicability(
        jurisdiction=Jurisdiction(
            level=JurisdictionLevel.DISTRICT, state="Bihar", district="Vaishali"
        )
    )
    assert district.specificity()[0] > state.specificity()[0] > national.specificity()[0]


def test_specificity_scheme_specific_beats_generic() -> None:
    generic = _applicability()
    specific = _applicability(scheme="mudra-shishu")
    assert specific.specificity()[1] > generic.specificity()[1]


def test_specificity_category_specific_beats_generic() -> None:
    generic = _applicability()
    specific = _applicability(categories=(C.GROCERY,))
    assert specific.specificity()[2] > generic.specificity()[2]


def test_specificity_loan_band_specific_beats_generic() -> None:
    generic = _applicability()
    banded = _applicability(min_loan_inr=Decimal("50000"), max_loan_inr=Decimal("100000"))
    assert banded.specificity()[3] > generic.specificity()[3]


def test_applicability_rejects_inverted_loan_band() -> None:
    with pytest.raises(ValueError, match="max_loan_inr must not be less than"):
        _applicability(min_loan_inr=Decimal("100000"), max_loan_inr=Decimal("50000"))


def test_applicability_rejects_inverted_effective_window() -> None:
    with pytest.raises(ValueError, match="must not precede"):
        _applicability(effective_from=date(2026, 1, 1), effective_to=date(2025, 1, 1))


# ======================================================================
# ParameterResolution — chosen only when RESOLVED
# ======================================================================


def test_resolved_status_requires_chosen() -> None:
    from vyaparsarathi.models.parameters import ParameterResolution

    with pytest.raises(ValueError, match="must carry chosen"):
        ParameterResolution(name=ParameterName.INTEREST_RATE_PCT, status=ResolutionStatus.RESOLVED)


def test_non_resolved_status_must_not_carry_chosen() -> None:
    from vyaparsarathi.models.parameters import ParameterResolution

    with pytest.raises(ValueError, match="must not carry chosen"):
        ParameterResolution(
            name=ParameterName.INTEREST_RATE_PCT,
            status=ResolutionStatus.NO_EVIDENCE,
            chosen=_parameter(),
        )


def test_resolved_status_with_chosen_is_valid() -> None:
    from vyaparsarathi.models.parameters import ParameterResolution

    res = ParameterResolution(
        name=ParameterName.INTEREST_RATE_PCT,
        status=ResolutionStatus.RESOLVED,
        chosen=_parameter(),
    )
    assert res.chosen is not None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
