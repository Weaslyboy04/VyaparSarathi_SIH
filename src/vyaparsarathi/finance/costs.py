"""Project cost and working-capital calculation (CLAUDE.md §14, §15).

Pure. These functions assume their required drivers are already resolved —
whether a plan has *any* usable project-cost line at all is a missing-evidence
question for `finance/assessment.py` to decide before calling in here; a
`ValueError` here means a caller (not a data-quality) mistake, matching
`DiscoveryService.discover`'s convention for a bad radius.

Only genuinely structural conventions (contingency %, the working-capital day
counts, the opex cushion) fall back to `FinanceConfig` defaults when a plan
does not state them — never a revenue, price, margin or cost-line figure.
Every fallback is recorded in the result's `notes`.
"""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.finance.finance_config import DEFAULT_FINANCE_CONFIG, FinanceConfig
from vyaparsarathi.finance.money import q_money, q_ratio, rupees
from vyaparsarathi.finance.results import (
    CostLineBreakdown,
    ProjectCostResult,
    WorkingCapitalResult,
)
from vyaparsarathi.models.finance import ProjectCostInput, WorkingCapitalInput


def compute_working_capital(
    working_capital: WorkingCapitalInput,
    *,
    monthly_revenue_inr: Decimal,
    monthly_cogs_inr: Decimal,
    monthly_fixed_opex_inr: Decimal,
    cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG,
) -> WorkingCapitalResult:
    notes: list[str] = []

    if working_capital.opening_inventory is not None:
        inventory_days_used = Decimal("0")
        inventory_req = q_money(rupees(working_capital.opening_inventory.value))
        notes.append(
            "opening_inventory was supplied directly and used as the inventory "
            "requirement; inventory_days was not used to derive it"
        )
    elif working_capital.inventory_days is not None:
        inventory_days_used = rupees(working_capital.inventory_days.value)
        inventory_req = q_money((monthly_cogs_inr / Decimal(30)) * inventory_days_used)
    else:
        inventory_days_used = Decimal("0")
        inventory_req = Decimal("0.00")
        notes.append(
            "no inventory holding period or opening inventory supplied; inventory "
            "requirement treated as zero"
        )

    if working_capital.receivable_days is not None:
        receivable_days = rupees(working_capital.receivable_days.value)
    else:
        receivable_days = cfg.receivable_days_default
        notes.append(
            f"no receivable period supplied; assumed {cfg.receivable_days_default} day(s) "
            "(config:finance default — cash rural retail)"
        )

    if working_capital.payable_days is not None:
        payable_days = rupees(working_capital.payable_days.value)
    else:
        payable_days = cfg.payable_days_default
        notes.append(
            f"no payable period supplied; assumed {cfg.payable_days_default} day(s) "
            "(config:finance default)"
        )

    if working_capital.opex_cushion_months is not None:
        opex_cushion_months = rupees(working_capital.opex_cushion_months.value)
    else:
        opex_cushion_months = cfg.opex_cushion_months
        notes.append(
            f"no operating-cash cushion supplied; assumed {cfg.opex_cushion_months} "
            "month(s) (config:finance default)"
        )

    receivables = q_money((monthly_revenue_inr / Decimal(30)) * receivable_days)
    payables = q_money((monthly_cogs_inr / Decimal(30)) * payable_days)
    operating_reserve = q_money(monthly_fixed_opex_inr * opex_cushion_months)

    gross_wc = inventory_req + receivables + operating_reserve
    net_wc = gross_wc - payables

    return WorkingCapitalResult(
        inventory_days=inventory_days_used,
        receivable_days=receivable_days,
        payable_days=payable_days,
        opex_cushion_months=opex_cushion_months,
        inventory_requirement_inr=inventory_req,
        receivables_inr=receivables,
        payables_inr=payables,
        operating_reserve_inr=operating_reserve,
        gross_working_capital_inr=q_money(gross_wc),
        net_working_capital_inr=q_money(net_wc),
        notes=notes,
    )


def compute_project_cost(
    project_cost: ProjectCostInput,
    working_capital_result: WorkingCapitalResult,
    *,
    cfg: FinanceConfig = DEFAULT_FINANCE_CONFIG,
) -> ProjectCostResult:
    if not project_cost.lines:
        raise ValueError(
            "compute_project_cost requires at least one CostLine; the caller must resolve "
            "missing-evidence cases before calling into the pure engine"
        )

    offsets_by_line: dict[str, Decimal] = {}
    for offset in project_cost.offsets:
        offsets_by_line[offset.reduces_line] = offsets_by_line.get(
            offset.reduces_line, Decimal("0")
        ) + rupees(offset.amount_avoided.value)

    notes: list[str] = []
    breakdown: list[CostLineBreakdown] = []
    capex_subtotal = Decimal("0")
    for line in project_cost.lines:
        stated = q_money(rupees(line.amount.value))
        offset_amount = q_money(offsets_by_line.pop(line.label, Decimal("0")))
        if offset_amount > stated:
            notes.append(
                f"asset offset for '{line.label}' (Rs {offset_amount}) exceeds the line's "
                f"stated cost (Rs {stated}); floored at zero"
            )
            offset_amount = stated
        net = q_money(stated - offset_amount)
        breakdown.append(
            CostLineBreakdown(
                label=line.label,
                kind=line.kind,
                stated_amount_inr=stated,
                offset_applied_inr=offset_amount,
                net_amount_inr=net,
            )
        )
        capex_subtotal += net

    for unmatched_label in offsets_by_line:
        notes.append(
            f"an asset-spend offset names '{unmatched_label}', which is not a project-cost "
            "line in this plan; the offset was not applied"
        )

    if project_cost.contingency_pct is not None:
        contingency_pct = rupees(project_cost.contingency_pct.value)
    else:
        contingency_pct = cfg.contingency_pct
        notes.append(
            f"no contingency percentage supplied; assumed {cfg.contingency_pct} of capex "
            "(config:finance default)"
        )

    contingency = q_money(capex_subtotal * contingency_pct)
    project_total = capex_subtotal + contingency + working_capital_result.net_working_capital_inr

    notes.append(
        "opening inventory is counted once, inside working capital, never also as a capex line."
    )

    return ProjectCostResult(
        lines=breakdown,
        capex_subtotal_inr=q_money(capex_subtotal),
        contingency_pct=q_ratio(contingency_pct),
        contingency_inr=contingency,
        net_working_capital_inr=working_capital_result.net_working_capital_inr,
        project_cost_inr=q_money(project_total),
        notes=notes,
    )


def compute_capital_gap(
    project_cost_inr: Decimal,
    *,
    promoter_cash_inr: Decimal,
    other_committed_funds_inr: Decimal,
    loan_principal_inr: Decimal,
) -> Decimal:
    """`max(0, project_cost - funding_available)`. Never negative — a surplus
    is not a "negative gap"."""
    funding_available = promoter_cash_inr + other_committed_funds_inr + loan_principal_inr
    return max(Decimal("0.00"), q_money(project_cost_inr - funding_available))


def compute_promoter_contribution_pct(
    project_cost_inr: Decimal, promoter_cash_inr: Decimal
) -> Decimal | None:
    """`None` when project cost is zero — a ratio has no meaning there."""
    if project_cost_inr <= 0:
        return None
    return q_ratio(promoter_cash_inr / project_cost_inr)
