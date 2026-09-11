"""The Phase 5 structured-parameter registry and the acquisition -> engine seam
(CLAUDE.md §14, §15, §18, §19, §22, §23).

:class:`SourcedParameter` is one auto-published registry row: a single financial
fact extracted from a document chunk by an extractor LLM and independently
confirmed by a different verifier LLM (``scripts/build_parameter_registry.py
extract``), carrying enough of a paper trail that the extraction itself is
checkable rather than merely asserted (see the two model-validators below and
``sources/knowledge/loader.py``'s third check against the cited chunk's actual
text). It is deliberately *not* a `FinancialInput` — it is the reviewed evidence
a `FinancialInput` is later built from
(``knowledge/plan_binding.py``), because a registry row can be resolved into many
different plans over its lifetime, while a `FinancialInput` is frozen and belongs
to exactly one.

:class:`ParameterName` is a closed enum on purpose: a number with no
`ParameterName` has nowhere to go, however confidently a document states it. This
is what stops "any number found in a PDF" from ever reaching the financial engine
(CLAUDE.md §3.1, §30).

:class:`FinanceKnowledgeEvidence` is the Phase 5 seam — the
`DemandEvidence` / `OpportunityEvidence` analogue (CLAUDE.md §26.1's pattern,
extended): one JSON-round-trippable object crossing from the impure acquisition
layer (``discovery/knowledge_acquisition.py`` — file I/O only, no network) into
the pure ``knowledge/resolver.py`` and ``knowledge/plan_binding.py``.

This module imports `Unit` from `models.finance` (one unit vocabulary, never a
parallel enum that could silently drift) but nothing else from that module, and
`models.finance` never imports this module — no cycle.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vyaparsarathi.errors import FinancialInputError
from vyaparsarathi.models.finance import Unit
from vyaparsarathi.models.knowledge import (
    ChunkLocator,
    Jurisdiction,
    KnowledgeAcquisitionReport,
    RetrievedPassage,
    SourceTier,
)
from vyaparsarathi.models.taxonomy import BusinessCategory


class ParameterName(StrEnum):
    """CLOSED enum of the financial facts this phase knows how to resolve and
    bind. Adding a new kind of sourced parameter means adding a member here,
    an entry in ``knowledge/parameter_spec.py``'s `PARAMETER_SPEC` table, and
    (if it should reach `FinancialPlanInput`) a case in
    ``knowledge/plan_binding.py`` — never an ad hoc string."""

    INTEREST_RATE_PCT = "interest_rate_pct"
    LOAN_TENURE_MONTHS = "loan_tenure_months"
    MORATORIUM_MONTHS = "moratorium_months"
    PROMOTER_MARGIN_PCT = "promoter_margin_pct"
    LOAN_CEILING_INR = "loan_ceiling_inr"
    SUBSIDY_PCT = "subsidy_pct"
    LICENCE_FEE_INR = "licence_fee_inr"
    SECURITY_DEPOSIT_MONTHS = "security_deposit_months"
    GROSS_MARGIN_PCT = "gross_margin_pct"  # sector benchmark; tier-gated
    COGS_PCT = "cogs_pct"  # sector benchmark; tier-gated
    INVENTORY_DAYS = "inventory_days"  # sector benchmark; tier-gated


class ValueNormalization(StrEnum):
    """The conversion applied to turn a document's printed ``value_token`` into
    `SourcedParameter.value`. Named explicitly (rather than left to a free-form
    parser) so :func:`normalize_value` can re-derive ``value`` from
    ``value_token`` and `SourcedParameter` can check the two agree — the third
    of the three checks that make "never fabricate a number" verifiable
    (`sources/knowledge/loader.py`, ``scripts/build_parameter_registry.py
    --verify``)."""

    AS_STATED = "as_stated"  # the printed number, unit unchanged
    PERCENT_TO_RATIO = "percent_to_ratio"  # "10%"   -> Decimal("0.10")
    PERCENT_AS_ANNUAL_RATE = "percent_as_annual_rate"  # "11.5%" -> Decimal("11.5")
    THOUSAND_TO_INR = "thousand_to_inr"  # "90 thousand" / "90k" -> Decimal("90000")
    LAKH_TO_INR = "lakh_to_inr"  # "2.5 lakh" -> Decimal("250000")
    CRORE_TO_INR = "crore_to_inr"  # "1.2 crore" -> Decimal("12000000")
    YEARS_TO_MONTHS = "years_to_months"  # "5 years" -> 60


_THOUSAND = Decimal("1000")
_LAKH = Decimal("100000")
_CRORE = Decimal("10000000")
# Deliberately permissive about comma grouping: Indian documents group digits
# 2-2-3 ("1,50,000"), not the Western 3-3-3 ("150,000"). Rather than encode one
# grouping convention, this matches any digits/commas and strips every comma
# before parsing — the grouping itself carries no information once removed.
# Deliberately `[0-9]`, not `\d`: Python's `\d` is Unicode-aware by default
# and would also match Devanagari digits (Decimal itself understands them
# too), silently "succeeding" on a script this system has made no decision
# to support — see `llm/language_guard.py` for the explicit detect-and-ask
# path this forces instead (never a silent misparse).
_NUMERAL_RE = re.compile(r"-?[0-9][0-9,]*(?:\.[0-9]+)?")


def normalize_value(value_token: str, normalization: ValueNormalization) -> Decimal | int:
    """Deterministically re-derive a `SourcedParameter.value` from the exact
    printed substring it was extracted from. Pure, stdlib-only (no I/O, no
    locale); used both by `SourcedParameter`'s own model-validator (so a
    fabricated or miscopied ``value`` is rejected at construction) and by
    ``scripts/build_parameter_registry.py --verify`` (so a tampered committed
    row is caught before it reaches a plan).

    Deliberately lives in this module, not in ``knowledge/parameter_spec.py``:
    a model in ``models/`` must not import from a higher-level package
    (CLAUDE.md §3.6 — mirrors why `models/finance.py` re-implements its own
    float-rejection guard rather than importing `finance/money.py`). The
    knowledge layer imports this function, not the reverse.

    Raises `FinancialInputError` on a token this normalization cannot parse —
    a caller error (the registry row would fail review), never a "missing
    evidence" case.
    """
    match = _NUMERAL_RE.search(value_token)
    if match is None:
        raise FinancialInputError(f"normalize_value: no numeral found in {value_token!r}")
    numeral = Decimal(match.group(0).replace(",", ""))

    if normalization is ValueNormalization.AS_STATED:
        return numeral
    if normalization is ValueNormalization.PERCENT_TO_RATIO:
        return numeral / Decimal("100")
    if normalization is ValueNormalization.PERCENT_AS_ANNUAL_RATE:
        return numeral
    if normalization is ValueNormalization.THOUSAND_TO_INR:
        return numeral * _THOUSAND
    if normalization is ValueNormalization.LAKH_TO_INR:
        return numeral * _LAKH
    if normalization is ValueNormalization.CRORE_TO_INR:
        return numeral * _CRORE
    if normalization is ValueNormalization.YEARS_TO_MONTHS:
        return int(numeral * 12)
    raise FinancialInputError(  # pragma: no cover — exhaustive over the enum above
        f"normalize_value: unhandled normalization {normalization!r}"
    )


class Applicability(BaseModel):
    """Who/where/when a `SourcedParameter` applies to. Every field narrows the
    set of plans the row is eligible for; an empty/`None` field means "this row
    does not restrict on this axis", never "this row applies to nothing"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    jurisdiction: Jurisdiction
    scheme: str | None = None  # None = not scheme-specific
    categories: tuple[BusinessCategory, ...] = ()  # empty = all categories
    activity_kind: str = ""  # verbatim, e.g. "manufacturing"; not matched against taxonomy
    min_loan_inr: Decimal | None = Field(default=None, ge=0)
    # True = the source states a strictly-greater-than bound ("above X" —
    # X itself does NOT qualify); False (default) = "X and above"/"at least
    # X" — X itself DOES qualify. A band's precise inclusive/exclusive
    # wording matters for real scheme eligibility (MUDRA's Kishor band is
    # "above Rs. 50,000", not "Rs. 50,000 and above" — Rs. 50,000 itself is
    # Shishu, not Kishor) — see `knowledge/resolver.py::_loan_band_ok`.
    min_loan_inr_exclusive: bool = False
    max_loan_inr: Decimal | None = Field(default=None, ge=0)
    # True = strictly-less-than ("less than Y"/"below Y" — Y itself does NOT
    # qualify); False (default) = "up to Y"/"not exceeding Y" — Y itself DOES
    # qualify.
    max_loan_inr_exclusive: bool = False
    effective_from: date | None = None
    effective_to: date | None = None
    # Verbatim qualifiers from the document (e.g. "subject to collateral-free
    # cover under CGTMSE"). CARRIED, never interpreted — CLAUDE.md §18: this
    # phase retrieves rules, it does not decide eligibility.
    conditions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _loan_band_shape(self) -> Applicability:
        if self.min_loan_inr_exclusive and self.min_loan_inr is None:
            raise ValueError("min_loan_inr_exclusive requires min_loan_inr to be set")
        if self.max_loan_inr_exclusive and self.max_loan_inr is None:
            raise ValueError("max_loan_inr_exclusive requires max_loan_inr to be set")
        if self.min_loan_inr is not None and self.max_loan_inr is not None:
            if self.max_loan_inr < self.min_loan_inr:
                raise ValueError("max_loan_inr must not be less than min_loan_inr")
            if self.max_loan_inr == self.min_loan_inr and (
                self.min_loan_inr_exclusive or self.max_loan_inr_exclusive
            ):
                raise ValueError(
                    "min_loan_inr == max_loan_inr with an exclusive bound describes an "
                    "empty band — no amount can ever satisfy it"
                )
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not precede effective_from")
        return self

    def specificity(self) -> tuple[int, int, int, int]:
        """(jurisdiction, scheme, category, loan-band) specificity, each 0 or 1.
        Higher is narrower. The deterministic ordering key for
        `knowledge/resolver.py`'s precedence rule 2 — never used to select a
        value on its own, only to rank survivors that already passed every
        filter."""
        jurisdiction_specificity = {
            "national": 0,
            "state": 1,
            "district": 2,
        }[self.jurisdiction.level.value]
        return (
            jurisdiction_specificity,
            1 if self.scheme else 0,
            1 if self.categories else 0,
            1 if (self.min_loan_inr is not None or self.max_loan_inr is not None) else 0,
        )


