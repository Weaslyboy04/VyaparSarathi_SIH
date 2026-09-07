"""The Phase 3 <-> Phase 4 bridge (CLAUDE.md §12, §15).

`market/opportunity_models.py::FinancialFitInput` is the seam Phase 3 already
declared and already accepts (`score_opportunities(..., financial_fit=...)`) —
its `notes` are surfaced onto the matching candidate's `warnings`; `feasible`,
`capital_gap_inr` and `required_promoter_margin_inr` were declared but never
read until now. This module is the **only** one under `finance/` allowed to
import `vyaparsarathi.market` (enforced by `tests/test_finance_purity.py`); it
does not change how Phase 3 scores anything — Phase 3's `market_opportunity`,
`asset_fit` and `experience_fit` components are untouched.
"""

from __future__ import annotations

from vyaparsarathi.finance.assessment_models import (
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
)
from vyaparsarathi.finance.money import rupees, whole_rupees
from vyaparsarathi.market.opportunity_models import FinancialFitInput
from vyaparsarathi.models.finance import InputKind

# feasible=True means "fundable and serviceable as structured". A plan that
# clears financing and debt service but still shows a cash-flow stress point
# is NOT that, even though it is not yet UNSERVICEABLE either — hence False,
# not True, for CASH_FLOW_STRESS. The full status and rung are never lost:
# they are always the first entry in `notes`.
_FEASIBLE_TRUE = frozenset(
    {FinancialFeasibilityStatus.FEASIBLE, FinancialFeasibilityStatus.FEASIBLE_WITH_STRETCH}
)
_FEASIBLE_FALSE = frozenset(
    {
        FinancialFeasibilityStatus.FINANCING_GAP,
        FinancialFeasibilityStatus.CASH_FLOW_STRESS,
        FinancialFeasibilityStatus.UNSERVICEABLE,
    }
)


def to_financial_fit(result: FinancialAssessmentResult) -> FinancialFitInput:
    if result.status in _FEASIBLE_TRUE:
        feasible: bool | None = True
    elif result.status in _FEASIBLE_FALSE:
        feasible = False
    else:  # INSUFFICIENT_FINANCIAL_EVIDENCE
        feasible = None

    capital_gap_inr = (
        whole_rupees(result.capital_gap_inr) if result.capital_gap_inr is not None else None
    )

    # Phase 4 never derives a scheme margin; this is populated only when the
    # plan carried an explicit, non-ASSUMED declared_margin_requirement (the
    # model itself already forbids ASSUMED here — see FinancingInput).
    required_promoter_margin_inr: int | None = None
    dmr = result.declared_margin_requirement
    if dmr is not None and dmr.kind in (InputKind.USER_PROVIDED, InputKind.SOURCED):
        required_promoter_margin_inr = whole_rupees(rupees(dmr.value))

    notes = [f"financial status: {result.status.value} (rung: {result.rung.value})"]
    if result.dscr is not None and result.dscr.average_annual_dscr is not None:
        notes.append(
            f"average annual DSCR: {result.dscr.average_annual_dscr} (cash-basis; no "
            "depreciation add-back, no tax modelled)"
        )
    if result.cash_flow is not None:
        notes.append(
            f"minimum cash balance: Rs {result.cash_flow.minimum_cash_balance_inr:,} in month "
            f"{result.cash_flow.minimum_cash_month}"
        )
    total = result.assumptions.core_drivers_total
    assumed = result.assumptions.counts_by_kind.get(InputKind.ASSUMED, 0)
    if total:
        notes.append(
            f"{assumed} of {total} financial inputs on this plan are configured ASSUMPTIONS, "
            "not sourced facts."
        )
    notes.append(
        "Whether any asset or cash satisfies a scheme's promoter-margin rule is NOT "
        "determined here."
    )

    return FinancialFitInput(
        category=result.category,
        feasible=feasible,
        capital_gap_inr=capital_gap_inr,
        required_promoter_margin_inr=required_promoter_margin_inr,
        notes=notes,
    )
