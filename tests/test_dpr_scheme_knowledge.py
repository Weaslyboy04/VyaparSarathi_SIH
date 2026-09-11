"""Priority 3 fix from the SIH26091 judge feedback pass (CLAUDE.md §18, §19,
§23): a resolved scheme parameter that is specific to some OTHER scheme (e.g.
a PMMY loan ceiling, retrieved because no SIH-specific document exists in
the knowledge base) must never be rendered as if it belonged to the
SIH-declared Term Loan / Micro Finance structure that the Financial
assessment section's figures are actually built on. The report already
carries the scheme name on `Applicability.scheme` — this only has to be
surfaced, not computed. Offline; the resolution is hand-built rather than
retrieved, to isolate rendering from corpus-loading concerns.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from tests.dpr_pipeline import minimal_turns, run_pipeline
from vyaparsarathi.dpr.artifacts import load_artifacts
from vyaparsarathi.dpr.sections import build_scheme_knowledge_section
from vyaparsarathi.models.finance import Unit
from vyaparsarathi.models.knowledge import ChunkLocator, Jurisdiction, JurisdictionLevel, SourceTier
from vyaparsarathi.models.parameters import (
    Applicability,
    FinanceKnowledgeEvidence,
    ParameterName,
    ParameterQuery,
    ParameterResolution,
    ResolutionStatus,
    SourcedParameter,
    ValueNormalization,
)

_RETRIEVED_AT = datetime(2026, 1, 15, tzinfo=UTC)
_AS_OF = date(2026, 1, 1)


def _pmmy_loan_ceiling() -> SourcedParameter:
    return SourcedParameter(
        parameter_id="doc-pmmy:loan_ceiling_inr:1",
        name=ParameterName.LOAN_CEILING_INR,
        value=2_000_000,
        unit=Unit.INR,
        value_token="20",
        normalization=ValueNormalization.LAKH_TO_INR,
        evidence_quote="The maximum loan ceiling under this scheme is Rs 20 lakh.",
        document_id="doc-pmmy",
        chunk_id="doc-pmmy#s1",
        locator=ChunkLocator(section="1"),
        tier=SourceTier.GOVT_PRIMARY,
        applicability=Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL),
            scheme="PMMY",
        ),
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )


def _evidence_with_pmmy_ceiling() -> FinanceKnowledgeEvidence:
    chosen = _pmmy_loan_ceiling()
    resolution = ParameterResolution(
        name=ParameterName.LOAN_CEILING_INR,
        status=ResolutionStatus.RESOLVED,
        chosen=chosen,
        source=f"knowledge:{chosen.document_id}",
        source_ref=f"{chosen.document_id}#{chosen.locator.as_ref()}",
        retrieved_at=_RETRIEVED_AT,
        confidence=0.8,
    )
    return FinanceKnowledgeEvidence(
        query=ParameterQuery(names=(ParameterName.LOAN_CEILING_INR,), as_of=_AS_OF),
        resolutions=[resolution],
        acquired_at=_RETRIEVED_AT,
    )


def test_scheme_specific_parameter_names_its_scheme(tmp_path: Path) -> None:
    session = run_pipeline(minimal_turns(), tmp_path=tmp_path)
    arts = replace(load_artifacts(session), knowledge=_evidence_with_pmmy_ceiling())
    section = build_scheme_knowledge_section(session, arts)

    line = next(
        line for line in section.resolved_parameters if line.name == "loan_ceiling_inr"
    )
    assert line.status == ResolutionStatus.RESOLVED.value
    # the reader must be able to see this figure is PMMY's, not the
    # SIH-declared Term Loan/Micro Finance structure the Financial
    # assessment section's own numbers are built on.
    assert "PMMY" in " ".join(line.notes)