class SourcedParameter(BaseModel):
    """One auto-published registry row — the structured evidence record.
    Frozen: a row is not edited in place; a correction is a new row (with a
    new ``parameter_id``) extracted and verified again.

    A row is published only by ``scripts/build_parameter_registry.py
    extract``'s genuinely blind dual-LLM gate: two different models —
    extractor and verifier — each independently receive the same chunk,
    document jurisdiction, and allowed-schema instructions, and each
    independently return at most one candidate. Neither model is ever shown
    the other's answer or asked to check it; the two candidates are compared
    only after both responses are in hand, by exact deterministic
    canonicalization (never fuzzy-matched). A row is published only when the
    two independently produced candidates agree; any disagreement — full or
    partial, including one side finding nothing — drops the row silently,
    never queued for human review (see that script's module docstring for
    the full gate). ``extractor_model``/``verifier_model`` record which two
    models independently agreed; ``verified_on`` is the date that gate ran.

    Two validators enforce the first two of the three "never fabricate a
    number" checks (CLAUDE.md §3.5, §30); the third (``evidence_quote`` occurs
    verbatim in the cited chunk's actual text) can only run where the chunk is
    available, so it lives in `sources/knowledge/loader.py`, not here.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    parameter_id: str = Field(min_length=1)  # f"{document_id}:{name}:{nn}"
    name: ParameterName
    value: Decimal | int = Field(ge=0)
    unit: Unit
    value_token: str = Field(min_length=1)  # the exact printed substring, e.g. "10%"
    normalization: ValueNormalization
    evidence_quote: str = Field(min_length=1)  # the verbatim sentence the token came from
    document_id: str = Field(min_length=1)
    chunk_id: str = Field(min_length=1)
    locator: ChunkLocator
    tier: SourceTier
    applicability: Applicability
    reference_date: date | None = None  # the date the RULE describes, not retrieval
    is_benchmark: bool = False  # a sector benchmark is never a scheme rule
    extractor_model: str = Field(min_length=1)  # e.g. "gemini-3.6-flash"
    verifier_model: str = Field(min_length=1)  # e.g. "gemini-3.5-flash" — a DIFFERENT model
    verified_on: date  # when the dual-LLM gate ran — an operator-stated CLI value,
    # never a clock read (CLAUDE.md §28)
    notes: str = ""

    @field_validator("value", mode="before")
    @classmethod
    def _reject_float_value(cls, v: object) -> object:
        # Same mode="before" reasoning as FinancialInput._reject_float_value:
        # pydantic's Decimal | int coercion runs before an "after" validator.
        if isinstance(v, float):
            raise FinancialInputError(
                f"SourcedParameter.value rejects float ({v!r}); pass an int or a Decimal "
                "(CLAUDE.md §4.2)."
            )
        return v

    @model_validator(mode="after")
    def _value_token_in_quote(self) -> SourcedParameter:
        if self.value_token not in self.evidence_quote:
            raise ValueError(
                f"value_token {self.value_token!r} does not occur in evidence_quote "
                "— a reviewed row must be traceable to the printed substring it came from"
            )
        return self

    @model_validator(mode="after")
    def _value_matches_normalization(self) -> SourcedParameter:
        expected = normalize_value(self.value_token, self.normalization)
        if expected != self.value:
            raise ValueError(
                f"normalize_value({self.value_token!r}, {self.normalization!r}) == "
                f"{expected!r}, but value == {self.value!r} — the row's stated value "
                "does not match its own declared normalization"
            )
        return self


class ParameterQuery(BaseModel):
    """What a caller is asking `knowledge/resolver.py::resolve_parameters` for.
    ``as_of`` is a STATED date, supplied by the caller — never `date.today()`
    (CLAUDE.md §28: no clock read in a deterministic path)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    names: tuple[ParameterName, ...] = Field(min_length=1)
    category: BusinessCategory | None = None
    state: str | None = None
    district: str | None = None
    scheme: str | None = None
    loan_amount_inr: Decimal | None = Field(default=None, ge=0)
    as_of: date | None = None


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    NO_EVIDENCE = "no_evidence"
    CONFLICTING = "conflicting"
    STALE_ONLY = "stale_only"
    CONDITIONS_UNRESOLVED = "conditions_unresolved"


