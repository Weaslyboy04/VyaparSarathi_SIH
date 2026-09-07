"""The parameter specification table (CLAUDE.md §18, §30).

``PARAMETER_SPEC`` is the single place that says, for every
:class:`~vyaparsarathi.models.parameters.ParameterName`: which
:class:`~vyaparsarathi.models.finance.Unit` a resolved value must carry, and
which :class:`~vyaparsarathi.models.knowledge.SourceTier`\\ s are credible
enough to supply it. It is a hand-authored, qualitative table — the same class
of artifact as ``market/relationships.py``'s ``CATEGORY_RELATIONSHIPS`` or
``market/opportunity_config.py``'s ``_CAPITAL_BANDS`` — not a set of numeric
thresholds, so it is a plain module-level table rather than a field on
:class:`~vyaparsarathi.knowledge.knowledge_config.KnowledgeConfig`.

Two things this table exists to prevent, both concrete failure modes named in
the Phase 5 plan:

1. A `SECONDARY`-tier document (a blog post, a news article) supplying an
   interest rate or a scheme margin. ``knowledge/resolver.py`` filters any
   candidate whose tier is not in ``allowed_tiers`` for its name, before any
   precedence rule runs.
2. A resolved parameter with the wrong `Unit` silently reaching a
   `FinancialPlanInput` field that expects a different one — the concrete
   trap this phase names explicitly: a scheme margin is usually stated as a
   percentage (`Unit.RATIO`), but
   `FinancingInput.declared_margin_requirement` is consumed by
   `finance/fit.py` as **rupees** (`Unit.INR`). ``knowledge/plan_binding.py``
   checks a resolved value's unit against ``PARAMETER_SPEC[name].unit`` before
   binding and rejects a mismatch rather than coercing it.

:func:`normalize_value` is re-exported here from
`vyaparsarathi.models.parameters` for a stable, discoverable import path
(``from vyaparsarathi.knowledge.parameter_spec import normalize_value``); it
is defined in ``models/`` rather than here because a model must not import
from a higher-level package (CLAUDE.md §3.6) — the dependency runs
``knowledge -> models``, never the reverse.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.models.finance import Unit
from vyaparsarathi.models.knowledge import SourceTier
from vyaparsarathi.models.parameters import ParameterName, normalize_value

__all__ = ["ParameterSpec", "PARAMETER_SPEC", "normalize_value"]

# Tiers credible enough to state a binding financial rate, margin, ceiling or
# tenure (a scheme's own terms). Never SECONDARY, never INDUSTRY_BODY alone.
_SCHEME_TIERS: frozenset[SourceTier] = frozenset(
    {SourceTier.GOVT_PRIMARY, SourceTier.REGULATOR, SourceTier.PUBLIC_SECTOR_INSTITUTION}
)

# A statutory fee: only a government or regulator publication states one
# authoritatively.
_STATUTORY_FEE_TIERS: frozenset[SourceTier] = frozenset(
    {SourceTier.GOVT_PRIMARY, SourceTier.REGULATOR}
)

# Sector operating benchmarks: a recognised industry body's published figures
# are admissible alongside government/PSU sources, but never SECONDARY —
# CLAUDE.md §30's "do not... report counts without coverage/confidence" and
# docs/phase-4.md's refusal of uncited "typical margin" tables both apply here.
_BENCHMARK_TIERS: frozenset[SourceTier] = frozenset(
    {
        SourceTier.GOVT_PRIMARY,
        SourceTier.PUBLIC_SECTOR_INSTITUTION,
        SourceTier.INDUSTRY_BODY,
    }
)


class ParameterSpec(BaseModel):
    """The fixed contract for one `ParameterName`: its expected unit and the
    tiers allowed to supply it. Frozen — this table is not user-configurable
    (unlike `KnowledgeConfig`); loosening a tier floor or changing an expected
    unit is a code change, reviewed like any other."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ParameterName
    unit: Unit
    allowed_tiers: frozenset[SourceTier]
    is_benchmark: bool = False
    description: str = ""


