"""Read the committed Phase 5 knowledge corpus — documents, chunks, and the
reviewed parameter registry (CLAUDE.md §18, §19, §23, §33).

The corpus lives as plain, committed files under one directory (default
``data/knowledge/``, see ``data/knowledge/SOURCES.md``):

* ``documents.jsonl`` — one :class:`DocumentRecord` per line.
* ``chunks.jsonl.gz`` (or ``chunks.jsonl``, uncompressed — tests use the plain
  form for readability; the loader accepts either) — one
  :class:`DocumentChunk` per line.
* ``parameters.csv`` — one reviewed :class:`SourcedParameter` per row, in the
  flattened column layout this module reads and
  ``scripts/build_parameter_registry.py`` writes.
* ``manifest.json`` — corpus build metadata (version, build timestamp).

This is the file-backed, offline analogue of
``sources/census/loader.py::CensusVillageSource``: a missing directory or
missing file degrades to an empty, ``corpus_present=False`` result; a
malformed line is skipped and counted, never raised. Nothing here ever makes
a network call.

**Three checks make "never fabricate a number" verifiable, not merely
asserted** (CLAUDE.md §3.5, §30). Two run inside a `SourcedParameter`'s own
pydantic validators (``models/parameters.py``): ``value_token`` must occur in
``evidence_quote``, and ``normalize_value(value_token, normalization)`` must
equal ``value``. The third can only run here, where the cited chunk's actual
text is available: ``evidence_quote`` must occur verbatim (after whitespace
normalization) in that chunk's ``text``. A row failing any of the three —
including a row that fails the first two at CSV-parse time — is dropped and
counted in :class:`KnowledgeAcquisitionReport`, never silently kept.

This module reads ``PARAMETER_SPEC`` only for its tier-floor check (a row
whose tier is not credible enough for its `ParameterName` is dropped here,
before any resolver logic runs); it does not check units — the unit check
belongs to ``knowledge/plan_binding.py``, which rejects rather than coerces a
mismatch when binding into a `FinancialPlanInput` field.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from vyaparsarathi.config import Settings, get_settings
from vyaparsarathi.knowledge.parameter_spec import PARAMETER_SPEC
from vyaparsarathi.models.finance import Unit
from vyaparsarathi.models.knowledge import (
    ChunkLocator,
    DocumentChunk,
    DocumentRecord,
    Jurisdiction,
    JurisdictionLevel,
    KnowledgeAcquisitionReport,
    SourceTier,
)
from vyaparsarathi.models.parameters import (
    Applicability,
    ParameterName,
    SourcedParameter,
    ValueNormalization,
)
from vyaparsarathi.models.taxonomy import BusinessCategory
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

_DOCUMENTS_FILE = "documents.jsonl"
_CHUNKS_FILE_GZ = "chunks.jsonl.gz"
_CHUNKS_FILE_PLAIN = "chunks.jsonl"
_PARAMETERS_FILE = "parameters.csv"
_MANIFEST_FILE = "manifest.json"

_PARAM_CSV_FIELDNAMES: tuple[str, ...] = (
    "parameter_id",
    "name",
    "value",
    "unit",
    "value_token",
    "normalization",
    "evidence_quote",
    "document_id",
    "chunk_id",
    "locator_page_from",
    "locator_page_to",
    "locator_section",
    "locator_paragraph_index",
    "tier",
    "appl_jurisdiction_level",
    "appl_jurisdiction_state",
    "appl_jurisdiction_district",
    "appl_scheme",
    "appl_categories",
    "appl_activity_kind",
    "appl_min_loan_inr",
    "appl_max_loan_inr",
    "appl_effective_from",
    "appl_effective_to",
    "appl_conditions",
    "reference_date",
    "is_benchmark",
    "reviewed_by",
    "reviewed_on",
    "notes",
)


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _lines(path: Path) -> Iterator[str]:
    if path.suffix == ".gz":
        with gzip.open(path, mode="rt", encoding="utf-8", newline="") as handle:
            yield from handle
    else:
        with path.open(encoding="utf-8", newline="") as handle:
            yield from handle


def _empty_or(value: str) -> str | None:
    v = value.strip()
    return v if v else None


def _optional_int(value: str) -> int | None:
    v = value.strip()
    return int(v) if v else None


def _optional_decimal(value: str) -> Decimal | None:
    v = value.strip()
    return Decimal(v) if v else None


def _optional_date(value: str) -> date | None:
    v = value.strip()
    return date.fromisoformat(v) if v else None


def _multi(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _row_to_parameter(row: dict[str, str]) -> SourcedParameter:
    """Un-flatten one ``parameters.csv`` row into a `SourcedParameter`. Raises
    on any malformation (bad enum value, bad Decimal, a validator failure on
    the model itself); the caller counts and continues."""
    locator = ChunkLocator(
        page_from=_optional_int(row.get("locator_page_from", "")),
        page_to=_optional_int(row.get("locator_page_to", "")),
        section=row.get("locator_section", "").strip(),
        paragraph_index=_optional_int(row.get("locator_paragraph_index", "")),
    )
    jurisdiction = Jurisdiction(
        level=JurisdictionLevel(row["appl_jurisdiction_level"]),
        state=_empty_or(row.get("appl_jurisdiction_state", "")),
        district=_empty_or(row.get("appl_jurisdiction_district", "")),
    )
    categories = tuple(BusinessCategory(c) for c in _multi(row.get("appl_categories", "")))
    applicability = Applicability(
        jurisdiction=jurisdiction,
        scheme=_empty_or(row.get("appl_scheme", "")),
        categories=categories,
        activity_kind=row.get("appl_activity_kind", "").strip(),
        min_loan_inr=_optional_decimal(row.get("appl_min_loan_inr", "")),
        max_loan_inr=_optional_decimal(row.get("appl_max_loan_inr", "")),
        effective_from=_optional_date(row.get("appl_effective_from", "")),
        effective_to=_optional_date(row.get("appl_effective_to", "")),
        conditions=_multi(row.get("appl_conditions", "")),
    )
    value_raw = row["value"].strip()
    value: Decimal | int
    try:
        value = int(value_raw)
    except ValueError:
        value = Decimal(value_raw)  # raises InvalidOperation on genuine garbage

    return SourcedParameter(
        parameter_id=row["parameter_id"],
        name=ParameterName(row["name"]),
        value=value,
        unit=Unit(row["unit"]),
        value_token=row["value_token"],
        normalization=ValueNormalization(row["normalization"]),
        evidence_quote=row["evidence_quote"],
        document_id=row["document_id"],
        chunk_id=row["chunk_id"],
        locator=locator,
        tier=SourceTier(row["tier"]),
        applicability=applicability,
        reference_date=_optional_date(row.get("reference_date", "")),
        is_benchmark=row.get("is_benchmark", "").strip().lower() == "true",
        reviewed_by=row["reviewed_by"],
        reviewed_on=date.fromisoformat(row["reviewed_on"]),
        notes=row.get("notes", ""),
    )


class FileCorpusStore:
    """Loads the committed knowledge corpus from disk once at construction.
    Implements the `~vyaparsarathi.knowledge.base.CorpusStore` protocol.
    Never raises — every failure is caught, counted, and surfaced through
    :meth:`report`."""

    def __init__(
        self, directory: str | Path | None = None, settings: Settings | None = None
    ) -> None:
        s = settings or get_settings()
        self._dir = Path(directory if directory is not None else s.knowledge_corpus_dir)
        (
            self._documents,
            self._chunks,
            self._parameters,
            self._report,
        ) = self._load()

    @property
    def path(self) -> Path:
        return self._dir

    def documents(self) -> Sequence[DocumentRecord]:
        return list(self._documents.values())

    def chunks(self) -> Sequence[DocumentChunk]:
        return list(self._chunks.values())

    def parameters(self) -> Sequence[SourcedParameter]:
        return list(self._parameters)

    def document(self, document_id: str) -> DocumentRecord | None:
        return self._documents.get(document_id)

    def chunk(self, chunk_id: str) -> DocumentChunk | None:
        return self._chunks.get(chunk_id)

    def report(self) -> KnowledgeAcquisitionReport:
        return self._report

    # -- loading -------------------------------------------------------

    def _load(
        self,
    ) -> tuple[
        dict[str, DocumentRecord],
        dict[str, DocumentChunk],
        list[SourcedParameter],
        KnowledgeAcquisitionReport,
    ]:
        if not self._dir.is_dir():
            logger.info("knowledge corpus not found at %s", self._dir)
            return {}, {}, [], KnowledgeAcquisitionReport(corpus_present=False)

        errors: list[str] = []
        parse_errors = 0

        manifest_version = ""
        manifest_built_at: datetime | None = None
        manifest_path = self._dir / _MANIFEST_FILE
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                manifest_version = str(manifest.get("version", ""))
                built_at_raw = manifest.get("built_at")
                if built_at_raw:
                    manifest_built_at = datetime.fromisoformat(built_at_raw)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                logger.warning("knowledge corpus manifest at %s unreadable: %s", manifest_path, exc)
                errors.append(f"manifest: {exc}")

        documents = self._load_documents()
        parse_errors += documents[1]
        chunks = self._load_chunks()
        parse_errors += chunks[1]

        (
            loaded_parameters,
            param_parse_errors,
            rejected_unverified_quote,
            rejected_unknown_chunk,
            rejected_tier_floor,
        ) = self._load_parameters(chunks[0])
        parse_errors += param_parse_errors

        documents_by_tier: dict[SourceTier, int] = {}
        for doc in documents[0].values():
            documents_by_tier[doc.tier] = documents_by_tier.get(doc.tier, 0) + 1

        report = KnowledgeAcquisitionReport(
            corpus_present=True,
            corpus_manifest_version=manifest_version,
            corpus_built_at=manifest_built_at,
            documents_loaded=len(documents[0]),
            chunks_loaded=len(chunks[0]),
            parameters_loaded=len(loaded_parameters),
            parameters_rejected_unverified_quote=rejected_unverified_quote,
            parameters_rejected_unknown_chunk=rejected_unknown_chunk,
            parameters_rejected_tier_floor=rejected_tier_floor,
            parse_errors=parse_errors,
            documents_by_tier=documents_by_tier,
            errors=errors,
        )
        return documents[0], chunks[0], loaded_parameters, report

    def _load_documents(self) -> tuple[dict[str, DocumentRecord], int]:
        path = self._dir / _DOCUMENTS_FILE
        if not path.exists():
            return {}, 0
        documents: dict[str, DocumentRecord] = {}
        parse_errors = 0
        for line_no, line in enumerate(_lines(path), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload: Any = json.loads(stripped)
                doc = DocumentRecord.model_validate(payload)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                parse_errors += 1
                logger.warning("%s:%d: malformed document record: %s", path, line_no, exc)
                continue
            documents[doc.document_id] = doc
        return documents, parse_errors

    def _load_chunks(self) -> tuple[dict[str, DocumentChunk], int]:
        gz_path = self._dir / _CHUNKS_FILE_GZ
        plain_path = self._dir / _CHUNKS_FILE_PLAIN
        path = gz_path if gz_path.exists() else (plain_path if plain_path.exists() else None)
        if path is None:
            return {}, 0

        chunks: dict[str, DocumentChunk] = {}
        parse_errors = 0
        for line_no, line in enumerate(_lines(path), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload: Any = json.loads(stripped)
                chunk = DocumentChunk.model_validate(payload)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                parse_errors += 1
                logger.warning("%s:%d: malformed chunk: %s", path, line_no, exc)
                continue
            actual_hash = hashlib.sha256(chunk.text.encode("utf-8")).hexdigest()
            if actual_hash != chunk.text_sha256:
                parse_errors += 1
                logger.warning(
                    "%s:%d: chunk %s text_sha256 mismatch (corpus file may be corrupted)",
                    path,
                    line_no,
                    chunk.chunk_id,
                )
                continue
            chunks[chunk.chunk_id] = chunk
        return chunks, parse_errors

    def _load_parameters(
        self, chunks: dict[str, DocumentChunk]
    ) -> tuple[list[SourcedParameter], int, int, int, int]:
        path = self._dir / _PARAMETERS_FILE
        if not path.exists():
            return [], 0, 0, 0, 0

        loaded: list[SourcedParameter] = []
        parse_errors = 0
        rejected_unverified_quote = 0
        rejected_unknown_chunk = 0
        rejected_tier_floor = 0

        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            for row_no, row in enumerate(reader, start=2):  # header is row 1
                try:
                    param = _row_to_parameter(row)
                except (ValueError, TypeError, KeyError, InvalidOperation) as exc:
                    parse_errors += 1
                    logger.warning("%s:%d: malformed parameter row: %s", path, row_no, exc)
                    continue

                chunk = chunks.get(param.chunk_id)
                if chunk is None:
                    rejected_unknown_chunk += 1
                    logger.warning(
                        "%s:%d: parameter %s cites unknown chunk_id %r",
                        path,
                        row_no,
                        param.parameter_id,
                        param.chunk_id,
                    )
                    continue

                if _normalize_whitespace(param.evidence_quote) not in _normalize_whitespace(
                    chunk.text
                ):
                    rejected_unverified_quote += 1
                    logger.warning(
                        "%s:%d: parameter %s evidence_quote not found in cited chunk %s",
                        path,
                        row_no,
                        param.parameter_id,
                        param.chunk_id,
                    )
                    continue

                spec = PARAMETER_SPEC.get(param.name)
                if spec is not None and param.tier not in spec.allowed_tiers:
                    rejected_tier_floor += 1
                    logger.warning(
                        "%s:%d: parameter %s tier %s is below the floor for %s",
                        path,
                        row_no,
                        param.parameter_id,
                        param.tier.value,
                        param.name.value,
                    )
                    continue

                loaded.append(param)

        return (
            loaded,
            parse_errors,
            rejected_unverified_quote,
            rejected_unknown_chunk,
            rejected_tier_floor,
        )


__all__ = ["FileCorpusStore", "PARAM_CSV_FIELDNAMES"]

# Re-exported without the leading underscore: `scripts/build_parameter_registry.py`
# writes exactly this column layout, so both sides read from one definition.
PARAM_CSV_FIELDNAMES = _PARAM_CSV_FIELDNAMES
