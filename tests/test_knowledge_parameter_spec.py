"""`knowledge/parameter_spec.py` + `knowledge/knowledge_config.py` — the fixed
unit/tier contract and the frozen tunables (CLAUDE.md §18, §19, §22, §30).
Pure & offline."""

from __future__ import annotations

import pytest

from vyaparsarathi.knowledge.knowledge_config import DEFAULT_KNOWLEDGE_CONFIG, KnowledgeConfig
from vyaparsarathi.knowledge.parameter_spec import PARAMETER_SPEC
from vyaparsarathi.models.knowledge import SourceTier
from vyaparsarathi.models.parameters import ParameterName


def test_every_parameter_name_has_a_spec() -> None:
    missing = set(ParameterName) - set(PARAMETER_SPEC)
    assert not missing, f"ParameterName(s) with no PARAMETER_SPEC entry: {missing}"


def test_spec_key_matches_its_own_name_field() -> None:
    for name, spec in PARAMETER_SPEC.items():
        assert spec.name is name


def test_no_parameter_ever_accepts_a_secondary_tier() -> None:
    # SECONDARY commentary is never sufficient alone for a binding financial
    # fact or a sector benchmark (CLAUDE.md §30).
    for name, spec in PARAMETER_SPEC.items():
        assert SourceTier.SECONDARY not in spec.allowed_tiers, name


def test_statutory_fee_requires_a_government_or_regulator_source() -> None:
    spec = PARAMETER_SPEC[ParameterName.LICENCE_FEE_INR]
    assert spec.allowed_tiers == frozenset({SourceTier.GOVT_PRIMARY, SourceTier.REGULATOR})


def test_benchmark_parameters_are_flagged_as_benchmarks() -> None:
    for name in (
        ParameterName.GROSS_MARGIN_PCT,
        ParameterName.COGS_PCT,
        ParameterName.INVENTORY_DAYS,
    ):
        assert PARAMETER_SPEC[name].is_benchmark is True


def test_non_benchmark_parameters_are_not_flagged_as_benchmarks() -> None:
    for name in (
        ParameterName.INTEREST_RATE_PCT,
        ParameterName.LOAN_TENURE_MONTHS,
        ParameterName.MORATORIUM_MONTHS,
        ParameterName.PROMOTER_MARGIN_PCT,
        ParameterName.LOAN_CEILING_INR,
        ParameterName.SUBSIDY_PCT,
        ParameterName.LICENCE_FEE_INR,
        ParameterName.SECURITY_DEPOSIT_MONTHS,
    ):
        assert PARAMETER_SPEC[name].is_benchmark is False


def test_default_knowledge_config_tier_weight_covers_every_tier() -> None:
    missing = set(SourceTier) - set(DEFAULT_KNOWLEDGE_CONFIG.tier_weight)
    assert not missing


def test_default_knowledge_config_tier_weights_are_monotonic_with_authority() -> None:
    w = DEFAULT_KNOWLEDGE_CONFIG.tier_weight
    assert (
        w[SourceTier.GOVT_PRIMARY]
        >= w[SourceTier.REGULATOR]
        >= w[SourceTier.PUBLIC_SECTOR_INSTITUTION]
        >= w[SourceTier.INDUSTRY_BODY]
        >= w[SourceTier.SECONDARY]
    )


def test_knowledge_config_is_frozen() -> None:
    with pytest.raises(ValueError):
        DEFAULT_KNOWLEDGE_CONFIG.reference_year = 2027  # type: ignore[misc]


def test_knowledge_config_caveats_are_non_empty_strings() -> None:
    assert DEFAULT_KNOWLEDGE_CONFIG.caveats
    assert all(isinstance(c, str) and c.strip() for c in DEFAULT_KNOWLEDGE_CONFIG.caveats)


def test_knowledge_config_default_construction_matches_explicit() -> None:
    assert KnowledgeConfig() == DEFAULT_KNOWLEDGE_CONFIG


def test_agreement_bonus_cap_actually_allows_a_bonus() -> None:
    # A cap <= 1.0 would make `min(1 + agreement_bonus, agreement_bonus_cap)`
    # permanently evaluate to 1.0 — the agreement bonus would be silently
    # inert. Guards against reintroducing that bug.
    cfg = DEFAULT_KNOWLEDGE_CONFIG
    multiplier = min(1.0 + cfg.agreement_bonus, cfg.agreement_bonus_cap)
    assert multiplier > 1.0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
