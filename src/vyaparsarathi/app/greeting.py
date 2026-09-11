"""The one channel-neutral opening message every channel shows a brand-new
session (CLAUDE.md §25 Phase 6/7). A plain constant, not a template with
inputs — there is nothing session-specific to say before a first message has
even arrived, so there is nothing here for an LLM to author or get wrong.

Lives in `app/` (not `conversation/`) so `channels/whatsapp/gateway.py` can use
it without violating `tests/test_channels_purity.py`'s ban on the channel
layer importing the conversation state machine. Both
`scripts/phase6_chat.py` (prints it once per new CLI session) and
`WhatsAppGateway` (prepends it as the first bubble only when a session is
genuinely new) use this exact text, so CLI and WhatsApp semantics match
(CLAUDE.md's "CLI semantics must match future WhatsApp semantics").
"""

from __future__ import annotations

GREETING_MESSAGE = (
    "Hi, welcome to VyaparSarathi. I can help assess a local business idea, "
    "financing needs, and repayment risk. Try: 'I want to open a grocery shop "
    "in Bhagwanpur, Bihar, and I have ₹6.5 lakh.'"
)

__all__ = ["GREETING_MESSAGE"]
