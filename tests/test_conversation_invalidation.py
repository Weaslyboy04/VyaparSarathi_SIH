"""`conversation/artifacts.py` — fingerprinting + cascade invalidation
(CLAUDE.md §25 Phase 6). PURE; both worked examples from the approved plan.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.conversation.artifacts import compute_fingerprints, invalidate
from vyaparsarathi.conversation.deltas import apply_understanding
from vyaparsarathi.conversation.session_models import (
    ConversationSession,
    SlotName,
    StepArtifact,
    StepId,
)
from vyaparsarathi.conversation.understanding import Intent, SlotUpdateInput, TurnUnderstanding
from vyaparsarathi.models.parameters import ValueNormalization

_IMPURE_STEPS = frozenset(
    {StepId.DISCOVER, StepId.DEMAND_EVIDENCE, StepId.OPPORTUNITY_EVIDENCE, StepId.FINANCE_KNOWLEDGE}
)


def _seeded_session() -> ConversationSession:
    now = datetime.now(UTC)
    session = ConversationSession(session_id="s1", created_at=now, updated_at=now)
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.PROVIDE_INFO,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.PROPOSED_BUSINESS_TEXT,
                    raw_text="a pulses grocery store",
                    value_token="a pulses grocery store",
                    normalization=ValueNormalization.AS_STATED,
                ),
                SlotUpdateInput(
                    slot=SlotName.LOCATION_TEXT,
                    raw_text="Bhagwanpur, Bihar",
                    value_token="Bhagwanpur, Bihar",
                    normalization=ValueNormalization.AS_STATED,
                ),
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="I have 6.5 lakh",
                    value_token="6.5 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
            ),
        ),
        turn_index=1,
    )
    # Simulate that every step has already run once, at this exact state.
    # Two passes to reach the fixed point: an optional dependency's
    # contribution to a fingerprint only appears once THAT dependency has an
    # artifact (see artifacts.py's module docstring on the FINANCIAL_FIT ->
    # OPPORTUNITY loop) — real execution reaches this naturally, one step at
    # a time, in true DAG order; seeding "everything at once" here needs a
    # second pass to land on the same self-consistent state.
    for _ in range(2):
        fresh = compute_fingerprints(session)
        artifacts = {
            step: StepArtifact(
                step=step, fingerprint=fp, computed_on_turn=1, payload={}, payload_type=""
            )
            for step, fp in fresh.items()
        }
        session = session.model_copy(update={"artifacts": artifacts})
    return session


def test_no_op_turn_invalidates_nothing() -> None:
    session = _seeded_session()
    _, dropped = invalidate(session)
    assert dropped == ()


def test_worked_example_a_cash_correction_touches_zero_impure_steps() -> None:
    """'actually I only have 4 lakh' — every impure (network/disk) step must
    survive; only pure downstream steps re-run."""
    session = _seeded_session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.CORRECT_SLOT,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.LIQUID_CASH_INR,
                    raw_text="actually I only have 4 lakh",
                    value_token="4 lakh",
                    normalization=ValueNormalization.LAKH_TO_INR,
                ),
            ),
        ),
        turn_index=2,
    )
    _, dropped = invalidate(session)
    assert dropped, "expected at least OPPORTUNITY and RECOMMEND to be invalidated"
    assert set(dropped) & _IMPURE_STEPS == set()  # zero network/disk steps re-run
    assert StepId.OPPORTUNITY in dropped
    assert StepId.RECOMMEND in dropped
    # DISCOVER/DEMAND_EVIDENCE/OPPORTUNITY_EVIDENCE/FINANCE_KNOWLEDGE are
    # untouched — the "zero HTTP requests, zero file reads" payoff.
    assert StepId.DISCOVER not in dropped
    assert StepId.DEMAND_EVIDENCE not in dropped
    assert StepId.OPPORTUNITY_EVIDENCE not in dropped
    assert StepId.FINANCE_KNOWLEDGE not in dropped


def test_worked_example_b_category_change_survives_demand_evidence() -> None:
    """'make it cattle feed instead' — DEMAND_EVIDENCE (category-independent)
    survives; everything category-dependent re-runs.

    SCHEME_CAPACITY also survives THIS particular invalidate() call — not
    because it is category-independent (`route_scheme` doesn't use category
    either, but its fingerprint still echoes `resolved_category` so a stale
    label eventually gets refreshed), but because it has no `required_steps`
    at all (deliberate: it must be ready from turn one, off `LIQUID_CASH_INR`
    alone, never waiting on RESOLVE_PROPOSED — see workflow.py's comment on
    its `StepSpec`). `session.resolved_category` on the session object itself
    only actually changes once RESOLVE_PROPOSED *runs*, not merely once its
    fingerprint is judged stale (which is all a bare invalidate() call, with
    no step execution, can ever observe) — so a SCHEME_CAPACITY artifact
    survives one further turn after a category correction before its
    (cosmetic-only; the money figures never depend on category) `category`
    label catches up. Real execution reaches the fixed point naturally,
    exactly like the two-pass `_seeded_session()` helper above."""
    session = _seeded_session()
    session, _ = apply_understanding(
        session,
        TurnUnderstanding(
            intent=Intent.CORRECT_SLOT,
            slot_updates=(
                SlotUpdateInput(
                    slot=SlotName.PROPOSED_BUSINESS_TEXT,
                    raw_text="make it cattle feed instead",
                    value_token="make it cattle feed instead",
                    normalization=ValueNormalization.AS_STATED,
                ),
            ),
        ),
        turn_index=2,
    )
    _, dropped = invalidate(session)
    assert StepId.DEMAND_EVIDENCE not in dropped
    assert StepId.SCHEME_CAPACITY not in dropped
    assert StepId.RESOLVE_PROPOSED in dropped
    assert (
        StepId.DISCOVER in dropped
    )  # re-runs, but hits the OSM client's own byte-identical-QL cache
    assert StepId.OPPORTUNITY_EVIDENCE in dropped
    assert StepId.OPPORTUNITY in dropped
    assert StepId.RECOMMEND in dropped
    # every step except DEMAND_EVIDENCE and SCHEME_CAPACITY (see the docstring)
    assert len(dropped) == len(StepId) - 2


def test_market_price_evidence_fingerprint_is_subtype_sensitive() -> None:
    """A correction from a bare category to a more specific subtype (e.g.
    "grocery" -> "pulses grocery") must change MARKET_PRICE_EVIDENCE's
    fingerprint even when `resolved_category` stays the same — otherwise a
    cached artifact queried for the wrong (or no) commodity would silently
    survive the correction (CLAUDE.md §30)."""
    session = _seeded_session()
    bare = compute_fingerprints(session)[StepId.MARKET_PRICE_EVIDENCE]

    with_subtype = session.model_copy(update={"resolved_subtypes": ("pulses",)})
    specific = compute_fingerprints(with_subtype)[StepId.MARKET_PRICE_EVIDENCE]

    assert bare != specific


def test_fingerprints_exclude_output_timestamps() -> None:
    """Fingerprints are pure functions of session state; calling twice with
    an unchanged session yields byte-identical fingerprints (the anti-drift
    guarantee that lets a cache actually work turn over turn)."""
    session = _seeded_session()
    a = compute_fingerprints(session)
    b = compute_fingerprints(session)
    assert a == b


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
