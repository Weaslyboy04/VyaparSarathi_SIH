"""One-off ETL, stage 1: operator-prepared document text -> the section-aware
chunk corpus Phase 5 reads (``data/knowledge/documents.jsonl`` +
``data/knowledge/chunks.jsonl.gz``).

Not imported by the package; runs no part of the request path — mirrors
``scripts/build_census_dataset.py`` (CLAUDE.md §23, §33). Deterministic: rows
are sorted by id, and ``--built-at`` is a value the operator supplies, never
a clock read.

This script does **not** parse PDF, DOCX, or HTML markup — that would need a
new dependency the project has not adopted (CLAUDE.md §4.1: "keep
dependencies minimal; justify every new one"). It reads **plain text** the
operator has already extracted (e.g. via ``pdftotext``, a copy-paste from an
official HTML page, or any text export) using a small heading/marker
convention described below. Add a real extractor here, stdlib-permitting or
with a justified new dependency, only once the corpus is large enough that
manual extraction is the bottleneck — not before.

## Input layout

One directory per document under ``--raw-dir``:

    <raw-dir>/<document_id>/document.json   # DocumentRecord fields, hand-filled
    <raw-dir>/<document_id>/text.txt        # the extracted plain text

``document.json`` holds every `DocumentRecord` field except `document_id`
(taken from the directory name) and `content_sha256` (computed here, from
`text.txt`, so it changes whenever the operator's extraction changes).
`retrieved_at` is still the operator's own value — when *they* downloaded the
source, not read from a clock here.

## Text convention (section-aware chunking, CLAUDE.md §19)

* A line starting with ``#`` (one or more, Markdown-style) opens a new
  section at that heading depth; its text becomes ``heading_path``. A
  leading numeral in the heading (``## 4.2 Promoter contribution``) becomes
  ``locator.section`` (``"4.2"``); a heading with no leading numeral leaves
  `section` empty and relies on `heading_path` alone.
* A line ``[[page:N]]`` on its own sets the current page number for
  subsequent chunks (`locator.page_from`/`page_to`); omit it if the source
  has no stable page numbers.
* A line ``[[topics: a, b]]`` / ``[[schemes: a, b]]`` / ``[[categories: a,
  b]]`` right after a heading tags every chunk in that section.
* A section longer than ``--max-words`` is split into further chunks at
  paragraph boundaries (blank-line-separated), each gaining its own
  ``locator.paragraph_index`` under the same heading/section.

    python scripts/build_knowledge_corpus.py \\
        --raw-dir data/knowledge/raw \\
        --built-at 2026-01-15T00:00:00+00:00 \\
        --out-dir data/knowledge
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vyaparsarathi.models.knowledge import (  # noqa: E402
    ChunkLocator,
    DocumentChunk,
    DocumentRecord,
    KnowledgeTopic,
)
from vyaparsarathi.models.taxonomy import BusinessCategory  # noqa: E402

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
_SECTION_NUMBER_RE = re.compile(r"^(\d+(?:\.\d+)*)\s*")
_PAGE_MARKER_RE = re.compile(r"^\[\[page:(\d+)\]\]$")
_TAG_MARKER_RE = re.compile(r"^\[\[(topics|schemes|categories):\s*(.*)\]\]$")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_marker_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


class _Section:
    __slots__ = (
        "heading_path",
        "section_number",
        "page",
        "topics",
        "schemes",
        "categories",
        "lines",
    )

    def __init__(
        self,
        heading_path: tuple[str, ...],
        section_number: str,
        page: int | None,
    ) -> None:
        self.heading_path = heading_path
        self.section_number = section_number
        self.page = page
        self.topics: tuple[str, ...] = ()
        self.schemes: tuple[str, ...] = ()
        self.categories: tuple[str, ...] = ()
        self.lines: list[str] = []


def _parse_sections(text: str) -> list[_Section]:
    """Split raw text into one `_Section` per heading (or one section with an
    empty heading_path for any text preceding the first heading)."""
    sections: list[_Section] = []
    stack: list[str] = []
    current_page: int | None = None
    current = _Section(heading_path=(), section_number="", page=None)
    sections.append(current)

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        page_match = _PAGE_MARKER_RE.match(line.strip())
        if page_match:
            current_page = int(page_match.group(1))
            # Apply retroactively to the section already open, in case the
            # marker sits inside a section's body rather than before its
            # heading — otherwise it would only take effect on the NEXT
            # heading, silently leaving the current section unmarked.
            current.page = current_page
            continue

        tag_match = _TAG_MARKER_RE.match(line.strip())
        if tag_match:
            kind, values = tag_match.group(1), _split_marker_list(tag_match.group(2))
            if kind == "topics":
                current.topics = tuple(values)
            elif kind == "schemes":
                current.schemes = tuple(values)
            else:
                current.categories = tuple(values)
            continue

        heading_match = _HEADING_RE.match(line)
        if heading_match:
            depth = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            stack = stack[: depth - 1]
            stack.append(title)
            number_match = _SECTION_NUMBER_RE.match(title)
            section_number = number_match.group(1) if number_match else ""
            current = _Section(
                heading_path=tuple(stack), section_number=section_number, page=current_page
            )
            sections.append(current)
            continue

        current.lines.append(raw_line)

    return [s for s in sections if any(line.strip() for line in s.lines)]


def _split_paragraphs(lines: list[str], max_words: int) -> list[str]:
    """Group lines into paragraph-based chunks, each under `max_words`."""
    text = "\n".join(lines).strip()
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for para in paragraphs:
        words = len(para.split())
        if current and current_words + words > max_words:
            chunks.append("\n\n".join(current))
            current, current_words = [], 0
        current.append(para)
        current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def build_chunks(document_id: str, text: str, *, max_words: int) -> list[DocumentChunk]:
    """Pure: text in, `DocumentChunk` list out. Deterministic — no clock, no
    randomness; `chunk_id` collisions (two sections sharing a locator ref)
    are disambiguated with a stable running suffix."""
    chunks: list[DocumentChunk] = []
    seen_refs: dict[str, int] = {}

    for section in _parse_sections(text):
        pieces = _split_paragraphs(section.lines, max_words=max_words)
        for para_idx, piece in enumerate(pieces):
            paragraph_index = para_idx if len(pieces) > 1 else None
            locator = ChunkLocator(
                page_from=section.page,
                section=section.section_number,
                paragraph_index=paragraph_index,
            )
            ref = locator.as_ref()
            seen_refs[ref] = seen_refs.get(ref, 0) + 1
            if seen_refs[ref] > 1:
                ref = f"{ref}-{seen_refs[ref]}"
            chunk_id = f"{document_id}#{ref}"
            chunks.append(
                DocumentChunk(
                    chunk_id=chunk_id,
                    document_id=document_id,
                    locator=locator,
                    heading_path=section.heading_path,
                    text=piece,
                    token_count=len(piece.split()),
                    topics=tuple(KnowledgeTopic(t) for t in section.topics),
                    schemes=section.schemes,
                    categories=tuple(BusinessCategory(c) for c in section.categories),
                    text_sha256=_sha256_text(piece),
                )
            )
    return chunks


def _load_document_record(raw_dir: Path, document_id: str) -> DocumentRecord:
    meta_path = raw_dir / document_id / "document.json"
    meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["document_id"] = document_id
    text_path = raw_dir / document_id / "text.txt"
    meta["content_sha256"] = _sha256_text(text_path.read_text(encoding="utf-8"))
    return DocumentRecord.model_validate(meta)


def _document_ids(raw_dir: Path) -> Iterator[str]:
    for child in sorted(raw_dir.iterdir()):
        if child.is_dir() and (child / "document.json").exists() and (child / "text.txt").exists():
            yield child.name


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="build_knowledge_corpus", description=__doc__)
    p.add_argument(
        "--raw-dir", required=True, type=Path, help="dir of <document_id>/{document.json,text.txt}"
    )
    p.add_argument(
        "--out-dir", required=True, type=Path, help="output dir (documents.jsonl, chunks.jsonl.gz)"
    )
    p.add_argument(
        "--max-words",
        type=int,
        default=350,
        help="split a section into paragraph-chunks above this",
    )
    p.add_argument(
        "--built-at",
        required=True,
        help="ISO-8601 timestamp for manifest.json's built_at — a stated value, never a clock read",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    document_ids = list(_document_ids(args.raw_dir))
    if not document_ids:
        print(
            f"no <document_id>/document.json + text.txt pairs found under {args.raw_dir}",
            file=sys.stderr,
        )
        return 1

    documents: list[DocumentRecord] = []
    all_chunks: list[DocumentChunk] = []
    for document_id in document_ids:
        try:
            doc = _load_document_record(args.raw_dir, document_id)
        except Exception as exc:  # noqa: BLE001 — report and continue with the rest
            print(f"{document_id}: skipped, {exc}", file=sys.stderr)
            continue
        text = (args.raw_dir / document_id / "text.txt").read_text(encoding="utf-8")
        chunks = build_chunks(document_id, text, max_words=args.max_words)
        if not chunks:
            print(f"{document_id}: no chunks produced (empty text.txt?)", file=sys.stderr)
            continue
        documents.append(doc)
        all_chunks.extend(chunks)
        print(f"{document_id}: {len(chunks)} chunk(s)", file=sys.stderr)

    documents.sort(key=lambda d: d.document_id)
    all_chunks.sort(key=lambda c: c.chunk_id)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    docs_path = args.out_dir / "documents.jsonl"
    with docs_path.open("w", encoding="utf-8", newline="\n") as f:
        for doc in documents:
            f.write(json.dumps(doc.model_dump(mode="json"), ensure_ascii=False))
            f.write("\n")

    chunks_path = args.out_dir / "chunks.jsonl.gz"
    with gzip.open(chunks_path, mode="wt", encoding="utf-8", newline="\n") as f:
        for chunk in all_chunks:
            f.write(json.dumps(chunk.model_dump(mode="json"), ensure_ascii=False))
            f.write("\n")

    manifest_path = args.out_dir / "manifest.json"
    existing_version = 0
    if manifest_path.exists():
        try:
            existing_version = int(
                json.loads(manifest_path.read_text(encoding="utf-8"))
                .get("version", "v0")
                .lstrip("v")
            )
        except (ValueError, json.JSONDecodeError):
            existing_version = 0
    manifest = {
        "version": f"v{existing_version + 1}",
        "built_at": args.built_at,
        "documents": len(documents),
        "chunks": len(all_chunks),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    print(
        f"wrote {len(documents)} document(s), {len(all_chunks)} chunk(s) to {args.out_dir}",
        file=sys.stderr,
    )
    print(
        "Next: scripts/build_parameter_registry.py propose, then hand-review "
        "the resulting parameters.csv before committing it.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
