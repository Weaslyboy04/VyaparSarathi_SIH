"""The DPR's provenance primitive (CLAUDE.md §23's four-way distinction, plus
two states a *report* needs that an engine result does not: declared
configuration, and an explicit "not available"). PURE.

Every figure and claim the DPR prints is wrapped in a `ProvenancedValue` so a
reader — and `tests/test_dpr_provenance.py` — can always answer: did the
entrepreneur say this, did a cited document say it, did a deterministic
calculation produce it, is it a stated assumption, or is the evidence simply
absent? There is no sixth option; the assembler may not emit a bare string
where a `ProvenancedValue` belongs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from vyaparsarathi.dpr.format import INPUT_REQUIRED, NOT_AVAILABLE


class ValueOrigin(StrEnum):
    USER_PROVIDED = "user_provided"  # the entrepreneur stated it; unverified
    SOURCED = "sourced"  # an official document said it; carries a citation
    CALCULATED = "calculated"  # a deterministic engine produced it; names its inputs
    ASSUMED = "assumed"  # a configured MVP convention; carries a rationale
    # The SIH26091 problem statement's own declared 10%/90% structure etc. —
    # configuration this deployment states, NEVER a retrieved scheme rule
    # (kept distinct so it can never be cited as external evidence).
    DECLARED_CONFIG = "declared_config"
    NOT_AVAILABLE = "not_available"  # input or evidence absent — shown, never hidden


class GapReason(StrEnum):
    NO_EVIDENCE = "no_evidence"  # -> "Not available from current evidence"
    INPUT_REQUIRED = "input_required"  # -> "Additional input required"
    DECLINED = "declined"  # the entrepreneur was asked and declined
    AMBIGUOUS = "ambiguous"  # stated but unresolved (e.g. several "Bhagwanpur"s)


_GAP_TEXT: dict[GapReason, str] = {
    GapReason.NO_EVIDENCE: NOT_AVAILABLE,
    GapReason.INPUT_REQUIRED: INPUT_REQUIRED,
    GapReason.DECLINED: "The entrepreneur declined to provide this",
    GapReason.AMBIGUOUS: "Stated but unresolved — needs disambiguation",
}


class ProvenancedValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    label: str
    display: str  # already formatted for print
    origin: ValueOrigin
    raw: str | None = None  # the machine value as a string, for JSON/audit
    citation_id: str | None = None  # SOURCED only
    rationale: str = ""  # ASSUMED / DECLARED_CONFIG / a gap explanation
    inputs: tuple[str, ...] = ()  # CALCULATED only — the named inputs it rests on
    gap_reason: GapReason | None = None  # NOT_AVAILABLE only
    note: str = ""

    @model_validator(mode="after")
    def _shape(self) -> ProvenancedValue:
        if self.origin is ValueOrigin.SOURCED and not self.citation_id:
            raise ValueError("a SOURCED ProvenancedValue requires a citation_id")
        if self.origin is ValueOrigin.CALCULATED and not self.inputs:
            raise ValueError("a CALCULATED ProvenancedValue must name its inputs")
        if self.origin in (ValueOrigin.ASSUMED, ValueOrigin.DECLARED_CONFIG):
            if not self.rationale.strip():
                raise ValueError(f"a {self.origin.value} ProvenancedValue requires a rationale")
        if self.origin is ValueOrigin.NOT_AVAILABLE:
            if self.gap_reason is None:
                raise ValueError("a NOT_AVAILABLE ProvenancedValue requires a gap_reason")
            if self.citation_id or self.inputs or self.raw:
                raise ValueError("a NOT_AVAILABLE ProvenancedValue must not carry a value")
        return self


def pv_user(
    label: str, display: str, *, raw: str | None = None, note: str = ""
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label, display=display, origin=ValueOrigin.USER_PROVIDED, raw=raw, note=note
    )


def pv_sourced(
    label: str, display: str, *, citation_id: str, raw: str | None = None, note: str = ""
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label,
        display=display,
        origin=ValueOrigin.SOURCED,
        citation_id=citation_id,
        raw=raw,
        note=note,
    )


def pv_calc(
    label: str,
    display: str,
    *,
    inputs: tuple[str, ...],
    raw: str | None = None,
    note: str = "",
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label,
        display=display,
        origin=ValueOrigin.CALCULATED,
        inputs=inputs,
        raw=raw,
        note=note,
    )


def pv_assumed(
    label: str, display: str, *, rationale: str, raw: str | None = None, note: str = ""
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label,
        display=display,
        origin=ValueOrigin.ASSUMED,
        rationale=rationale,
        raw=raw,
        note=note,
    )


def pv_config(
    label: str, display: str, *, rationale: str, raw: str | None = None, note: str = ""
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label,
        display=display,
        origin=ValueOrigin.DECLARED_CONFIG,
        rationale=rationale,
        raw=raw,
        note=note,
    )


def pv_missing(
    label: str, *, reason: GapReason = GapReason.NO_EVIDENCE, note: str = ""
) -> ProvenancedValue:
    return ProvenancedValue(
        label=label,
        display=_GAP_TEXT[reason],
        origin=ValueOrigin.NOT_AVAILABLE,
        gap_reason=reason,
        note=note,
    )


def is_gap(value: ProvenancedValue) -> bool:
    return value.origin is ValueOrigin.NOT_AVAILABLE


__all__ = [
    "GapReason",
    "ProvenancedValue",
    "ValueOrigin",
    "is_gap",
    "pv_assumed",
    "pv_calc",
    "pv_config",
    "pv_missing",
    "pv_sourced",
    "pv_user",
]
