"""Deterministic tokenization for lexical retrieval (CLAUDE.md §19).

Mirrors `normalization/text.py::normalize_name`'s conservative style
(transliterate, lowercase, strip punctuation, collapse whitespace) but for
free-text passages rather than business names: a broader English stopword
list is appropriate here because BM25 scoring benefits from removing
high-frequency, low-information words, whereas a business name must not lose
distinguishing tokens.
"""

from __future__ import annotations

import re

from unidecode import unidecode

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")

# A small, fixed English stopword list — [tunable] in spirit, but kept as a
# plain constant rather than a KnowledgeConfig field: changing it changes
# every existing BM25 index's term statistics, so it is a code change, not a
# per-run tunable.
STOPWORDS: frozenset[str] = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "if",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "as",
        "by",
        "at",
        "from",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "shall",
        "will",
        "may",
        "not",
        "no",
        "such",
        "any",
        "under",
        "per",
    }
)


def tokenize(text: str) -> list[str]:
    """Text -> a list of lowercase, ASCII-folded, punctuation-free tokens
    with stopwords removed. Deterministic; no locale dependence."""
    if not text:
        return []
    ascii_text = unidecode(text).lower()
    ascii_text = _PUNCT_RE.sub(" ", ascii_text)
    ascii_text = _WS_RE.sub(" ", ascii_text).strip()
    if not ascii_text:
        return []
    return [t for t in ascii_text.split(" ") if t and t not in STOPWORDS]


__all__ = ["STOPWORDS", "tokenize"]
