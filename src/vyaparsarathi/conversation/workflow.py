"""The step DAG (CLAUDE.md §4, §25 Phase 6). PURE — names steps and their
dependency edges only; it never calls an engine. `llm/tools.py` is the only
module that executes a `StepId` (`STEP_RUNNERS`), which is why this module
can be swept by `test_conversation_purity.py` with **no** allow-list
exception: it imports nothing but `conversation.session_models`, `enum`,
`typing` and `pydantic`.

Every edge here is a **data dependency, not a judgement** (the plan's
architecture decision, §"Architecture"): `compute_competition_metrics` needs a
`CompetitorAnalysisResult`; `acquire_opportunity_evidence` needs a union
`DiscoveryResult`. There is exactly one place an LLM (or a structured channel
input) gets a say — `requested_step` on `TurnUnderstanding` — and it can only
ever **narrow** the ready set computed here, never expand it
(`test_conversation_workflow.py`'s narrowing property test).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from vyaparsarathi.conversation.session_models import SlotName, StepId


class StepSpec(BaseModel):
    """One `StepId`'s dependency declaration.

    ``required_steps`` must all have an artifact before this step is ready.
    ``optional_steps`` may or may not have one — when one that was absent
    becomes present, this step's fingerprint changes and it re-runs (this is
    how the Phase 4 -> Phase 3 "loop" falls out of ordinary cascade
    invalidation rather than being a special case; see
    `conversation/artifacts.py`). ``required_slots`` gates readiness on a
    hard, structural precondition (there is no location to discover without
    `LOCATION_TEXT`) — deliberately **not** used for financial drivers, which
    the finance engine itself reports missing via
    `FinancialAssessmentResult.missing_core_drivers`
    (`finance/assessment.py::missing_core_drivers`) rather than being gated
    here; that keeps the DAG a data-dependency graph, not a duplicate of the
    engine's own validation. ``config_kwarg`` records which of the two
    inconsistent keyword names (`config=` vs `cfg=`) the real engine
    function uses — preserved, not unified (approved plan) — and is checked
    against the actual signature via `inspect.signature` in
    `tests/test_conversation_workflow.py`.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    step: StepId
    required_steps: tuple[StepId, ...] = ()
    optional_steps: tuple[StepId, ...] = ()
    required_slots: tuple[SlotName, ...] = ()
    impure: bool = False
    config_kwarg: Literal["config", "cfg", None] = None


STEP_SPECS: dict[StepId, StepSpec] = {
    StepId.RESOLVE_PROPOSED: StepSpec(
        step=StepId.RESOLVE_PROPOSED,
        required_slots=(SlotName.PROPOSED_BUSINESS_TEXT,),
        impure=False,
    ),
    StepId.DISCOVER: StepSpec(
        step=StepId.DISCOVER,
        required_steps=(StepId.RESOLVE_PROPOSED,),
        required_slots=(SlotName.LOCATION_TEXT,),
        impure=True,
    ),
    StepId.ANALYZE: StepSpec(
        step=StepId.ANALYZE,
        required_steps=(StepId.DISCOVER, StepId.RESOLVE_PROPOSED),
        impure=False,
    ),
    StepId.METRICS: StepSpec(
        step=StepId.METRICS,
        required_steps=(StepId.ANALYZE, StepId.DISCOVER),
        impure=False,
        config_kwarg="config",
    ),
    StepId.DEMAND_EVIDENCE: StepSpec(
        step=StepId.DEMAND_EVIDENCE,
        required_steps=(StepId.DISCOVER,),
        impure=True,
    ),
    StepId.DEMAND_SIGNALS: StepSpec(
        step=StepId.DEMAND_SIGNALS,
        required_steps=(StepId.DEMAND_EVIDENCE,),
        optional_steps=(StepId.METRICS,),
        impure=False,
        config_kwarg="config",
    ),
    StepId.ASSESS_MARKET: StepSpec(
        step=StepId.ASSESS_MARKET,
        required_steps=(StepId.METRICS, StepId.DEMAND_SIGNALS),
        optional_steps=(StepId.ANALYZE,),
        impure=False,
        config_kwarg="config",
    ),
    StepId.OPPORTUNITY_EVIDENCE: StepSpec(
        step=StepId.OPPORTUNITY_EVIDENCE,
        required_steps=(StepId.DISCOVER, StepId.RESOLVE_PROPOSED, StepId.DEMAND_EVIDENCE),
        impure=True,
    ),
    StepId.OPPORTUNITY: StepSpec(
        step=StepId.OPPORTUNITY,
        required_steps=(StepId.OPPORTUNITY_EVIDENCE,),
        optional_steps=(StepId.FINANCIAL_FIT,),
        impure=False,
        config_kwarg="config",
    ),
    StepId.FINANCE_KNOWLEDGE: StepSpec(
        step=StepId.FINANCE_KNOWLEDGE,
        required_steps=(StepId.RESOLVE_PROPOSED,),
        # DISCOVER is optional: a state/district-aware query is better once
        # the location is resolved, but this step does not need to wait for
        # it — it re-runs once DISCOVER's artifact appears (ordinary cascade
        # invalidation, same mechanism as OPPORTUNITY <-> FINANCIAL_FIT).
        optional_steps=(StepId.DISCOVER,),
        impure=True,
    ),
    StepId.BUILD_PLAN: StepSpec(
        step=StepId.BUILD_PLAN,
        required_steps=(StepId.RESOLVE_PROPOSED,),
        impure=False,
    ),
    StepId.BIND_PLAN: StepSpec(
        step=StepId.BIND_PLAN,
        required_steps=(StepId.BUILD_PLAN, StepId.FINANCE_KNOWLEDGE),
        impure=False,
        config_kwarg="cfg",
    ),
    StepId.STRUCTURE_FINANCE: StepSpec(
        step=StepId.STRUCTURE_FINANCE,
        required_steps=(StepId.BIND_PLAN,),
        impure=False,
        # `finance/structuring.py::structure_financing` takes two distinct
        # configs (`scheme_cfg=`, `fin_cfg=`) — neither literally named
        # `config`/`cfg` — so this StepSpec carries neither of the two
        # inconsistent keyword names; see `config_kwarg`'s own docstring.
        config_kwarg=None,
    ),
    StepId.ASSESS_FINANCE: StepSpec(
        step=StepId.ASSESS_FINANCE,
        # Both kept: STRUCTURE_FINANCE already requires BIND_PLAN, so this
        # adds nothing to readiness, but it keeps the dependency on the
        # bound plan explicit in the fingerprint rather than implicit
        # through one extra hop (see conversation/artifacts.py).
        required_steps=(StepId.BIND_PLAN, StepId.STRUCTURE_FINANCE),
        impure=False,
        config_kwarg="cfg",
    ),
    StepId.FINANCIAL_FIT: StepSpec(
        step=StepId.FINANCIAL_FIT,
        required_steps=(StepId.ASSESS_FINANCE,),
        impure=False,
    ),
    StepId.RECOMMEND: StepSpec(
        step=StepId.RECOMMEND,
        required_steps=(StepId.OPPORTUNITY, StepId.ASSESS_FINANCE),
        optional_steps=(StepId.ASSESS_MARKET,),
        impure=False,
        config_kwarg="cfg",
    ),
    StepId.SWOT: StepSpec(
        step=StepId.SWOT,
        # Mirrors RECOMMEND's own dependency shape (required: OPPORTUNITY,
        # ASSESS_FINANCE) plus STRUCTURE_FINANCE — never computed from a
        # partial finance picture within a turn. STRUCTURE_FINANCE always
        # completes (NOT_CONFIGURED/INSUFFICIENT_EVIDENCE are results, not
        # failures), so requiring it never blocks SWOT from becoming ready.
        required_steps=(StepId.OPPORTUNITY, StepId.ASSESS_FINANCE, StepId.STRUCTURE_FINANCE),
        optional_steps=(StepId.ASSESS_MARKET,),
        impure=False,
        config_kwarg="cfg",
    ),
}


