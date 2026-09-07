"""Phase 6 conversation state and orchestration (CLAUDE.md §25 Phase 6).

This package is **PURE**: no network, no disk, no wall-clock, no LLM calls, no
import of `vyaparsarathi.{llm,sources,geocoding,database,discovery}`. It knows
the shape of a multi-turn conversation (slots, the step DAG, invalidation,
clarification, recommendation, the evidence bundle, grounding, rendering) but
never executes an engine itself — `llm/tools.py` is the only module that calls
Phase 1-5 code, and it does so through `StepId` names this package defines,
never the reverse. See `tests/test_conversation_purity.py`.
"""

from __future__ import annotations
