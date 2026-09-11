"""`channels/whatsapp/mapping.py` — pure inbound/outbound translation
(CLAUDE.md §4, §25 Phase 7). No network, no service.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from vyaparsarathi.app.dto import (
    AdvisoryPhase,
    AdvisoryReply,
    ChannelId,
    Choice,
    ExpectedInput,
    OutboundMessage,
)
from vyaparsarathi.channels.whatsapp.errors import MalformedWebhookError
from vyaparsarathi.channels.whatsapp.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_whatsapp_messages,
)
from vyaparsarathi.channels.whatsapp.models import InboundKind, OutboundKind

_NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# --- parse_inbound ---------------------------------------------------------


def test_parse_plain_text_message() -> None:
    msg = parse_inbound({"from": "+919812345678", "type": "text", "text": "  I have 6.5 lakh  "})
    assert msg.kind is InboundKind.TEXT
    assert msg.sender == "+919812345678"
    assert msg.text == "  I have 6.5 lakh  "  # preserved verbatim; only 'from'/ids are trimmed


def test_parse_interactive_reply_carries_the_choice_id() -> None:
    msg = parse_inbound(
        {
            "from": "+919812345678",
            "type": "interactive",
            "interactive": {"reply_id": "2", "title": "Bhagwanpur, Bihar"},
            "message_id": "wamid.abc",
        }
    )
    assert msg.kind is InboundKind.INTERACTIVE_REPLY
    assert msg.choice_id == "2"
    assert msg.text == "Bhagwanpur, Bihar"
    assert msg.provider_message_id == "wamid.abc"


def test_parse_unknown_type_is_unsupported_not_an_error() -> None:
    for kind in ("audio", "voice", "image", "document", "location", "sticker"):
        msg = parse_inbound({"from": "+91", "type": kind})
        assert msg.kind is InboundKind.UNSUPPORTED
        assert msg.unsupported_type == kind


@pytest.mark.parametrize(
    "payload",
    [
        "not-an-object",
        [],
        {"type": "text", "text": "hi"},  # no 'from'
        {"from": "  ", "type": "text", "text": "hi"},  # blank 'from'
        {"from": "+91", "text": "hi"},  # no 'type'
        {"from": "+91", "type": "  "},  # blank 'type'
        {"from": "+91", "type": "text"},  # text type, no text
        {"from": "+91", "type": "text", "text": "   "},  # text type, blank text
        {"from": "+91", "type": "interactive"},  # no interactive object
        {"from": "+91", "type": "interactive", "interactive": {}},  # no reply_id
        {"from": "+91", "type": "interactive", "interactive": {"reply_id": ""}},
    ],
)
def test_parse_rejects_malformed_payloads(payload: object) -> None:
    with pytest.raises(MalformedWebhookError):
        parse_inbound(payload)  # type: ignore[arg-type]


# --- channel_session_id --------------------------------------------------


def test_channel_session_id_is_namespaced_and_trimmed() -> None:
    assert channel_session_id("  +919812345678 ") == "whatsapp:+919812345678"


def test_channel_session_id_rejects_empty() -> None:
    with pytest.raises(MalformedWebhookError):
        channel_session_id("   ")


# --- to_message_request ------------------------------------------------


def test_text_message_maps_to_free_text_request() -> None:
    msg = parse_inbound({"from": "+91", "type": "text", "text": "open a grocery shop"})
    req = to_message_request(msg, session_id="whatsapp:+91", received_at=_NOW)
    assert req.channel is ChannelId.WHATSAPP
    assert req.text == "open a grocery shop"
    assert req.selected_choice is None
    assert req.received_at == _NOW


def test_bare_number_text_maps_to_selected_choice() -> None:
    msg = parse_inbound({"from": "+91", "type": "text", "text": " 2 "})
    req = to_message_request(msg, session_id="s", received_at=_NOW)
    assert req.selected_choice == 2
    assert req.text == " 2 "  # raw text kept for audit


def test_large_number_text_is_not_treated_as_a_choice() -> None:
    msg = parse_inbound({"from": "+91", "type": "text", "text": "40000"})
    req = to_message_request(msg, session_id="s", received_at=_NOW)
    assert req.selected_choice is None


def test_interactive_reply_maps_to_selected_choice() -> None:
    msg = parse_inbound(
        {"from": "+91", "type": "interactive", "interactive": {"reply_id": "3", "title": "X"}}
    )
    req = to_message_request(msg, session_id="s", received_at=_NOW)
    assert req.selected_choice == 3


# --- to_whatsapp_messages --------------------------------------------


def _reply(**kw: object) -> AdvisoryReply:
    base: dict[str, object] = {"session_id": "s", "turn_index": 1, **kw}
    return AdvisoryReply(**base)  # type: ignore[arg-type]


def test_each_nonempty_bubble_becomes_one_outbound_message() -> None:
    reply = _reply(
        messages=(
            OutboundMessage(text="Market stance: proceed."),
            OutboundMessage(text=""),  # dropped
            OutboundMessage(text="Financial status: feasible."),
        ),
        state=AdvisoryPhase.COMPLETE,
    )
    out = to_whatsapp_messages(reply, to="+91")
    assert [m.body for m in out] == ["Market stance: proceed.", "Financial status: feasible."]
    assert all(m.to == "+91" and m.kind is OutboundKind.TEXT for m in out)


def test_choice_reply_appends_a_plain_text_instruction_bubble() -> None:
    choices = (Choice(index=1, label="Bhagwanpur, Bihar"), Choice(index=2, label="Bhagwanpur, UP"))
    reply = _reply(
        messages=(
            OutboundMessage(text="Several matches were found; please choose one."),
            OutboundMessage(text="1. Bhagwanpur, Bihar"),
            OutboundMessage(text="2. Bhagwanpur, UP", choices=choices),
        ),
        choices=choices,
        expects=ExpectedInput.CHOICE,
        state=AdvisoryPhase.BLOCKED,
    )
    out = to_whatsapp_messages(reply, to="+91")
    assert out[-1].body == "Reply with a number from 1 to 2."
    # the numbered options are already present as their own bubbles
    assert "1. Bhagwanpur, Bihar" in [m.body for m in out]
    assert "2. Bhagwanpur, UP" in [m.body for m in out]


def test_empty_reply_still_produces_one_safe_bubble() -> None:
    out = to_whatsapp_messages(_reply(messages=()), to="+91")
    assert len(out) == 1
    assert "text" in out[0].body.lower()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
