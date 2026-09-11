"""Tunable Phase 6 conversation parameters (CLAUDE.md §33: "all thresholds ...
live in config/ ... and are imported, not redefined"). Frozen, echoed where
useful, never a magic number in `conversation/*.py`.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ConversationMode(StrEnum):
    """Which UX the planner's ladder runs (CLAUDE.md §2's "COLLECT → VALIDATE
    → COLLECT → ANALYSE → one advisory" vs. the Phase 6 structured-command
    debug harness). Deliberately NOT a field on `ConversationConfig` — that
    model is dumped into `CONFIG_BLOBS` and fingerprinted
    (`conversation/artifacts.py`), so a field here would invalidate every
    cached artifact the instant a session's mode changed; this is a plain,
    unfingerprinted enum instead, threaded through `RunContext.mode`
    (`llm/tools.py`) to `conversation/planner.py::decide`.
    """

    NORMAL = "normal"  # the collect-then-deliver advisory UX (default for real channels)
    DEVELOPER = "developer"  # today's incremental structured-command harness (default here)


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # A catchment radius the entrepreneur never stated. This IS an ASSUMED
    # slot value (config-sourced, rationale recorded) — CLAUDE.md §3.5 permits
    # an explicit, labelled assumption; it never silently substitutes for a
    # missing financial driver. [tunable]
    default_radius_m: int = 5_000

    # Phase 3's own cap (CLAUDE.md §6.1); re-declared here only as the ceiling
    # this layer will accept from a user before flagging a CONTRADICTION.
    max_radius_m: int = 25_000  # [tunable]

    # How many geocoding candidates are ever shown as numbered choices.
    max_disambiguation_choices: int = 6  # [tunable]

    # Session/turn growth bounds (plan §"Risks": "Unbounded session growth").
    max_turns: int = 200  # [tunable]
    max_message_chars: int = 4_000  # [tunable]

    # How many bounded LLM repair attempts a malformed structured response gets
    # before the deterministic give-up path takes over.
    max_repair_attempts: int = 1  # [tunable]

    # Retrieval width for the explanation-stage evidence bundle.
    evidence_passage_limit: int = 6  # [tunable]

    # The FinancialPlanInput.horizon_months every plan is built with — a
    # structural modelling-window choice, not a financial figure, so it is
    # not a FinancialInput and carries no provenance kind. [tunable]
    default_horizon_months: int = 36

    # Assumption used when nothing overrides how a loan handles moratorium
    # interest — a structural default (not a FinancialInput; see
    # plan_builder.py) since MoratoriumTreatment carries no provenance.
    default_moratorium_treatment: str = "interest_serviced"  # [tunable]

    # Nonce length (hex chars) for delimiting untrusted passage text inside an
    # LLM prompt (see conversation/bundle.py). Deterministic — derived from the
    # bundle fingerprint, never random.
    injection_nonce_hex_len: int = 6  # [tunable]

    # Grounding: a digit run this long or longer in narrative text must trace
    # to a bundle Fact's rendered value, or the section is replaced. Default
    # 1 (every numeral checked) — high-stakes figures like an "11%" rate or
    # a "1.6" DSCR are short, so a coarser threshold would wave them through.
    grounding_min_digit_run: int = 1  # [tunable]


DEFAULT_CONVERSATION_CONFIG = ConversationConfig()

__all__ = ["ConversationConfig", "ConversationMode", "DEFAULT_CONVERSATION_CONFIG"]
