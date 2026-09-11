"""`channels/telegram/mapping.py` (CLAUDE.md §25 Phase 7). Pure; no network."""

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
from vyaparsarathi.channels.telegram.errors import MalformedUpdateError
from vyaparsarathi.channels.telegram.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_telegram_messages,
)
from vyaparsarathi.channels.telegram.models import InboundKind


def _update(**message_fields: object) -> dict:
    return {
        "update_id": 1,
        "message": {"message_id": 5, "chat": {"id": 987, "type": "private"}, **message_fields},
    }


def test_text_message_parses_as_text() -> None:
    msg = parse_inbound(_update(text="hello"))
    assert msg.kind is InboundKind.TEXT
    assert msg.chat_id == "987"
    assert msg.text == "hello"
    assert msg.provider_message_id == "5"


@pytest.mark.parametrize("key", ["voice", "photo", "document", "sticker", "location"])
def test_media_types_parse_as_unsupported(key: str) -> None:
    msg = parse_inbound(_update(**{key: {"anything": "here"}}))
    assert msg.kind is InboundKind.UNSUPPORTED
    assert msg.unsupported_type == key


def test_update_without_a_message_key_is_malformed() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_inbound({"update_id": 1, "edited_message": {"chat": {"id": 1}, "text": "x"}})


def test_message_without_chat_id_is_malformed() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_inbound({"update_id": 1, "message": {"text": "hi"}})


def test_message_with_no_recognised_content_is_malformed() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_inbound(_update())


def test_non_mapping_update_is_malformed() -> None:
    with pytest.raises(MalformedUpdateError):
        parse_inbound("not a dict")  # type: ignore[arg-type]


def test_channel_session_id_is_namespaced() -> None:
    assert channel_session_id("987") == "telegram:987"


def test_channel_session_id_rejects_empty() -> None:
    with pytest.raises(MalformedUpdateError):
        channel_session_id("   ")


def test_to_message_request_maps_bare_number_to_selected_choice() -> None:
    msg = parse_inbound(_update(text="2"))
    req = to_message_request(msg, session_id="telegram:987", received_at=datetime.now(UTC))
    assert req.selected_choice == 2
    assert req.channel is ChannelId.TELEGRAM


def test_to_message_request_large_number_is_not_a_choice() -> None:
    msg = parse_inbound(_update(text="40000"))
    req = to_message_request(msg, session_id="telegram:987", received_at=datetime.now(UTC))
    assert req.selected_choice is None
    assert req.text == "40000"


def _reply(
    *texts: str,
    choices: tuple[Choice, ...] = (),
    expects: ExpectedInput = ExpectedInput.NONE,
) -> AdvisoryReply:
    return AdvisoryReply(
        session_id="s1",
        turn_index=1,
        messages=tuple(OutboundMessage(text=t) for t in texts),
        choices=choices,
        expects=expects,
        state=AdvisoryPhase.COLLECTING,
    )


def test_to_telegram_messages_one_bubble_per_line() -> None:
    reply = _reply("line one", "line two")
    bubbles = to_telegram_messages(reply, chat_id="987")
    assert [b.body for b in bubbles] == ["line one", "line two"]
    assert all(b.chat_id == "987" for b in bubbles)


def test_to_telegram_messages_appends_reply_with_a_number_hint() -> None:
    choices = (Choice(index=1, label="A"), Choice(index=2, label="B"))
    reply = _reply("pick one", choices=choices, expects=ExpectedInput.CHOICE)
    bubbles = to_telegram_messages(reply, chat_id="987")
    assert bubbles[-1].body == "Reply with a number from 1 to 2."


def test_to_telegram_messages_empty_reply_gets_a_fallback_bubble() -> None:
    reply = _reply()
    bubbles = to_telegram_messages(reply, chat_id="987")
    assert len(bubbles) == 1
    assert bubbles[0].body


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
