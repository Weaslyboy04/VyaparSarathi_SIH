"""`channels/whatsapp/meta_signature.py` (CLAUDE.md §24, §25 Phase 7). Pure;
no network, no real Meta call."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from vyaparsarathi.channels.whatsapp.meta_signature import verify_meta_signature

_SECRET = "test-app-secret"
_BODY = b'{"object":"whatsapp_business_account","entry":[]}'


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_valid_signature_is_accepted() -> None:
    header = _sign(_SECRET, _BODY)
    assert verify_meta_signature(_SECRET, _BODY, header) is True


def test_wrong_secret_is_rejected() -> None:
    header = _sign("a-different-secret", _BODY)
    assert verify_meta_signature(_SECRET, _BODY, header) is False


def test_tampered_body_is_rejected() -> None:
    header = _sign(_SECRET, _BODY)
    assert verify_meta_signature(_SECRET, _BODY + b"tampered", header) is False


def test_missing_prefix_is_rejected() -> None:
    digest = hmac.new(_SECRET.encode("utf-8"), _BODY, hashlib.sha256).hexdigest()
    assert verify_meta_signature(_SECRET, _BODY, digest) is False  # no "sha256=" prefix


@pytest.mark.parametrize("header", ["", "sha256=", "sha1=abcd", "not-even-close"])
def test_malformed_headers_never_raise_and_are_rejected(header: str) -> None:
    assert verify_meta_signature(_SECRET, _BODY, header) is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
