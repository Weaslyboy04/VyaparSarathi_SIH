"""The channel-neutral "/clear" reset command (CLAUDE.md §25 Phase 6/7).
Lives in `app/` (not `conversation/`) for the same reason `greeting.py`
does — `channels/whatsapp/` and `channels/telegram/` cannot import
`conversation/` (`tests/test_channels_purity.py`), and this is genuinely a
channel-lifecycle concern, not a conversation/advisory decision: it deletes
the session outright (`AdvisoryService.delete_session`) rather than routing
through the DAG at all, so the *same* phone number / chat id starts a
completely fresh session — greeting and all — on its next message, instead
of a `/reset`-shaped structured field the planner would have to know about.
"""

from __future__ import annotations

CLEAR_COMMANDS = frozenset({"/clear", "/reset", "/restart"})

CLEAR_CONFIRMATION_MESSAGE = (
    "Session cleared. Send any message (e.g. 'hi') to start a new conversation."
)


def is_clear_command(text: str) -> bool:
    return text.strip().lower() in CLEAR_COMMANDS


__all__ = ["CLEAR_COMMANDS", "CLEAR_CONFIRMATION_MESSAGE", "is_clear_command"]
