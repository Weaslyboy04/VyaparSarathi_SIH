"""Phase 6 LLM edge: provider access, prompts, structured output, and
`tools.py` (the only module in the whole codebase allowed to execute a
`StepId` against Phase 1-5 engines — CLAUDE.md §25 Phase 6).

Nothing in `vyaparsarathi.{market,finance,knowledge,discovery,sources,
geocoding,database,models}` may import this package — the model layer must
stay usable with no LLM, ever (`tests/test_llm_leaf.py`).
"""

from __future__ import annotations