PARAMETER_SPEC: dict[ParameterName, ParameterSpec] = {
    ParameterName.INTEREST_RATE_PCT: ParameterSpec(
        name=ParameterName.INTEREST_RATE_PCT,
        unit=Unit.PERCENT_PER_ANNUM,
        allowed_tiers=_SCHEME_TIERS,
        description="The scheme/loan's nominal annual interest rate.",
    ),
    ParameterName.LOAN_TENURE_MONTHS: ParameterSpec(
        name=ParameterName.LOAN_TENURE_MONTHS,
        unit=Unit.MONTHS,
        allowed_tiers=_SCHEME_TIERS,
        description="The scheme/loan's repayment tenure (post-moratorium EMI count).",
    ),
    ParameterName.MORATORIUM_MONTHS: ParameterSpec(
        name=ParameterName.MORATORIUM_MONTHS,
        unit=Unit.MONTHS,
        allowed_tiers=_SCHEME_TIERS,
        description="The scheme/loan's principal moratorium period.",
    ),
    ParameterName.PROMOTER_MARGIN_PCT: ParameterSpec(
        name=ParameterName.PROMOTER_MARGIN_PCT,
        unit=Unit.RATIO,
        allowed_tiers=_SCHEME_TIERS,
        description=(
            "The scheme's promoter-contribution requirement, as a fraction of project "
            "cost. NEVER bound into FinancingInput.declared_margin_requirement, which "
            "finance/fit.py consumes as absolute rupees (Unit.INR) — see "
            "knowledge/plan_binding.py's module docstring."
        ),
    ),
    ParameterName.LOAN_CEILING_INR: ParameterSpec(
        name=ParameterName.LOAN_CEILING_INR,
        unit=Unit.INR,
        allowed_tiers=_SCHEME_TIERS,
        description="The scheme's maximum loan amount for this band/category.",
    ),
    ParameterName.SUBSIDY_PCT: ParameterSpec(
        name=ParameterName.SUBSIDY_PCT,
        unit=Unit.RATIO,
        allowed_tiers=_SCHEME_TIERS,
        description="A scheme's capital or interest subsidy, as a fraction of project cost.",
    ),
    ParameterName.LICENCE_FEE_INR: ParameterSpec(
        name=ParameterName.LICENCE_FEE_INR,
        unit=Unit.INR,
        allowed_tiers=_STATUTORY_FEE_TIERS,
        description="A statutory licence/registration fee (FSSAI, Shops & Establishments, etc.).",
    ),
    ParameterName.SECURITY_DEPOSIT_MONTHS: ParameterSpec(
        name=ParameterName.SECURITY_DEPOSIT_MONTHS,
        unit=Unit.MONTHS,
        allowed_tiers=_SCHEME_TIERS,
        description="A scheme's required security deposit, expressed as months of rent/EMI.",
    ),
    ParameterName.GROSS_MARGIN_PCT: ParameterSpec(
        name=ParameterName.GROSS_MARGIN_PCT,
        unit=Unit.RATIO,
        allowed_tiers=_BENCHMARK_TIERS,
        is_benchmark=True,
        description="A sector operating-margin benchmark for a business category.",
    ),
    ParameterName.COGS_PCT: ParameterSpec(
        name=ParameterName.COGS_PCT,
        unit=Unit.RATIO,
        allowed_tiers=_BENCHMARK_TIERS,
        is_benchmark=True,
        description="A sector cost-of-goods-sold benchmark for a business category.",
    ),
    ParameterName.INVENTORY_DAYS: ParameterSpec(
        name=ParameterName.INVENTORY_DAYS,
        unit=Unit.DAYS,
        allowed_tiers=_BENCHMARK_TIERS,
        is_benchmark=True,
        description="A sector typical inventory-holding period for a business category.",
    ),
}
