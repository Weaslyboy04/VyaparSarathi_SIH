"""The Phase 5 knowledge-corpus domain models (CLAUDE.md §5, §18, §19, §23).

A :class:`DocumentRecord` is one official/credible source document an operator has
downloaded and reviewed offline (never fetched at request time — CLAUDE.md §21's
crawling policy does not apply here; there is no crawler). A :class:`DocumentChunk`
is one section-aware slice of that document's text, addressed by a
:class:`ChunkLocator` that is deliberately a machine-parsable pointer (page/section/
paragraph), never prose — a document *title* must never end up inside a
`FinancialInput.source_ref`, because that string is echoed into
`FinancialAssessmentResult` and scanned by `tests/test_finance_guardrails.py`.

:class:`RetrievedPassage` is evidence for *display and citation only*. It is never
the mechanism by which a number reaches the financial engine — that is
:class:`~vyaparsarathi.models.parameters.SourcedParameter`, resolved by
``knowledge/resolver.py`` without ever consulting a retriever (see
``knowledge/plan_binding.py``'s module docstring).

:class:`KnowledgeAcquisitionReport` is the coverage analogue of
:class:`~vyaparsarathi.models.demand.DemandAcquisitionReport`: every degradation
(missing corpus, a row that failed quote verification, a parse error) is counted
here rather than raised, so the acquisition layer never crashes and never fabricates
data (CLAUDE.md §6.1, §33).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vyaparsarathi.models.taxonomy import BusinessCategory


class SourceTier(StrEnum):
    """How authoritative a document's publisher is. Used both to floor which
    tiers may supply a given :class:`~vyaparsarathi.models.parameters.ParameterName`
    (``knowledge/parameter_spec.py``) and to weight confidence
    (``knowledge/confidence.py``). Ordered most to least authoritative; the
    ordering itself is read from ``knowledge_config.py``'s tier-weight table,
    never inferred from declaration order here."""

    GOVT_PRIMARY = "govt_primary"  # ministry / gazette notification / scheme guideline
    REGULATOR = "regulator"  # a central regulator's circular
    PUBLIC_SECTOR_INSTITUTION = "public_sector_institution"  # development bank / PSU bank
    INDUSTRY_BODY = "industry_body"  # a recognised trade/industry association report
    SECONDARY = "secondary"  # commentary / press coverage; never sufficient alone


class JurisdictionLevel(StrEnum):
    NATIONAL = "national"
    STATE = "state"
    DISTRICT = "district"


class Jurisdiction(BaseModel):
    """The administrative scope a document or a parameter applies to. Deliberately
    a separate enum from :class:`~vyaparsarathi.models.demand.GeographyLevel`
    (CLAUDE.md §7, §30): that model describes the geography a *population figure*
    enumerates and has no ``NATIONAL`` member; adding one would change
    ``DemandEvidence`` serialization, a Phase 2C change Phase 5 has no business
    making."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: JurisdictionLevel
    state: str | None = None  # required unless level is NATIONAL
    district: str | None = None  # required when level is DISTRICT

    @model_validator(mode="after")
    def _scope_shape(self) -> Jurisdiction:
        if self.level is JurisdictionLevel.NATIONAL:
            if self.state is not None or self.district is not None:
                raise ValueError("a NATIONAL jurisdiction must not carry state or district")
        elif self.level is JurisdictionLevel.STATE:
            if not self.state:
                raise ValueError("a STATE jurisdiction requires state")
            if self.district is not None:
                raise ValueError("a STATE jurisdiction must not carry district")
        elif self.level is JurisdictionLevel.DISTRICT:
            if not self.state or not self.district:
                raise ValueError("a DISTRICT jurisdiction requires both state and district")
        return self


