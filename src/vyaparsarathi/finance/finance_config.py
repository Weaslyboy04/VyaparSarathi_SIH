"""The single configuration layer for the Phase 4 financial engine
(CLAUDE.md §15, §16).

Mirrors `market/opportunity_config.py`: a frozen Pydantic model of tunables,
echoed verbatim into every result. **Everything here is a structural modelling
convention, never a market claim.** Contingency percentage, an opex cushion in
months, a DSCR comfort threshold, a stress-test delta — these describe *how
the engine models a plan*, not *what this business will sell for*. There is
no default here (and there must never be one added) for revenue, price,
volume, gross margin, a project-cost line, an interest rate, a tenure, or a
moratorium — those are supplied per plan or the run reports
`INSUFFICIENT_FINANCIAL_EVIDENCE`.

Every value is `# [tunable]`. Money/rate values use `Decimal` throughout
(CLAUDE.md §4.2) so a config default can flow into a calculation without
reintroducing floating-point error.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

# --- stress-test scenarios ---------------------------------------------------
#
# Each scenario is a pure transform of a FinancialPlanInput, applied by
# finance/stress.py. Deltas are MVP heuristics, not derived from any market
# study — every scenario is emitted as an ASSUMED FinancialInput naming its
# delta. [tunable]


class StressScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    description: str
    revenue_multiplier: Decimal = Decimal("1")
    margin_delta_pct: Decimal = Decimal("0")  # subtracted from gross_margin_pct
    fixed_opex_multiplier: Decimal = Decimal("1")
    ramp_months_delta: int = 0
    apply_seasonality: bool = False  # "lean season": use the supplied seasonality_index


_STRESS_SCENARIOS: tuple[StressScenario, ...] = (
    StressScenario(
        name="revenue_down_20",
        description="Sales run 20% below the base-case plan.",
        revenue_multiplier=Decimal("0.80"),
    ),
    StressScenario(
        name="revenue_down_30",
        description="Sales run 30% below the base-case plan.",
        revenue_multiplier=Decimal("0.70"),
    ),
    StressScenario(
        name="margin_down_300bps",
        description="Gross margin is 3 percentage points lower than planned.",
        margin_delta_pct=Decimal("0.03"),
    ),
    StressScenario(
        name="opex_up_15",
        description="Fixed operating expenses run 15% above the base-case plan.",
        fixed_opex_multiplier=Decimal("1.15"),
    ),
    StressScenario(
        name="ramp_slower_2m",
        description="Revenue takes 2 months longer to ramp up than planned.",
        ramp_months_delta=2,
    ),
    StressScenario(
        name="lean_season",
        description=(
            "A sustained low-season stretch, per the plan's own seasonality index. "
            "Skipped (not fabricated) when no seasonality_index was supplied."
        ),
        apply_seasonality=True,
    ),
    StressScenario(
        name="combined_downside",
        description="Lower sales, higher costs and a slower ramp together.",
        revenue_multiplier=Decimal("0.75"),
        fixed_opex_multiplier=Decimal("1.10"),
        ramp_months_delta=2,
    ),
)

# --- fixed caveats (copied verbatim into every result) ----------------------
#
# Human-authored; NOT generated interpretation (that is Phase 6). [decision]
_CAVEATS: tuple[str, ...] = (
    "This is arithmetic on the figures supplied for this plan, most of them stated "
    "assumptions rather than sourced facts. It is a test of whether a stated plan "
    "holds together, not a forecast of what the business will actually earn.",
    "The financial feasibility verdict, the Phase 3 opportunity score, and the market "
    "and demand confidence figures are four separate measurements. None of them "
    "multiplies or implies another; a business can be commercially attractive and "
    "financially unfinanceable, or the reverse.",
    "Owning an asset can reduce what a project needs to spend. It does NOT value the "
    "asset in rupees and does NOT count toward a scheme's promoter-margin requirement — "
    "that is a retrieved scheme rule, decided at a later (Phase 5) stage.",
    "No depreciation and no tax are modelled anywhere in this engine. Every profit and "
    "DSCR figure here is on a cash basis; do not read them as accounting profit or as a "
    "tax return figure.",
    "This engine does not determine loan eligibility, scheme qualification, or credit "
    "approval. It structures and pressure-tests a plan; a bank's or scheme's own "
    "appraisal is a separate process this does not replace.",
)


class FinanceConfig(BaseModel):
    """Tunable parameters for the Phase 4 financial engine. Frozen; echoed into
    every result for traceability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # --- structural cost/working-capital conventions --- [tunable]
    contingency_pct: Decimal = Field(default=Decimal("0.05"))
    opex_cushion_months: Decimal = Field(default=Decimal("2"))
    receivable_days_default: Decimal = Field(default=Decimal("0"))
    payable_days_default: Decimal = Field(default=Decimal("0"))

    # --- DSCR / cash-viability thresholds --- [tunable]
    dscr_comfortable: Decimal = Field(default=Decimal("1.25"))
    dscr_unserviceable: Decimal = Field(default=Decimal("1.00"))
    min_cash_floor_inr: Decimal = Field(default=Decimal("0"))

    # --- evidence-sufficiency gate --- [tunable]
    max_assumption_share: float = Field(default=1.0, ge=0.0, le=1.0)
    max_horizon_months: int = Field(default=120, ge=1)

    # --- rate convention (echoed, never silently changed) --- [decision]
    rate_convention: str = "nominal_annual/12"

    # --- data ---
    stress_scenarios: tuple[StressScenario, ...] = _STRESS_SCENARIOS
    caveats: tuple[str, ...] = _CAVEATS


DEFAULT_FINANCE_CONFIG = FinanceConfig()
