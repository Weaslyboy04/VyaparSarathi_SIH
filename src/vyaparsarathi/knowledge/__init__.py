"""Knowledge / evidence layer — provenance-aware financial parameters resolved
from a reviewed document registry (CLAUDE.md §18, §19, Phase 5).

Every module here is pure: no network, database, clock, RNG or LLM (enforced by
``tests/test_knowledge_purity.py``, mirroring ``tests/test_finance_purity.py``).
The only impure Phase 5 code lives in ``discovery/knowledge_acquisition.py`` and
``sources/knowledge/loader.py`` — file I/O only, never a network call at request
time (CLAUDE.md §21 does not apply: there is no crawler; documents are ingested
offline by an operator, see ``data/knowledge/SOURCES.md``).

``knowledge/resolver.py`` never calls ``knowledge/retrieval.py``: parameter
values are selected by deterministic precedence over the registry, and
retrieval only supplies supporting passages for display/citation. This is the
structural guarantee that a retrieval ranking can never change a number
(CLAUDE.md §3.1, §30 — RAG is not the source of truth).

``knowledge/plan_binding.py`` is the only module in this package that
constructs a `vyaparsarathi.models.finance.FinancialInput` or imports
`vyaparsarathi.models.finance` — the Phase 4 seam, exactly as
``finance/fit.py`` is the only module in ``finance/`` that imports
``vyaparsarathi.market``.
"""

from __future__ import annotations
