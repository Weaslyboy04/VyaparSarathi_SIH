"""Business-name normalization for matching (CLAUDE.md §7, §8).

Conservative on purpose: transliterate to ASCII, lowercase, strip punctuation,
collapse whitespace, drop only leading/standalone articles. It must not destroy
distinguishing information — ``name`` stays human-readable, ``normalized_name``
is a matching key.
"""

from __future__ import annotations

import re

from unidecode import unidecode

# Only truly generic words are removed, and only when other tokens remain.
_NOISE_TOKENS = {"the", "a", "an"}
_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")


def normalize_name(name: str | None) -> str:
    """Return a normalized matching key, or ``""`` for empty / missing input."""
    if not name:
        return ""

    text = unidecode(name)
    text = text.lower()
    text = text.replace("&", " and ")
    text = _PUNCT_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return ""

    tokens = text.split(" ")
    kept = [t for t in tokens if t not in _NOISE_TOKENS]
    if not kept:  # name was entirely "noise" — keep it rather than emit nothing
        kept = tokens
    return " ".join(kept)
