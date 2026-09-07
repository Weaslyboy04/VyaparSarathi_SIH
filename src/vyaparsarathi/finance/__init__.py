"""Financial engine — deterministic project cost, working capital, revenue,
operating costs, break-even, debt service, cash flow, DSCR, stress testing and
the financial feasibility verdict (CLAUDE.md §15, §16, §17).

Every engine here is pure: no network, database, clock, RNG or LLM (enforced
by ``tests/test_finance_purity.py``). Only ``finance/fit.py`` imports
``vyaparsarathi.market``, to hand a finished assessment back across the
already-declared Phase 3 seam (`FinancialFitInput`); nothing else in this
package does.

``finance/pipeline.py`` is an internal factoring, not a phase-boundary module:
both ``assessment.py`` (the base case) and ``stress.py`` (each scenario) call
its ``run_core_pipeline`` / ``decide_status`` so a stress scenario can never
diverge from the base pipeline's own logic, without ``assessment.py`` and
``stress.py`` importing each other.
"""

from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import (
    FinanceEvidenceRef,
    FinanceFinding,
    FinanceLadderRung,
    FinancialAssessmentResult,
    FinancialFeasibilityStatus,
    StressResult,
)
from vyaparsarathi.finance.cashflow import compute_cash_flow
from vyaparsarathi.finance.costs import (
    compute_capital_gap,
    compute_project_cost,
    compute_promoter_contribution_pct,
    compute_working_capital,
)
from vyaparsarathi.finance.debt import compute_debt_schedule, compute_emi
from vyaparsarathi.finance.dscr import compute_dscr
from vyaparsarathi.finance.finance_config import (
    DEFAULT_FINANCE_CONFIG,
    FinanceConfig,
    StressScenario,
)
from vyaparsarathi.finance.fit import to_financial_fit
from vyaparsarathi.finance.money import (
    MoneyINR,
    RateFrac,
    annual_pct_to_monthly_rate,
    q_money,
    q_rate,
    q_ratio,
    rupees,
    to_paise,
    whole_rupees,
)
from vyaparsarathi.finance.operations import (
    compute_break_even,
    compute_operating_costs,
    compute_revenue_schedule,
)
from vyaparsarathi.finance.results import (
    AmortisationPeriod,
    BreakEvenResult,
    CashFlowResult,
    CostLineBreakdown,
    DebtScheduleResult,
    DSCRPeriod,
    DSCRResult,
    MonthlyCashFlow,
    MonthlyOperatingCost,
    MonthlyRevenue,
    MoratoriumPeriod,
    OperatingCostResult,
    ProjectCostResult,
    RevenueScheduleResult,
    WorkingCapitalResult,
)
from vyaparsarathi.finance.stress import find_breaking_point, run_stress_scenarios

__all__ = [
    # money
    "MoneyINR",
    "RateFrac",
    "annual_pct_to_monthly_rate",
    "q_money",
    "q_rate",
    "q_ratio",
    "rupees",
    "to_paise",
    "whole_rupees",
    # config
    "DEFAULT_FINANCE_CONFIG",
    "FinanceConfig",
    "StressScenario",
    # results
    "AmortisationPeriod",
    "BreakEvenResult",
    "CashFlowResult",
    "CostLineBreakdown",
    "DSCRPeriod",
    "DSCRResult",
    "DebtScheduleResult",
    "MonthlyCashFlow",
    "MonthlyOperatingCost",
    "MonthlyRevenue",
    "MoratoriumPeriod",
    "OperatingCostResult",
    "ProjectCostResult",
    "RevenueScheduleResult",
    "WorkingCapitalResult",
    # assessment
    "FinanceEvidenceRef",
    "FinanceFinding",
    "FinanceLadderRung",
    "FinancialAssessmentResult",
    "FinancialFeasibilityStatus",
    "StressResult",
    # engine
    "assess_financials",
    "compute_break_even",
    "compute_capital_gap",
    "compute_cash_flow",
    "compute_debt_schedule",
    "compute_dscr",
    "compute_emi",
    "compute_operating_costs",
    "compute_project_cost",
    "compute_promoter_contribution_pct",
    "compute_revenue_schedule",
    "compute_working_capital",
    "find_breaking_point",
    "run_stress_scenarios",
    "to_financial_fit",
]
