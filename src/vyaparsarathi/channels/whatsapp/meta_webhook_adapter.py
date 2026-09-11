"""Meta Cloud API's real webhook shape <-> the neutral inbound `dict`
`mapping.parse_inbound` accepts (CLAUDE.md §25 Phase 7). PURE — plain dict
walking only, no `httpx`, no network, so this stays inside `channels/`
without violating `tests/test_channels_purity.py`.

Meta's actual webhook body looks like::

    {
      "object": "whatsapp_business_account",
      "entry": [{
        "id": "<waba id>",
        "changes": [{
          "field": "messages",
          "value": {
            "messaging_product": "whatsapp",
            "metadata": {"phone_number_id": "..."},
            "contacts": [{"wa_id": "919812345678", "profile": {"name": "..."}}],
            "messages": [{
              "from": "919812345678", "id": "wamid.xxx", "timestamp": "...",
              "type": "text", "text": {"body": "hello"}
            }]
          }
        }]
      }]
    }

— a batch of `entry`/`changes`/`messages`, and a `value` with no `messages`
key at all for a delivery-status update (sent/delivered/read), which this
module silently skips (never a new inbound turn). `models.py`'s own
docstring documents the neutral shape this produces.
"""

from __future__ import annotations

from typing import Any

# Meta's message.type values this system can act on; everything else
# (image, audio, video, document, sticker, location, contacts, ...) is passed
# through as-is so `parse_inbound` maps it to `InboundKind.UNSUPPORTED` —
# never dropped silently, never crashes on an unrecognised type.
_TEXT = "text"
_INTERACTIVE = "interactive"


def iter_neutral_inbound_payloads(raw_webhook_body: Any) -> list[dict[str, Any]]:
    """`raw_webhook_body` -> zero or more neutral payload dicts, each ready
    for `channels.whatsapp.mapping.parse_inbound`. Never raises — a
    malformed/unexpected shape (a missing key, a status-only update, a
    non-dict entry) simply contributes nothing, since the caller's job is to
    process real messages, not to validate Meta's own webhook contract."""
    payloads: list[dict[str, Any]] = []
    if not isinstance(raw_webhook_body, dict):
        return payloads
    for entry in _as_list(raw_webhook_body.get("entry")):
        if not isinstance(entry, dict):
            continue
        for change in _as_list(entry.get("changes")):
            if not isinstance(change, dict):
                continue
            value = change.get("value")
            if not isinstance(value, dict):
                continue
            for message in _as_list(value.get("messages")):
                neutral = _neutral_payload(message)
                if neutral is not None:
                    payloads.append(neutral)
    return payloads


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _neutral_payload(message: object) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    sender = message.get("from")
    msg_type = message.get("type")
    if not isinstance(sender, str) or not sender or not isinstance(msg_type, str) or not msg_type:
        return None

    neutral: dict[str, Any] = {"from": sender, "type": msg_type}
    message_id = message.get("id")
    if isinstance(message_id, str):
        neutral["message_id"] = message_id

    if msg_type == _TEXT:
        body = message.get("text")
        text = body.get("body") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            return None
        neutral["text"] = text
        return neutral

    if msg_type == _INTERACTIVE:
        interactive = message.get("interactive")
        reply = _extract_interactive_reply(interactive)
        if reply is None:
            return None
        neutral["interactive"] = reply
        return neutral

    # Any other type (image/audio/video/document/sticker/location/contacts/
    # button/order/system/unknown): pass through with no further fields —
    # `parse_inbound` maps it straight to InboundKind.UNSUPPORTED.
    return neutral


def _extract_interactive_reply(interactive: object) -> dict[str, str] | None:
    if not isinstance(interactive, dict):
        return None
    for kind in ("button_reply", "list_reply"):
        reply = interactive.get(kind)
        if isinstance(reply, dict) and isinstance(reply.get("id"), str) and reply["id"]:
            title = reply.get("title")
            return {"reply_id": reply["id"], "title": title if isinstance(title, str) else ""}
    return None


__all__ = ["iter_neutral_inbound_payloads"]
