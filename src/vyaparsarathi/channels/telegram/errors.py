"""Telegram channel errors (CLAUDE.md §25 Phase 7)."""

from __future__ import annotations

from vyaparsarathi.errors import VyaparError


class MalformedUpdateError(VyaparError):
    """A Telegram `getUpdates` update was structurally invalid (not a dict,
    or a `message` with no usable `chat.id`) — never raised for an update
    type we simply don't act on yet (e.g. `edited_message`, `my_chat_member`,
    a channel post); those are silently ignored by the poll loop instead."""


__all__ = ["MalformedUpdateError"]