class ChunkLocator(BaseModel):
    """A machine-parsable pointer into one document. Deliberately carries no
    document title and no section *heading text* — only numerals and the
    operator-assigned ``document_id`` (added by the caller when building
    ``chunk_id``/``source_ref``) can reach a :class:`~vyaparsarathi.models.finance
    .FinancialInput`. Prose citations live on
    :class:`~vyaparsarathi.models.parameters.ParameterResolution.citation`, which
    never crosses into a `FinancialPlanInput`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    page_from: int | None = Field(default=None, ge=1)
    page_to: int | None = Field(default=None, ge=1)
    section: str = ""  # as printed, e.g. "4.2" — never a heading's prose text
    paragraph_index: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _page_range_shape(self) -> ChunkLocator:
        if self.page_to is not None and self.page_from is None:
            raise ValueError("page_to requires page_from")
        if (
            self.page_from is not None
            and self.page_to is not None
            and self.page_to < self.page_from
        ):
            raise ValueError("page_to must not precede page_from")
        return self

    def as_ref(self) -> str:
        """An ASCII, stable, machine-parsable rendering — the part of a
        `FinancialInput.source_ref` that follows the ``#``. Empty components are
        omitted rather than rendered as ``None`` or ``""``."""
        parts: list[str] = []
        if self.page_from is not None:
            if self.page_to is not None and self.page_to != self.page_from:
                parts.append(f"p{self.page_from}-{self.page_to}")
            else:
                parts.append(f"p{self.page_from}")
        if self.section:
            parts.append(f"s{self.section}")
        if self.paragraph_index is not None:
            parts.append(f"para{self.paragraph_index}")
        return "/".join(parts) if parts else "loc"


class KnowledgeTopic(StrEnum):
    """A coarse subject tag on a chunk, used by the retrieval metadata prefilter
    (CLAUDE.md §19: "filtering by metadata ... over pure nearest-neighbour")."""

    SCHEME_FINANCING = "scheme_financing"
    LICENSING_COMPLIANCE = "licensing_compliance"
    TAX_THRESHOLD = "tax_threshold"
    SECTOR_BENCHMARK = "sector_benchmark"
    SUBSIDY = "subsidy"


class DocumentRecord(BaseModel):
    """One official/credible source document, downloaded and recorded by an
    operator (``scripts/build_knowledge_corpus.py``) — never fetched by this
    package at request time. ``retrieved_at`` is *when the operator downloaded
    it*; this is the timestamp that ultimately reaches
    `FinancialInput.retrieved_at`, deliberately never the run's own clock, so a
    binding stays reproducible across runs (CLAUDE.md §28)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    title: str
    publisher: str
    tier: SourceTier
    jurisdiction: Jurisdiction
    published_on: date | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    version: str = ""
    superseded_by: str | None = None  # another document_id, when known
    retrieval_url: str = ""  # recorded by the operator; never fetched at runtime
    retrieved_at: datetime  # when the OPERATOR downloaded it — not a run clock
    content_sha256: str = Field(min_length=64, max_length=64)
    page_count: int | None = Field(default=None, ge=1)
    language: str = "en"
    licence_note: str = ""  # redistribution status of this document's text

    @model_validator(mode="after")
    def _effective_window_shape(self) -> DocumentRecord:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not precede effective_from")
        return self


class DocumentChunk(BaseModel):
    """One section-aware slice of a document's text (CLAUDE.md §19: "chunk
    documents sensibly (section-aware)"). ``text_sha256`` lets the loader verify
    a registry row's ``evidence_quote`` was actually drawn from this exact text,
    not a since-edited version of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)  # f"{document_id}#{locator.as_ref()}", unique per chunk
    document_id: str
    locator: ChunkLocator
    heading_path: tuple[str, ...] = ()  # e.g. ("4", "4.2", "Promoter contribution")
    text: str = Field(min_length=1)
    token_count: int = Field(ge=0)
    topics: tuple[KnowledgeTopic, ...] = ()
    schemes: tuple[str, ...] = ()  # operator-assigned scheme slugs this chunk discusses
    categories: tuple[BusinessCategory, ...] = ()  # empty = not category-specific
    text_sha256: str = Field(min_length=64, max_length=64)


class RetrievedPassage(BaseModel):
    """Evidence for display/citation only — NEVER the mechanism by which a
    number reaches a calculation (CLAUDE.md §18: "RAG must NOT be the mechanism
    for" a computed fact). Field is named ``relevance``, not ``score``: `"score"`
    is a banned key substring in `tests/test_finance_guardrails.py`, and a Phase 5
    model must never carry a field that would trip it if ever nested into a
    result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk: DocumentChunk
    relevance: float = Field(ge=0.0)
    matched_terms: tuple[str, ...] = ()
    tier: SourceTier
    citation: str = ""  # human-readable; never crosses into FinancialPlanInput


class KnowledgeAcquisitionReport(BaseModel):
    """Coverage summary for one corpus load — the
    :class:`~vyaparsarathi.models.demand.DemandAcquisitionReport` analogue.
    Every degradation is counted here, never raised past the acquisition
    boundary (CLAUDE.md §6.1, §33): a missing corpus, a row whose evidence quote
    could not be verified, a row pointing at an unknown chunk, a row below a
    parameter's tier floor, and any parse error. ``errors`` carries stringified
    exceptions — "typed acquisition exceptions, stringified", the same idiom as
    `DemandAcquisitionReport.errors`."""

    model_config = ConfigDict(extra="forbid")

    corpus_present: bool = False
    corpus_manifest_version: str = ""
    corpus_built_at: datetime | None = None
    documents_loaded: int = 0
    chunks_loaded: int = 0
    parameters_loaded: int = 0
    parameters_rejected_unverified_quote: int = 0
    parameters_rejected_unknown_chunk: int = 0
    parameters_rejected_tier_floor: int = 0
    parse_errors: int = 0
    documents_by_tier: dict[SourceTier, int] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