class ParameterResolution(BaseModel):
    """The outcome of resolving one `ParameterName` against the registry.
    ``chosen`` is populated **only** when ``status is RESOLVED`` — every other
    status leaves it `None`, so a caller can never mistake a conflicting or
    stale reading for a usable one by skipping the status check."""

    model_config = ConfigDict(extra="forbid")

    name: ParameterName
    status: ResolutionStatus
    chosen: SourcedParameter | None = None
    candidates: list[SourcedParameter] = Field(default_factory=list)
    rejected_reasons: dict[str, str] = Field(default_factory=dict)  # parameter_id -> why
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confidence_basis: dict[str, float | int | str | None] = Field(default_factory=dict)
    agreeing_document_ids: tuple[str, ...] = ()
    source: str = ""  # f"knowledge:{document_id}" -> FinancialInput.source
    source_ref: str = ""  # f"{document_id}#{locator.as_ref()}" -> FinancialInput.source_ref
    # The chosen row's source DocumentRecord.retrieved_at (when the OPERATOR
    # downloaded that document) -> FinancialInput.retrieved_at. Populated by
    # the resolver, which already has document access, so
    # knowledge/plan_binding.py never needs a documents lookup of its own and
    # FinanceKnowledgeEvidence stays a flat, self-contained seam. Deliberately
    # NOT the run's own clock — see knowledge/resolver.py's module docstring.
    retrieved_at: datetime | None = None
    citation: str = ""  # human-readable, for the DPR. NEVER enters FinancialInput.
    notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _chosen_only_when_resolved(self) -> ParameterResolution:
        if self.status is ResolutionStatus.RESOLVED and self.chosen is None:
            raise ValueError("a RESOLVED resolution must carry chosen")
        if self.status is not ResolutionStatus.RESOLVED and self.chosen is not None:
            raise ValueError(
                f"a {self.status.value} resolution must not carry chosen "
                "— only RESOLVED may select a value"
            )
        return self


class FinanceKnowledgeEvidence(BaseModel):
    """THE PHASE 5 SEAM. Pure input to `knowledge/plan_binding.py`'s
    `bind_sourced_inputs` / `build_loan_terms` — the `DemandEvidence` /
    `OpportunityEvidence` analogue. Built by the impure acquisition layer
    (`discovery/knowledge_acquisition.py`), consumed by pure code only."""

    model_config = ConfigDict(extra="forbid")

    query: ParameterQuery
    resolutions: list[ParameterResolution] = Field(default_factory=list)
    passages: list[RetrievedPassage] = Field(default_factory=list)
    acquisition: KnowledgeAcquisitionReport = Field(default_factory=KnowledgeAcquisitionReport)
    warnings: list[str] = Field(default_factory=list)
    acquired_at: datetime  # the ONLY wall-clock read in this phase, at the boundary

    def resolution_for(self, name: ParameterName) -> ParameterResolution | None:
        for resolution in self.resolutions:
            if resolution.name is name:
                return resolution
        return None
