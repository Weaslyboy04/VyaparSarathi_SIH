"""Channel adapters (CLAUDE.md §4 "WhatsApp / channel interface", §25 Phase 7).

A channel package is a **translation boundary only**: it maps a provider's
inbound payload onto `app/dto.py::MessageRequest` and an
`app/dto.py::AdvisoryReply` back onto that provider's outbound shape. It calls
`app/service.py::AdvisoryService` and nothing else — no `market/`, `finance/`,
`knowledge/`, `discovery/`, `conversation/`, or `llm/` import lives here
(enforced by `tests/test_channels_purity.py`, mirroring
`tests/test_market_purity.py`). CLAUDE.md §25 Phase 7's explicit "must NOT":
no business logic in the channel layer.
"""

from __future__ import annotations

__all__: list[str] = []
