"""`ConversationSession` -> `EntrepreneurProfile` / `FinancialPlanInput`
(CLAUDE.md §13, §14, §15, §25 Phase 6). PURE.

This is the **only** module in `conversation/` allowed to construct a
`FinancialInput` or an `EntrepreneurProfile` — enforced by
`tests/test_conversation_purity.py`'s AST scan, mirroring
`knowledge/plan_binding.py` being the only Phase 5 module allowed to do the
same. It emits `InputKind.USER_PROVIDED` **only** — never `ASSUMED` (a
missing financial driver yields `INSUFFICIENT_FINANCIAL_EVIDENCE` and a
question, never an invented number: CLAUDE.md §30) and never `SOURCED` (that
kind is reserved for `knowledge/plan_binding.py::bind_sourced_inputs`, which
alone carries a real citation)."""

from __future__ import annotations

from decimal import Decimal

from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
)
from vyaparsarathi.conversation.session_models import ConversationSession, Slot, SlotName, SlotState
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    LoanTerms,
    MoratoriumTreatment,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory


def _user_provided_input(slot: Slot, *, label: str, unit: Unit) -> FinancialInput | None:
    """`None` unless `slot` is exactly `USER_PROVIDED` — the hard rule this
    module exists to enforce (see the module docstring)."""
    if slot.state is not SlotState.USER_PROVIDED:
        return None
    value = slot.value
    if not isinstance(value, Decimal | int):
        return None  # a TEXT-kind slot value leaking in here would be a caller bug
    return FinancialInput(
        label=label, value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def build_profile(session: ConversationSession) -> EntrepreneurProfile:
    """Project conversation slots onto the existing Phase 3
    `EntrepreneurProfile` (`models/profile.py`) — never a parallel model."""
    cash = session.slot(SlotName.LIQUID_CASH_INR)
    years = session.slot(SlotName.YEARS_EXPERIENCE)
    proposed_text = session.slot(SlotName.PROPOSED_BUSINESS_TEXT)

    liquid_cash_inr = (
        int(cash.value)
        if cash.state is SlotState.USER_PROVIDED and isinstance(cash.value, Decimal | int)
        else None
    )
    years_experience = (
        int(years.value)
        if years.state is SlotState.USER_PROVIDED and isinstance(years.value, Decimal | int)
        else None
    )
    proposed_raw_text = proposed_text.value if isinstance(proposed_text.value, str) else None

    return EntrepreneurProfile(
        liquid_cash_inr=liquid_cash_inr,
        assets=set(session.assets.current.items),
        experience_categories=set(session.experience_categories.current.items),
        years_experience=years_experience,
        proposed_category=session.resolved_category,
        proposed_subtypes=list(session.resolved_subtypes),
        proposed_raw_text=proposed_raw_text,
    )


def loan_principal_input(session: ConversationSession) -> FinancialInput | None:
    """Just the `LOAN_PRINCIPAL_INR` slot as a `FinancialInput`, for
    `llm/tools.py`'s `BIND_PLAN` runner to hand to
    `knowledge/plan_binding.py::build_loan_terms` when the user gave an
    amount but not (yet) a full rate/tenure/moratorium — `build_plan_input`
    below only assembles a `LoanTerms` when all four are already stated."""
    return _user_provided_input(
        session.slot(SlotName.LOAN_PRINCIPAL_INR), label="loan_principal_inr", unit=Unit.INR
    )


def build_plan_input(
    session: ConversationSession, *, cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG
) -> FinancialPlanInput:
    """Build whatever `FinancialPlanInput` the currently-stated slots support.
    Every driver the entrepreneur has not stated is simply absent — the
    finance engine (`finance/assessment.py`) reports exactly what is missing
    via `INSUFFICIENT_FINANCIAL_EVIDENCE`; this function never fills a gap."""
    category = session.resolved_category or BusinessCategory.UNKNOWN
    profile = build_profile(session)

    project_cost_amount = _user_provided_input(
        session.slot(SlotName.PROJECT_COST_INR), label="project_cost_inr", unit=Unit.INR
    )
    lines: list[CostLine] = []
    if project_cost_amount is not None:
        lines.append(
            CostLine(label="startup cost", kind=CostLineKind.EQUIPMENT, amount=project_cost_amount)
        )

    opex_amount = _user_provided_input(
        session.slot(SlotName.FIXED_OPEX_INR), label="fixed_opex_inr", unit=Unit.INR_PER_MONTH
    )
    fixed_lines: list[OpexLine] = []
    if opex_amount is not None:
        fixed_lines.append(OpexLine(label="other fixed costs", amount=opex_amount))

    monthly_revenue = _user_provided_input(
        session.slot(SlotName.MONTHLY_REVENUE_INR),
        label="monthly_revenue_inr",
        unit=Unit.INR_PER_MONTH,
    )
    cogs_pct = _user_provided_input(
        session.slot(SlotName.COGS_PCT), label="cogs_pct", unit=Unit.RATIO
    )

    promoter_cash = _user_provided_input(
        session.slot(SlotName.PROMOTER_CASH_CONTRIBUTION_INR),
        label="promoter_cash_contribution_inr",
        unit=Unit.INR,
    )

    principal = _user_provided_input(
        session.slot(SlotName.LOAN_PRINCIPAL_INR), label="loan_principal_inr", unit=Unit.INR
    )
    rate = _user_provided_input(
        session.slot(SlotName.LOAN_INTEREST_RATE_PCT),
        label="loan_interest_rate_pct",
        unit=Unit.PERCENT_PER_ANNUM,
    )
    tenure = _user_provided_input(
        session.slot(SlotName.LOAN_TENURE_MONTHS), label="loan_tenure_months", unit=Unit.MONTHS
    )
    moratorium = _user_provided_input(
        session.slot(SlotName.LOAN_MORATORIUM_MONTHS),
        label="loan_moratorium_months",
        unit=Unit.MONTHS,
    )

    loan: LoanTerms | None = None
    if principal is not None and rate is not None and tenure is not None and moratorium is not None:
        treatment = (
            MoratoriumTreatment.NONE
            if moratorium.value == 0
            else MoratoriumTreatment(cfg.default_moratorium_treatment)
        )
        loan = LoanTerms(
            principal_requested=principal,
            interest_rate_pct=rate,
            tenure_months=tenure,
            moratorium_months=moratorium,
            moratorium_treatment=treatment,
        )

    return FinancialPlanInput(
        category=category,
        profile=profile,
        project_cost=ProjectCostInput(lines=lines),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(monthly_revenue=monthly_revenue),
        operating_costs=OperatingCostInput(cogs_pct=cogs_pct, fixed_lines=fixed_lines),
        financing=FinancingInput(promoter_cash_contribution=promoter_cash, loan=loan),
        horizon_months=cfg.default_horizon_months,
    )


__all__ = ["build_plan_input", "build_profile", "loan_principal_input"]
