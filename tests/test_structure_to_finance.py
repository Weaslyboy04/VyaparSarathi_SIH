"""`STRUCTURE_FINANCE` wired into the real Phase 6 step machinery (CLAUDE.md
§25 Phase 6; Tier 1). Exercises `llm/tools.py::STEP_RUNNERS` + `run_step`
directly — the same machinery `llm/orchestrator.py::run_turn` drives — rather
than re-testing `finance/structuring.py`'s own arithmetic (see
`tests/test_finance_structuring.py` for that). Fully offline; no I/O.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

import vyaparsarathi.llm.tools as tools_module
from vyaparsarathi.config.sih_scheme import SihSchemeConfig
from vyaparsarathi.conversation.artifacts import record_artifact
from vyaparsarathi.conversation.session_models import ConversationSession, StepId
from vyaparsarathi.finance.structuring_models import SchemeStructureResult, SchemeStructureStatus
from vyaparsarathi.llm.tools import RunContext, run_step
from vyaparsarathi.models.finance import (
    CostLine,
    CostLineKind,
    FinancialInput,
    FinancialPlanInput,
    FinancingInput,
    InputKind,
    OperatingCostInput,
    OpexLine,
    ProjectCostInput,
    RevenueInput,
    Unit,
    WorkingCapitalInput,
)
from vyaparsarathi.models.profile import EntrepreneurProfile
from vyaparsarathi.models.taxonomy import BusinessCategory as C


def _provided(value: object, unit: Unit) -> FinancialInput:
    return FinancialInput(
        label="x", value=value, unit=unit, kind=InputKind.USER_PROVIDED, source="profile"
    )


def _plan() -> FinancialPlanInput:
    return FinancialPlanInput(
        category=C.GROCERY,
        profile=EntrepreneurProfile(liquid_cash_inr=650_000),
        project_cost=ProjectCostInput(
            lines=[
                CostLine(
                    label="startup cost",
                    kind=CostLineKind.EQUIPMENT,
                    amount=_provided(300_000, Unit.INR),
                )
            ]
        ),
        working_capital=WorkingCapitalInput(),
        revenue=RevenueInput(monthly_revenue=_provided(40_000, Unit.INR_PER_MONTH)),
        operating_costs=OperatingCostInput(
            cogs_pct=_provided(Decimal("0.70"), Unit.RATIO),
            fixed_lines=[
                OpexLine(label="other fixed costs", amount=_provided(4_000, Unit.INR_PER_MONTH))
            ],
        ),
        financing=FinancingInput(),
        horizon_months=36,
    )


def _session_with_bound_plan() -> ConversationSession:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now, turn_index=1)
    plan = _plan()
    return record_artifact(
        session,
        StepId.BIND_PLAN,
        payload=plan.model_dump(mode="json"),
        payload_type="vyaparsarathi.models.finance.FinancialPlanInput",
        fingerprint="test-fingerprint",
        turn_index=1,
    )


def _ctx() -> RunContext:
    # STRUCTURE_FINANCE and ASSESS_FINANCE never touch discovery_service /
    # overpass_client / census / corpus / repository — untyped sentinels are
    # sufficient (RunContext is a plain @dataclass, not pydantic-validated).
    from vyaparsarathi.config import Settings

    return RunContext(
        settings=Settings(cache_enabled=False),
        discovery_service=object(),  # type: ignore[arg-type]
        overpass_client=object(),  # type: ignore[arg-type]
        census=object(),  # type: ignore[arg-type]
        corpus=object(),  # type: ignore[arg-type]
        repository=object(),  # type: ignore[arg-type]
    )


def _scheme_cfg() -> SihSchemeConfig:
    return SihSchemeConfig(
        scheme_name="Test Declared Structure",
        promoter_contribution_pct=Decimal("0.10"),
        loan_pct=Decimal("0.90"),
        interest_rate_pct=Decimal("11"),
        tenure_months=60,
        moratorium_months=6,
        rationale="SIH26091 problem statement — test fixture value.",
    )


def test_default_deployment_is_configured_and_structures_a_real_loan() -> None:
    """The shipped default (`DEFAULT_SIH_SCHEME_TABLE`, both SIH bands
    declared) must never crash the turn — STRUCTURE_FINANCE band-routes the
    fixture plan's project cost (Rs 323,000) into the Term Loan band and
    structures a real split; ASSESS_FINANCE then runs on the structured
    plan."""
    session = _session_with_bound_plan()
    ctx = _ctx()

    session = run_step(StepId.STRUCTURE_FINANCE, session, ctx, turn_index=1)
    structure_artifact = session.artifacts[StepId.STRUCTURE_FINANCE]
    structure = SchemeStructureResult.model_validate(structure_artifact.payload)
    assert structure.status is SchemeStructureStatus.STRUCTURED
    assert structure.scheme_name == "Term Loan"

    session = run_step(StepId.ASSESS_FINANCE, session, ctx, turn_index=1)
    assert StepId.ASSESS_FINANCE in session.artifacts  # completed, did not raise


def test_an_explicitly_empty_scheme_table_still_degrades_to_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that deliberately does not want Tier 1 structuring active
    (an empty table, not the shipped default) must still degrade honestly —
    STRUCTURE_FINANCE reports NOT_CONFIGURED, and ASSESS_FINANCE still runs
    (on the unstructured plan) rather than stalling."""
    monkeypatch.setattr(tools_module, "DEFAULT_SIH_SCHEME_TABLE", ())
    session = _session_with_bound_plan()
    ctx = _ctx()

    session = run_step(StepId.STRUCTURE_FINANCE, session, ctx, turn_index=1)
    structure_artifact = session.artifacts[StepId.STRUCTURE_FINANCE]
    structure = SchemeStructureResult.model_validate(structure_artifact.payload)
    assert structure.status is SchemeStructureStatus.NOT_CONFIGURED

    session = run_step(StepId.ASSESS_FINANCE, session, ctx, turn_index=1)
    assert StepId.ASSESS_FINANCE in session.artifacts  # completed, did not raise


