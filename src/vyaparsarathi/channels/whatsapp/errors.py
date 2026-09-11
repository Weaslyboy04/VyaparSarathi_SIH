"""WhatsApp channel-boundary exceptions (CLAUDE.md §33, §25 Phase 7)."""

from __future__ import annotations

from vyaparsarathi.errors import VyaparError


class MalformedWebhookError(VyaparError):
    """An inbound webhook payload did not match the neutral contract
    (`channels/whatsapp/models.py`) — e.g. not an object, no sender, no
    message type, or a text message with no text. The transport layer should
    answer the provider with a 4xx and send nothing to the user; it is never
    surfaced to `AdvisoryService`."""


__all__ = ["MalformedWebhookError"]
