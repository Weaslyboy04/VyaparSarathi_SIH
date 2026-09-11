"""`channels/whatsapp/meta_webhook_adapter.py` (CLAUDE.md §25 Phase 7). Pure;
no network. Fixtures shaped like real Meta Cloud API webhook bodies."""

from __future__ import annotations

import pytest

from vyaparsarathi.channels.whatsapp.mapping import parse_inbound
from vyaparsarathi.channels.whatsapp.meta_webhook_adapter import iter_neutral_inbound_payloads
from vyaparsarathi.channels.whatsapp.models import InboundKind


def _entry(*messages: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "123"},
                            "messages": list(messages),
                        },
                    }
                ],
            }
        ],
    }


def test_text_message_maps_to_the_neutral_shape() -> None:
    raw = _entry(
        {
            "from": "919812345678",
            "id": "wamid.abc",
            "timestamp": "123",
            "type": "text",
            "text": {"body": "hello there"},
        }
    )
    payloads = iter_neutral_inbound_payloads(raw)
    assert payloads == [
        {
            "from": "919812345678",
            "type": "text",
            "message_id": "wamid.abc",
            "text": "hello there",
        }
    ]
    # And it actually round-trips through the real neutral-contract parser.
    parsed = parse_inbound(payloads[0])
    assert parsed.kind is InboundKind.TEXT
    assert parsed.text == "hello there"


def test_button_reply_maps_to_interactive_reply_id() -> None:
    raw = _entry(
        {
            "from": "919812345678",
            "id": "wamid.def",
            "type": "interactive",
            "interactive": {
                "type": "button_reply",
                "button_reply": {"id": "2", "title": "Bhagwanpur, Bihar"},
            },
        }
    )
    payloads = iter_neutral_inbound_payloads(raw)
    assert payloads[0]["interactive"] == {"reply_id": "2", "title": "Bhagwanpur, Bihar"}
    parsed = parse_inbound(payloads[0])
    assert parsed.kind is InboundKind.INTERACTIVE_REPLY
    assert parsed.choice_id == "2"


def test_list_reply_maps_to_interactive_reply_id() -> None:
    raw = _entry(
        {
            "from": "919812345678",
            "type": "interactive",
            "interactive": {"type": "list_reply", "list_reply": {"id": "1", "title": "Option A"}},
        }
    )
    payloads = iter_neutral_inbound_payloads(raw)
    assert payloads[0]["interactive"] == {"reply_id": "1", "title": "Option A"}


def test_unsupported_media_type_passes_through_for_unsupported_handling() -> None:
    raw = _entry({"from": "919812345678", "id": "wamid.img", "type": "image"})
    payloads = iter_neutral_inbound_payloads(raw)
    assert payloads == [{"from": "919812345678", "type": "image", "message_id": "wamid.img"}]
    parsed = parse_inbound(payloads[0])
    assert parsed.kind is InboundKind.UNSUPPORTED
    assert parsed.unsupported_type == "image"


def test_multiple_messages_in_one_webhook_call_all_come_back() -> None:
    raw = _entry(
        {"from": "111", "type": "text", "text": {"body": "first"}},
        {"from": "222", "type": "text", "text": {"body": "second"}},
    )
    payloads = iter_neutral_inbound_payloads(raw)
    assert [p["from"] for p in payloads] == ["111", "222"]
    assert [p["text"] for p in payloads] == ["first", "second"]


def test_status_only_update_contributes_nothing() -> None:
    """A delivery/read receipt has a `value` with `statuses`, no `messages`
    key at all — this must never be mistaken for an inbound message."""
    raw = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "statuses": [{"id": "wamid.x", "status": "delivered"}],
                        },
                    }
                ],
            }
        ],
    }
    assert iter_neutral_inbound_payloads(raw) == []


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"entry": "not-a-list"},
        {"entry": [{"changes": "not-a-list"}]},
        {"entry": [{"changes": [{"value": "not-a-dict"}]}]},
        {"entry": [{"changes": [{"value": {"messages": [{"type": "text"}]}}]}]},  # no "from"
        {"entry": [{"changes": [{"value": {"messages": [{"from": "1"}]}}]}]},  # no "type"
        None,
        "a string",
    ],
)
def test_malformed_or_incomplete_payloads_never_raise(raw: object) -> None:
    assert iter_neutral_inbound_payloads(raw) == []


def test_text_message_with_empty_body_is_dropped() -> None:
    raw = _entry({"from": "919812345678", "type": "text", "text": {"body": "   "}})
    assert iter_neutral_inbound_payloads(raw) == []


def test_interactive_message_with_no_recognised_reply_kind_is_dropped() -> None:
    raw = _entry({"from": "919812345678", "type": "interactive", "interactive": {}})
    assert iter_neutral_inbound_payloads(raw) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