def test_configured_scheme_structures_a_loan_that_reaches_dscr_and_emi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools_module, "DEFAULT_SIH_SCHEME_TABLE", (_scheme_cfg(),))
    session = _session_with_bound_plan()
    ctx = _ctx()

    session = run_step(StepId.STRUCTURE_FINANCE, session, ctx, turn_index=1)
    structure = SchemeStructureResult.model_validate(
        session.artifacts[StepId.STRUCTURE_FINANCE].payload
    )
    assert structure.status is SchemeStructureStatus.STRUCTURED
    assert structure.loan_terms is not None

    session = run_step(StepId.ASSESS_FINANCE, session, ctx, turn_index=1)
    assessment_payload = session.artifacts[StepId.ASSESS_FINANCE].payload
    assert assessment_payload["debt"] is not None
    assert assessment_payload["debt"]["emi_inr"] is not None
    assert assessment_payload["dscr"] is not None


def test_a_user_stated_loan_is_never_overwritten_by_the_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tools_module, "DEFAULT_SIH_SCHEME_TABLE", (_scheme_cfg(),))
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now, turn_index=1)
    from vyaparsarathi.models.finance import LoanTerms, MoratoriumTreatment

    user_loan = LoanTerms(
        principal_requested=_provided(500_000, Unit.INR),
        interest_rate_pct=_provided(Decimal("9"), Unit.PERCENT_PER_ANNUM),
        tenure_months=_provided(48, Unit.MONTHS),
        moratorium_months=_provided(0, Unit.MONTHS),
        moratorium_treatment=MoratoriumTreatment.NONE,
    )
    plan = _plan().model_copy(update={"financing": FinancingInput(loan=user_loan)})
    session = record_artifact(
        session,
        StepId.BIND_PLAN,
        payload=plan.model_dump(mode="json"),
        payload_type="vyaparsarathi.models.finance.FinancialPlanInput",
        fingerprint="test-fingerprint",
        turn_index=1,
    )
    ctx = _ctx()

    session = run_step(StepId.STRUCTURE_FINANCE, session, ctx, turn_index=1)
    session = run_step(StepId.ASSESS_FINANCE, session, ctx, turn_index=1)
    assessment_payload = session.artifacts[StepId.ASSESS_FINANCE].payload
    assert assessment_payload["debt"]["principal_inr"] == "500000.00"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
