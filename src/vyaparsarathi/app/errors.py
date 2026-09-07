"""Backend-layer exceptions (CLAUDE.md §33, §25 Phase 6)."""

from __future__ import annotations

from vyaparsarathi.errors import VyaparError


class SessionNotFoundError(VyaparError):
    """`AdvisoryService.send_message` was called with a `session_id` that
    does not exist (or was never started). A channel adapter should treat
    this as "start a new session" — it is a caller-flow error, not a data
    condition any engine reports."""


__all__ = ["SessionNotFoundError"]
