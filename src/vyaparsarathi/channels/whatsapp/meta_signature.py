"""Verify a Meta Cloud API webhook's `X-Hub-Signature-256` header (CLAUDE.md
§24, §25 Phase 7). PURE — stdlib `hmac`/`hashlib` only, no `httpx`, so this
stays inside `channels/` without violating `tests/test_channels_purity.py`.

Meta signs every webhook POST body with HMAC-SHA256 over the **raw** request
bytes, keyed by the app's App Secret, and sends it as
``X-Hub-Signature-256: sha256=<hex digest>``. A transport that does not check
this would accept a POST from anyone who finds the callback URL — this
function is that check, using a constant-time comparison
(`hmac.compare_digest`) so response timing cannot leak the correct digest.
"""

from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def verify_meta_signature(app_secret: str, raw_body: bytes, signature_header: str) -> bool:
    """`True` iff `signature_header` (the raw `X-Hub-Signature-256` header
    value) matches the HMAC-SHA256 of `raw_body` keyed by `app_secret`.
    `False` for any malformed/missing header — never raises, so a caller can
    always safely reject on `False` without a try/except."""
    if not signature_header.startswith(_PREFIX):
        return False
    provided = signature_header[len(_PREFIX) :].strip()
    if not provided:
        return False
    expected = hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


__all__ = ["verify_meta_signature"]
