"""A scripted `LlmProvider` for tests and the offline demo (CLAUDE.md §25
Phase 6). Returns pre-recorded responses keyed by `LlmRequest.prompt_id`,
asserting loudly (raising, never silently making something up) when a caller
asks for more responses than were scripted — the same discipline
`respx`/`responses` mocks in this repo's HTTP tests already apply.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from vyaparsarathi.errors import LlmUnavailableError
from vyaparsarathi.llm.llm_models import LlmRequest, LlmResponse


class ScriptedLlmProvider:
    def __init__(self, responses: Mapping[str, Sequence[LlmResponse]]) -> None:
        self._queues: dict[str, list[LlmResponse]] = {k: list(v) for k, v in responses.items()}
        self.calls: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append(request)
        queue = self._queues.get(request.prompt_id)
        if not queue:
            raise LlmUnavailableError(
                f"ScriptedLlmProvider has no more responses queued for "
                f"prompt_id={request.prompt_id!r} (call #{len(self.calls)})"
            )
        return queue.pop(0)


__all__ = ["ScriptedLlmProvider"]