def all_dependencies(step: StepId) -> tuple[StepId, ...]:
    spec = STEP_SPECS[step]
    return (*spec.required_steps, *spec.optional_steps)


def _topological_order() -> tuple[StepId, ...] | None:
    """Kahn's algorithm over EVERY edge (required and optional). `None` if a
    cycle exists. The Phase 3 <-> Phase 4 "loop" the approved plan describes
    (`OPPORTUNITY` optionally depends on `FINANCIAL_FIT`) is not an actual
    graph cycle: `FINANCIAL_FIT`'s own dependency chain
    (`ASSESS_FINANCE -> BIND_PLAN -> {BUILD_PLAN, FINANCE_KNOWLEDGE} ->
    RESOLVE_PROPOSED`) never passes back through `OPPORTUNITY` — the two sit
    on independent branches that only converge at `RECOMMEND`. Ties are
    broken by `StepId.value` so this order is deterministic and stable
    across runs/processes, which `artifacts.py::compute_fingerprints` relies
    on to fingerprint each step only after every one of its dependencies
    (including optional ones) has already been fingerprinted."""
    remaining = {step: set(all_dependencies(step)) for step in STEP_SPECS}
    ordered: list[StepId] = []
    done: set[StepId] = set()
    while remaining:
        ready = sorted(
            (step for step, deps in remaining.items() if deps <= done), key=lambda s: s.value
        )
        if not ready:
            return None
        for step in ready:
            ordered.append(step)
            done.add(step)
            del remaining[step]
    return tuple(ordered)


# The single fixed order used both to break ties among several ready steps
# (planner rung 5) and to walk the DAG for fingerprinting (`artifacts.py`) —
# a true topological order over every declared edge, required and optional.
_ORDER = _topological_order()
if (
    _ORDER is None
):  # pragma: no cover — would only happen if STEP_SPECS itself were edited into a cycle
    raise RuntimeError("vyaparsarathi.conversation.workflow.STEP_SPECS contains a dependency cycle")
DAG_ORDER: tuple[StepId, ...] = _ORDER


def is_acyclic() -> bool:
    return _topological_order() is not None


def ready_steps(
    completed: frozenset[StepId], satisfied_slots: frozenset[SlotName]
) -> frozenset[StepId]:
    """Every `StepId` not yet in `completed` whose `required_steps` are all in
    `completed` and whose `required_slots` are all in `satisfied_slots`. A
    step with an unmet `optional_steps` entry is still ready — that entry
    simply is not part of its fingerprint yet (see `artifacts.py`)."""
    ready: set[StepId] = set()
    for step, spec in STEP_SPECS.items():
        if step in completed:
            continue
        if not set(spec.required_steps) <= completed:
            continue
        if not set(spec.required_slots) <= satisfied_slots:
            continue
        ready.add(step)
    return frozenset(ready)


__all__ = ["DAG_ORDER", "STEP_SPECS", "StepSpec", "all_dependencies", "is_acyclic", "ready_steps"]
