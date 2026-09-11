"""`conversation/plan_builder.py` (CLAUDE.md §13, §14, §15, §25 Phase 6)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from vyaparsarathi.conversation.deltas import apply_understanding
from vyaparsarathi.conversation.plan_builder import build_plan_input, build_profile
from vyaparsarathi.conversation.session_models import ConversationSession, SlotName
from vyaparsarathi.conversation.understanding import (
    AssetUpdateInput,
    Intent,
    SlotUpdateInput,
    TurnUnderstanding,
)
from vyaparsarathi.finance.assessment import assess_financials
from vyaparsarathi.finance.assessment_models import FinancialFeasibilityStatus
from vyaparsarathi.models.finance import InputKind
from vyaparsarathi.models.parameters import ValueNormalization
from vyaparsarathi.models.profile import AssetKind


def _session() -> ConversationSession:
    now = datetime.now(UTC)
    return ConversationSession(session_id="s1", created_at=now, updated_at=now)


def test_build_profile_reads_cash_and_assets() -> None:
    session = _session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="I have 6.5 lakh",
                    value_token="6.5 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
            ),
            asset_update=AssetUpdateInput(items=(AssetKind.STOREFRONT,), raw_text="I own a shop"),
        ),
        turn_index=1,
    )
    profile = build_profile(session)
    assert profile.liquid_cash_inr == 650_000
    assert profile.assets == {AssetKind.STOREFRONT}
    assert profile.unverified is True


def test_build_plan_input_with_no_slots_yields_insufficient_evidence() -> None:
    session = _session()
    plan = build_plan_input(session)
    result = assess_financials(plan)
    assert result.status is FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE
    assert len(result.missing_core_drivers) == 4


def test_build_plan_input_emits_only_user_provided_financial_inputs() -> None:
    session = _session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.MONTHLY_REVENUE_INR,
                    raw_text="I expect 40000 a month",
                    value_token="40000",
                    normalization=ValueNormalization.AS_STATED,
                ),
                SlotUpdateInput(
                    slot=SlotName.COGS_PCT,
                    raw_text="cogs is about 60%",
                    value_token="60%",
                    normalization=ValueNormalization.PERCENT_TO_RATIO,
                ),
                SlotUpdateInput(
                    slot=SlotName.PROJECT_COST_INR,
                    raw_text="setup will cost 2 lakh",
                    value_token="2 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
                SlotUpdateInput(
                    slot=SlotName.FIXED_OPEX_INR,
                    raw_text="fixed costs are 5000 a month",
                    value_token="5000",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=1,
    )
    plan = build_plan_input(session)
    assert plan.revenue.monthly_revenue is not None
    assert plan.revenue.monthly_revenue.kind is InputKind.USER_PROVIDED
    assert plan.revenue.monthly_revenue.value == Decimal("40000")
    assert plan.operating_costs.cogs_pct.kind is InputKind.USER_PROVIDED
    assert plan.project_cost.lines[0].amount.value == 200_000
    result = assess_financials(plan)
    assert result.status is not FinancialFeasibilityStatus.INSUFFICIENT_FINANCIAL_EVIDENCE


# --- the one deliberate ASSUMED exception: promoter contribution from cash --


def test_liquid_cash_alone_is_assumed_as_the_promoter_contribution() -> None:
    """SIH26091's Available Margin Capital IS the promoter's contribution
    under the declared structure, unless a contribution is separately
    stated — the one ASSUMED exception this module makes (see its
    docstring)."""
    session = _session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="I have 1 lakh",
                    value_token="1 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
            ),
        ),
        turn_index=1,
    )
    plan = build_plan_input(session)
    contribution = plan.financing.promoter_cash_contribution
    assert contribution is not None
    assert contribution.value == 100_000
    assert contribution.kind is InputKind.ASSUMED
    assert contribution.source == "config:sih_scheme"
    assert contribution.rationale


def test_a_separately_stated_contribution_is_never_overridden_by_cash() -> None:
    session = _session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="I have 1 lakh",
                    value_token="1 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
                SlotUpdateInput(
                    slot=SlotName.PROMOTER_CASH_CONTRIBUTION_INR,
                    raw_text="I'll put in 40000 of that",
                    value_token="40000",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=1,
    )
    plan = build_plan_input(session)
    contribution = plan.financing.promoter_cash_contribution
    assert contribution is not None
    assert contribution.value == 40_000
    assert contribution.kind is InputKind.USER_PROVIDED


def test_no_liquid_cash_stated_yields_no_promoter_contribution() -> None:
    session = _session()
    plan = build_plan_input(session)
    assert plan.financing.promoter_cash_contribution is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
