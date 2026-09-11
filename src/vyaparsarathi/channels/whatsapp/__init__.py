"""Provider-neutral WhatsApp channel boundary (CLAUDE.md §4, §25 Phase 7).

`WhatsAppGateway` takes a neutral inbound `dict` and returns neutral
`OutboundWhatsAppMessage`s (plus, when relevant, a `ReportResult`).
`FakeWhatsAppTransport` records them for tests. `MetaCloudApiTransport` is
the real Meta Cloud API adapter — still `httpx`-free itself (a `JsonPoster`/
`MediaUploader` is injected; the real one lives in
`scripts/whatsapp_webhook_server.py`) — and `meta_webhook_adapter`/
`meta_signature` translate Meta's real webhook shape and signature header
into what this package's neutral contract (`models.py`) expects.
"""

from __future__ import annotations

from vyaparsarathi.channels.whatsapp.errors import MalformedWebhookError
from vyaparsarathi.channels.whatsapp.fake_transport import FakeWhatsAppTransport
from vyaparsarathi.channels.whatsapp.gateway import (
    MEDIA_DEFERRED_NOTICE,
    VOICE_DEFERRED_NOTICE,
    WhatsAppGateway,
)
from vyaparsarathi.channels.whatsapp.mapping import (
    channel_session_id,
    parse_inbound,
    to_message_request,
    to_whatsapp_messages,
)
from vyaparsarathi.channels.whatsapp.meta_cloud_transport import (
    JsonPoster,
    MediaUploader,
    MetaCloudApiTransport,
    MetaSendError,
)
from vyaparsarathi.channels.whatsapp.meta_signature import verify_meta_signature
from vyaparsarathi.channels.whatsapp.meta_webhook_adapter import iter_neutral_inbound_payloads
from vyaparsarathi.channels.whatsapp.models import (
    InboundKind,
    InboundWhatsAppMessage,
    OutboundKind,
    OutboundWhatsAppMessage,
)

__all__ = [
    "MEDIA_DEFERRED_NOTICE",
    "VOICE_DEFERRED_NOTICE",
    "FakeWhatsAppTransport",
    "InboundKind",
    "InboundWhatsAppMessage",
    "JsonPoster",
    "MalformedWebhookError",
    "MediaUploader",
    "MetaCloudApiTransport",
    "MetaSendError",
    "OutboundKind",
    "OutboundWhatsAppMessage",
    "WhatsAppGateway",
    "channel_session_id",
    "iter_neutral_inbound_payloads",
    "parse_inbound",
    "to_message_request",
    "to_whatsapp_messages",
    "verify_meta_signature",
]
