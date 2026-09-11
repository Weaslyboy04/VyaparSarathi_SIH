"""`scripts/build_knowledge_corpus.py` + `scripts/build_parameter_registry.py`
— the offline ETL, tested the way `tests/test_finance_demos.py` imports
`scripts/phase4_demo.py` (CLAUDE.md §23, §33). Pure & offline; `main()` here
only ever touches `tmp_path`, never the real `data/knowledge/`.

The dual-LLM gate (`extract_and_verify`) is tested with a `_ScriptedProvider`
test double implementing `LlmProvider.complete()` — never `GeminiLlmProvider`,
never a real network call, matching CLAUDE.md §28's rule that external APIs
are mocked in unit tests. Both roles now run the IDENTICAL extraction task
(genuinely blind — see `build_parameter_registry.py`'s module docstring), so
every test scripts TWO independent candidate responses and asserts on how
`_canonical_agree` treats them.
"""

from __future__ import annotations

import copy
import csv
import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from scripts import build_knowledge_corpus, build_parameter_registry

from vyaparsarathi.errors import KnowledgeCorpusError
from vyaparsarathi.llm.llm_models import LlmRequest, LlmResponse
from vyaparsarathi.models.knowledge import (
    ChunkLocator,
    DocumentChunk,
    DocumentRecord,
    Jurisdiction,
    JurisdictionLevel,
    SourceTier,
)
from vyaparsarathi.sources.knowledge.loader import PARAM_CSV_FIELDNAMES, FileCorpusStore

build_chunks = build_knowledge_corpus.build_chunks
build_corpus_main = build_knowledge_corpus.main
extract_and_verify = build_parameter_registry.extract_and_verify
extraction_request = build_parameter_registry._extraction_request
write_parameters_csv = build_parameter_registry.write_parameters_csv
build_registry_main = build_parameter_registry.main
_cmd_verify = build_parameter_registry._cmd_verify
_parse_args = build_parameter_registry._parse_args

# ======================================================================
# build_chunks — pure, section-aware, deterministic
# ======================================================================


def test_build_chunks_splits_on_headings() -> None:
    text = (
        "# 1 Intro\n\nFirst section text.\n\n## 1.1 Detail\n\nSecond section text, more detailed.\n"
    )
    chunks = build_chunks("doc-a", text, max_words=100)
    assert [c.locator.section for c in chunks] == ["1", "1.1"]
    assert chunks[0].heading_path == ("1 Intro",)
    assert chunks[1].heading_path == ("1 Intro", "1.1 Detail")


def test_build_chunks_captures_page_and_tag_markers() -> None:
    text = (
        "# 1 Intro\n\n[[page:12]]\n[[topics: scheme_financing]]\n"
        "[[schemes: test-scheme]]\n\nSection text with markers applied.\n"
    )
    chunks = build_chunks("doc-a", text, max_words=100)
    assert len(chunks) == 1
    assert chunks[0].locator.page_from == 12
    assert chunks[0].topics[0].value == "scheme_financing"
    assert chunks[0].schemes == ("test-scheme",)


def test_build_chunks_splits_long_sections_into_paragraph_chunks() -> None:
    para = "word " * 20  # 20 words per paragraph
    text = "# 1 Intro\n\n" + f"{para}\n\n{para}\n\n{para}\n"
    # three 20-word paragraphs, cap 50: (1) 20 (2) 20+20=40<=50 combines (3)
    # 40+20=60>50 splits -> paragraphs 1+2 in one chunk, paragraph 3 alone.
    chunks = build_chunks("doc-a", text, max_words=50)
    assert len(chunks) == 2
    assert all(c.locator.section == "1" for c in chunks)
    assert {c.locator.paragraph_index for c in chunks} == {0, 1}
    assert chunks[0].text.count("word") == 40
    assert chunks[1].text.count("word") == 20


def test_build_chunks_never_splits_inside_a_single_paragraph() -> None:
    # Two oversized paragraphs, each alone bigger than max_words, must still
    # each become their own whole chunk rather than being cut mid-paragraph.
    para = "word " * 60
    text = "# 1 Intro\n\n" + f"{para}\n\n{para}\n"
    chunks = build_chunks("doc-a", text, max_words=50)
    assert len(chunks) == 2
    assert all(c.text.count("word") == 60 for c in chunks)


def test_build_chunks_is_deterministic() -> None:
    text = "# 1 Intro\n\nSome stable text.\n\n## 1.1 More\n\nMore stable text.\n"
    a = build_chunks("doc-a", text, max_words=100)
    b = build_chunks("doc-a", text, max_words=100)
    assert [c.model_dump(mode="json") for c in a] == [c.model_dump(mode="json") for c in b]


def test_build_chunks_disambiguates_colliding_locator_refs() -> None:
    # Two headings with the same leading number produce the same base ref;
    # the second must not silently overwrite the first's chunk_id.
    text = "# 1 First\n\nFirst text.\n\n# 1 Duplicate Heading Number\n\nSecond text.\n"
    chunks = build_chunks("doc-a", text, max_words=100)
    ids = [c.chunk_id for c in chunks]
    assert len(ids) == len(set(ids))


def test_build_chunks_on_empty_text_produces_nothing() -> None:
    assert build_chunks("doc-a", "", max_words=100) == []
    assert build_chunks("doc-a", "# 1 Empty Heading\n\n", max_words=100) == []


def test_build_chunks_atomic_multi_rule_split_stays_traceable_to_one_document() -> None:
    """The real PMEGP §3.2 shape: six sub-headings sharing the same section
    number "3.2", one atomic fact each. Every resulting chunk must keep the
    same document_id, a unique chunk_id, the shared section number, and a
    heading_path that individually distinguishes it — full provenance for
    an atomic split, not just "no collisions" (already covered above)."""
    text = (
        "# 3 Quantum and Nature of Financial Assistance\n\n"
        "## 3.2 Beneficiary contribution — General Category\n\n"
        "General Category: Beneficiary's contribution is 10% of project cost.\n\n"
        "## 3.2 Beneficiary contribution — Special Category\n\n"
        "Special Category: Beneficiary's contribution is 5% of project cost.\n\n"
        "## 3.2 Subsidy — General Category, Urban\n\n"
        "General Category: Rate of Subsidy is 15% of project cost in Urban areas.\n\n"
        "## 3.2 Subsidy — General Category, Rural\n\n"
        "General Category: Rate of Subsidy is 25% of project cost in Rural areas.\n\n"
        "## 3.2 Subsidy — Special Category, Urban\n\n"
        "Special Category: Rate of Subsidy is 25% of project cost in Urban areas.\n\n"
        "## 3.2 Subsidy — Special Category, Rural\n\n"
        "Special Category: Rate of Subsidy is 35% of project cost in Rural areas.\n"
    )
    chunks = build_chunks("pmegp-guidelines-2023", text, max_words=350)
    assert len(chunks) == 6
    assert all(c.document_id == "pmegp-guidelines-2023" for c in chunks)
    assert all(c.locator.section == "3.2" for c in chunks)
    chunk_ids = [c.chunk_id for c in chunks]
    assert len(chunk_ids) == len(set(chunk_ids))  # every id unique
    assert all(cid.startswith("pmegp-guidelines-2023#s3.2") for cid in chunk_ids)
    # each chunk's own distinguishing description is retained, and each
    # chunk states exactly one number — no chunk repeats another's fact.
    second_heading_parts = [c.heading_path[1] for c in chunks]
    assert len(set(second_heading_parts)) == 6
    number_bearing_facts = [c.text for c in chunks]
    assert len(set(number_bearing_facts)) == 6  # six distinct atomic sentences


# ======================================================================
# _extraction_request — genuinely blind, identical for both roles
# ======================================================================


def _document(**kw: object) -> DocumentRecord:
    base: dict[str, object] = {
        "document_id": "doc-a",
        "title": "Synthetic ETL test document",
        "publisher": "Fictional test publisher",
        "tier": SourceTier.GOVT_PRIMARY,
        "jurisdiction": Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"),
        "retrieved_at": datetime(2026, 1, 15, tzinfo=UTC),
        "content_sha256": "a" * 64,
    }
    base.update(kw)
    return DocumentRecord(**base)  # type: ignore[arg-type]


def _chunk(text: str, **kw: object) -> DocumentChunk:
    base: dict[str, object] = {
        "chunk_id": "doc-a#s1",
        "document_id": "doc-a",
        "locator": ChunkLocator(section="1"),
        "text": text,
        "token_count": len(text.split()),
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    base.update(kw)
    return DocumentChunk(**base)  # type: ignore[arg-type]


_CHUNK_TEXT = "The rate of interest chargeable shall be 9.5% per annum."


def test_extraction_request_is_identical_regardless_of_which_role_calls_it() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    a = extraction_request(chunk, document)
    b = extraction_request(chunk, document)
    assert a == b  # frozen pydantic models compare by value — byte-identical


def test_extraction_request_never_embeds_a_proposed_answer() -> None:
    # Genuine blindness, structurally: the shared instructions may use the
    # word "candidate" generically (describing what to extract), but the
    # request must never embed an actual PROPOSED ANSWER for the model to
    # react to — the old design's exact leak ("Candidate proposed by the
    # other model:\n{json}") and the old checklist/restated concepts are
    # both gone entirely.
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    request = extraction_request(chunk, document)
    full_text = " ".join(m.content for m in request.messages).lower()
    assert "candidate proposed" not in full_text
    assert "restated" not in full_text
    assert "checklist" not in full_text
    assert len(request.messages) == 2  # system + user only, nothing else injected


# ======================================================================
# extract_and_verify — the dual-LLM gate, via two scripted fake providers
# ======================================================================


class _ScriptedProvider:
    """A minimal `LlmProvider` test double: returns one fixed response text
    for every call (each test scans exactly one chunk, so no per-call
    routing is needed), or raises a fixed exception."""

    def __init__(
        self, response_text: str | None = None, *, raises: Exception | None = None
    ) -> None:
        self._response_text = response_text
        self._raises = raises

    def complete(self, request: LlmRequest) -> LlmResponse:
        if self._raises is not None:
            raise self._raises
        assert self._response_text is not None
        return LlmResponse(text=self._response_text, prompt_id=request.prompt_id)


_VALID_CANDIDATE_JSON: dict[str, object] = {
    "found": True,
    "name": "interest_rate_pct",
    "value_token": "9.5%",
    "unit": "percent_per_annum",
    "normalization": "percent_as_annual_rate",
    "evidence_quote": "The rate of interest chargeable shall be 9.5% per annum.",
    "applicability": {
        "jurisdiction": {"level": "state", "state": "Bihar", "district": None},
        "scheme": None,
        "categories": [],
        "activity_kind": "",
        "min_loan_inr": None,
        "min_loan_inr_exclusive": False,
        "max_loan_inr": None,
        "max_loan_inr_exclusive": False,
        "effective_from": None,
        "effective_to": None,
        "conditions": [],
    },
    "reference_date": None,
    "is_benchmark": False,
    "notes": "",
}


def test_extract_and_verify_publishes_on_full_agreement() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert stats.published == 1
    assert len(rows) == 1
    assert rows[0].name.value == "interest_rate_pct"
    assert rows[0].tier is document.tier  # copied from the document, never proposed
    assert rows[0].extractor_model == "test-extractor"
    assert rows[0].verifier_model == "test-verifier"
    assert rows[0].verified_on == date(2026, 1, 15)


def test_extract_and_verify_publishes_when_normalized_values_agree_despite_different_tokens() -> (
    None
):
    # Two independent reads may verbatim-quote different (both valid) spans
    # of the same sentence; what must agree is the NORMALIZED value, not the
    # raw value_token string.
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    candidate_b = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate_b["value_token"] = "9.50%"  # same number, different verbatim string
    candidate_b["evidence_quote"] = "chargeable shall be 9.50% per annum"
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(candidate_b))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert stats.published == 1
    assert rows[0].value_token == "9.5%"  # the extractor's own literal string is stored


def test_extract_and_verify_agrees_on_scheme_despite_incidental_whitespace() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    candidate_a = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate_a["applicability"]["scheme"] = "PMEGP"
    candidate_b = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate_b["applicability"]["scheme"] = "  PMEGP  "  # whitespace-only difference
    extractor = _ScriptedProvider(json.dumps(candidate_a))
    verifier = _ScriptedProvider(json.dumps(candidate_b))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert stats.published == 1
    assert rows[0].applicability.scheme == "PMEGP"


def test_extract_and_verify_drops_when_scheme_genuinely_differs() -> None:
    # Not a whitespace difference — a real disagreement about which scheme
    # this figure belongs to must never be papered over.
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    candidate_a = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate_a["applicability"]["scheme"] = "PMEGP"
    candidate_b = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate_b["applicability"]["scheme"] = "MUDRA"
    extractor = _ScriptedProvider(json.dumps(candidate_a))
    verifier = _ScriptedProvider(json.dumps(candidate_b))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_disagreement == 1


def test_extract_and_verify_drops_on_extractor_verifier_disagreement() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    disagreeing = copy.deepcopy(_VALID_CANDIDATE_JSON)
    disagreeing["value_token"] = "9.9%"
    disagreeing["evidence_quote"] = "chargeable shall be 9.9% per annum"
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(disagreeing))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_disagreement == 1


def test_extract_and_verify_drops_when_llm_response_is_unparseable() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    extractor = _ScriptedProvider("this is not json at all")
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_extractor_unparseable == 1


def test_extract_and_verify_copies_tier_from_document_never_from_candidate() -> None:
    document = _document(tier=SourceTier.PUBLIC_SECTOR_INSTITUTION)
    chunk = _chunk(_CHUNK_TEXT)
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))

    rows, _stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows[0].tier is SourceTier.PUBLIC_SECTOR_INSTITUTION


def test_extract_and_verify_drops_when_applicability_jurisdiction_exceeds_document_scope() -> None:
    document = _document(jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar"))
    chunk = _chunk(_CHUNK_TEXT)
    karnataka_candidate = copy.deepcopy(_VALID_CANDIDATE_JSON)
    karnataka_candidate["applicability"]["jurisdiction"] = {
        "level": "state",
        "state": "Karnataka",
        "district": None,
    }
    extractor = _ScriptedProvider(json.dumps(karnataka_candidate))
    verifier = _ScriptedProvider(json.dumps(karnataka_candidate))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_mechanical_or_scope == 1


def test_extract_and_verify_skips_chunk_with_no_matching_document() -> None:
    chunk = _chunk(_CHUNK_TEXT, document_id="doc-missing", chunk_id="doc-missing#s1")
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))

    rows, stats = extract_and_verify(
        [chunk],
        {},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_no_document == 1


def test_extract_and_verify_paces_between_chunks_but_not_before_the_first() -> None:
    document = _document()
    chunks = [
        _chunk(_CHUNK_TEXT, chunk_id="doc-a#s1"),
        _chunk(_CHUNK_TEXT, chunk_id="doc-a#s2"),
        _chunk(_CHUNK_TEXT, chunk_id="doc-a#s3"),
    ]
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    calls: list[float] = []

    extract_and_verify(
        chunks,
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
        pacing_seconds=2.5,
        sleep=calls.append,
    )
    assert calls == [2.5, 2.5]  # one fewer than the chunk count — none before the first


def test_extract_and_verify_default_pacing_never_sleeps() -> None:
    document = _document()
    chunks = [_chunk(_CHUNK_TEXT, chunk_id="doc-a#s1"), _chunk(_CHUNK_TEXT, chunk_id="doc-a#s2")]
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    calls: list[float] = []

    extract_and_verify(
        chunks,
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
        sleep=calls.append,
    )
    assert calls == []


def test_extract_and_verify_counts_both_no_candidate_as_agreement_not_disagreement() -> None:
    # Both independent readers correctly finding nothing is a normal,
    # expected outcome (filler/procedural text) — never a disagreement.
    document = _document()
    chunk = _chunk("Nothing relevant is mentioned in this section at all.")
    extractor = _ScriptedProvider(json.dumps({"found": False}))
    verifier = _ScriptedProvider(json.dumps({"found": False}))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_both_no_candidate == 1
    assert stats.dropped_disagreement == 0


def test_extract_and_verify_treats_one_sided_candidate_as_disagreement() -> None:
    document = _document()
    chunk = _chunk(_CHUNK_TEXT)
    extractor = _ScriptedProvider(json.dumps(_VALID_CANDIDATE_JSON))
    verifier = _ScriptedProvider(json.dumps({"found": False}))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_disagreement == 1
    assert stats.dropped_both_no_candidate == 0


# ======================================================================
# loan-band inclusive/exclusive agreement — the real MUDRA Kishor bug
# ======================================================================

_KISHOR_TEXT = "Kishor: covering loans above Rs. 50,000 and up to Rs. 5 lakhs."


def _kishor_candidate_json(
    *, min_loan_inr: str | None, min_exclusive: bool, max_loan_inr: str | None, max_exclusive: bool
) -> dict[str, object]:
    candidate = copy.deepcopy(_VALID_CANDIDATE_JSON)
    candidate["name"] = "loan_ceiling_inr"
    candidate["value_token"] = "5 lakhs"
    candidate["unit"] = "inr"
    candidate["normalization"] = "lakh_to_inr"
    candidate["evidence_quote"] = _KISHOR_TEXT
    candidate["applicability"]["jurisdiction"] = {
        "level": "national",
        "state": None,
        "district": None,
    }
    candidate["applicability"]["scheme"] = "mudra-kishor"
    candidate["applicability"]["min_loan_inr"] = min_loan_inr
    candidate["applicability"]["min_loan_inr_exclusive"] = min_exclusive
    candidate["applicability"]["max_loan_inr"] = max_loan_inr
    candidate["applicability"]["max_loan_inr_exclusive"] = max_exclusive
    return candidate


def test_extract_and_verify_agrees_on_loan_band_bounds_and_their_exclusivity() -> None:
    document = _document(jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL))
    chunk = _chunk(_KISHOR_TEXT)
    both = _kishor_candidate_json(
        min_loan_inr="50000", min_exclusive=True, max_loan_inr="500000", max_exclusive=False
    )
    extractor = _ScriptedProvider(json.dumps(both))
    verifier = _ScriptedProvider(json.dumps(both))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert stats.published == 1
    assert rows[0].applicability.min_loan_inr_exclusive is True
    assert rows[0].applicability.max_loan_inr_exclusive is False


def test_extract_and_verify_drops_when_loan_band_exclusivity_reading_disagrees() -> None:
    # This is the EXACT real disagreement a live diagnostic run surfaced:
    # one independent read left a bound unset/differently-scoped than the
    # other, despite both agreeing on the underlying number. Real, not
    # spurious — must be dropped, never silently accepted either way.
    document = _document(jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL))
    chunk = _chunk(_KISHOR_TEXT)
    exclusive_reading = _kishor_candidate_json(
        min_loan_inr="50000", min_exclusive=True, max_loan_inr="500000", max_exclusive=False
    )
    inclusive_reading = _kishor_candidate_json(
        min_loan_inr="50000", min_exclusive=False, max_loan_inr="500000", max_exclusive=False
    )
    extractor = _ScriptedProvider(json.dumps(exclusive_reading))
    verifier = _ScriptedProvider(json.dumps(inclusive_reading))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_disagreement == 1


def test_extract_and_verify_drops_when_one_side_omits_the_loan_band_entirely() -> None:
    # The literal failure mode observed live: one model derived min/max, the
    # other left them null.
    document = _document(jurisdiction=Jurisdiction(level=JurisdictionLevel.NATIONAL))
    chunk = _chunk(_KISHOR_TEXT)
    with_band = _kishor_candidate_json(
        min_loan_inr="50000", min_exclusive=True, max_loan_inr="500000", max_exclusive=False
    )
    without_band = _kishor_candidate_json(
        min_loan_inr=None, min_exclusive=False, max_loan_inr=None, max_exclusive=False
    )
    extractor = _ScriptedProvider(json.dumps(with_band))
    verifier = _ScriptedProvider(json.dumps(without_band))

    rows, stats = extract_and_verify(
        [chunk],
        {"doc-a": document},
        extractor,
        verifier,
        extractor_model="test-extractor",
        verifier_model="test-verifier",
        verified_on=date(2026, 1, 15),
    )
    assert rows == []
    assert stats.dropped_disagreement == 1


# ======================================================================
# write_parameters_csv — zero-row preservation vs. normal overwrite
# ======================================================================


def _sourced_param(**kw: object) -> object:
    from decimal import Decimal

    from vyaparsarathi.models.finance import Unit
    from vyaparsarathi.models.parameters import (
        Applicability,
        ParameterName,
        SourcedParameter,
        ValueNormalization,
    )

    base: dict[str, object] = {
        "parameter_id": "doc-a:interest_rate_pct:1",
        "name": ParameterName.INTEREST_RATE_PCT,
        "value": Decimal("9.5"),
        "unit": Unit.PERCENT_PER_ANNUM,
        "value_token": "9.5%",
        "normalization": ValueNormalization.PERCENT_AS_ANNUAL_RATE,
        "evidence_quote": _CHUNK_TEXT,
        "document_id": "doc-a",
        "chunk_id": "doc-a#s1",
        "locator": ChunkLocator(section="1"),
        "tier": SourceTier.GOVT_PRIMARY,
        "applicability": Applicability(
            jurisdiction=Jurisdiction(level=JurisdictionLevel.STATE, state="Bihar")
        ),
        "extractor_model": "test-extractor",
        "verifier_model": "test-verifier",
        "verified_on": date(2026, 1, 15),
    }
    base.update(kw)
    return SourcedParameter(**base)  # type: ignore[arg-type]


def test_write_parameters_csv_writes_fresh_file_even_with_zero_rows(tmp_path: Path) -> None:
    out = tmp_path / "parameters.csv"
    wrote = write_parameters_csv([], out)
    assert wrote is True
    assert out.exists()
    with out.open(encoding="utf-8", newline="") as f:
        assert list(csv.DictReader(f)) == []


def test_write_parameters_csv_overwrites_normally_on_nonzero_rows(tmp_path: Path) -> None:
    out = tmp_path / "parameters.csv"
    write_parameters_csv([_sourced_param()], out)  # seed an existing file
    from decimal import Decimal as _Decimal

    new_row = _sourced_param(
        parameter_id="doc-a:interest_rate_pct:2",
        value=_Decimal("11"),
        value_token="11%",
        evidence_quote="The rate of interest chargeable shall be 11% per annum.",
    )
    wrote = write_parameters_csv([new_row], out)
    assert wrote is True
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 1
    assert rows[0]["parameter_id"] == "doc-a:interest_rate_pct:2"


def test_write_parameters_csv_preserves_existing_file_when_run_publishes_zero_rows(
    tmp_path: Path,
) -> None:
    out = tmp_path / "parameters.csv"
    write_parameters_csv([_sourced_param()], out)  # a good, prior run
    before = out.read_text(encoding="utf-8")

    wrote = write_parameters_csv([], out)  # a total-failure run

    assert wrote is False
    after = out.read_text(encoding="utf-8")
    assert after == before  # byte-identical — nothing was touched
    assert not out.with_name(out.name + ".tmp").exists()  # temp file cleaned up


def test_write_parameters_csv_zero_rows_over_header_only_file_still_overwrites(
    tmp_path: Path,
) -> None:
    # A header-only file (no data rows) holds nothing worth preserving —
    # this is NOT the data-loss scenario `write_parameters_csv` guards
    # against, so it's fine to rewrite it (idempotent, still zero rows).
    out = tmp_path / "parameters.csv"
    write_parameters_csv([], out)  # header-only
    wrote = write_parameters_csv([], out)
    assert wrote is True


# ======================================================================
# build_knowledge_corpus.py main() — end to end on tmp_path
# ======================================================================


def _write_raw_document(raw_dir: Path, document_id: str, *, text: str) -> None:
    doc_dir = raw_dir / document_id
    doc_dir.mkdir(parents=True)
    (doc_dir / "document.json").write_text(
        json.dumps(
            {
                "title": "Synthetic ETL test document",
                "publisher": "Fictional test publisher",
                "tier": "govt_primary",
                "jurisdiction": {"level": "national"},
                "published_on": "2025-01-01",
                "retrieved_at": "2026-01-15T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    (doc_dir / "text.txt").write_text(text, encoding="utf-8")


def test_build_corpus_main_writes_documents_and_chunks(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    out_dir = tmp_path / "out"
    _write_raw_document(
        raw_dir, "doc-a", text="# 1 Intro\n\nThe rate of interest chargeable shall be 9.5%.\n"
    )
    rc = build_corpus_main(
        [
            "--raw-dir",
            str(raw_dir),
            "--out-dir",
            str(out_dir),
            "--built-at",
            "2026-01-15T00:00:00+00:00",
        ]
    )
    assert rc == 0
    store = FileCorpusStore(out_dir)
    report = store.report()
    assert report.documents_loaded == 1
    assert report.chunks_loaded == 1


def test_build_corpus_main_reports_failure_on_empty_raw_dir(tmp_path: Path) -> None:
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    rc = build_corpus_main(
        [
            "--raw-dir",
            str(raw_dir),
            "--out-dir",
            str(tmp_path / "out"),
            "--built-at",
            "2026-01-15T00:00:00+00:00",
        ]
    )
    assert rc == 1


# ======================================================================
# build_parameter_registry.py main() — verify catches tampering
# ======================================================================


def _built_corpus(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw"
    out_dir = tmp_path / "out"
    _write_raw_document(
        raw_dir,
        "doc-a",
        text="# 1 Intro\n\n[[schemes: test-scheme]]\n\n"
        "The rate of interest chargeable shall be 9.5% per annum.\n",
    )
    assert (
        build_corpus_main(
            [
                "--raw-dir",
                str(raw_dir),
                "--out-dir",
                str(out_dir),
                "--built-at",
                "2026-01-15T00:00:00+00:00",
            ]
        )
        == 0
    )
    return out_dir


def _write_signed_row(out_dir: Path, *, value_token: str, value: str) -> None:
    row = dict.fromkeys(PARAM_CSV_FIELDNAMES, "")
    row.update(
        {
            "parameter_id": "doc-a:interest_rate_pct:1",
            "name": "interest_rate_pct",
            "value": value,
            "unit": "percent_per_annum",
            "value_token": value_token,
            "normalization": "percent_as_annual_rate",
            "evidence_quote": "The rate of interest chargeable shall be 9.5% per annum.",
            "document_id": "doc-a",
            "chunk_id": "doc-a#s1",
            "locator_section": "1",
            "tier": "govt_primary",
            "appl_jurisdiction_level": "national",
            "appl_scheme": "test-scheme",
            "is_benchmark": "false",
            "extractor_model": "test-extractor",
            "verifier_model": "test-verifier",
            "verified_on": "2026-01-15",
        }
    )
    with (out_dir / "parameters.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(PARAM_CSV_FIELDNAMES))
        writer.writeheader()
        writer.writerow(row)


def test_verify_passes_on_a_correctly_signed_row(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="9.5")
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 0


def test_verify_fails_on_a_tampered_value(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    # value_token/evidence_quote say 9.5%, but value claims 99.9 — tampered.
    _write_signed_row(out_dir, value_token="9.5%", value="99.9")
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 1


def test_verify_fails_on_an_unknown_chunk_reference(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="9.5")
    content = (out_dir / "parameters.csv").read_text(encoding="utf-8")
    (out_dir / "parameters.csv").write_text(
        content.replace("doc-a#s1", "doc-a#s99-nonexistent"), encoding="utf-8"
    )
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 1


def test_verify_reports_clean_on_a_freshly_built_empty_corpus(tmp_path: Path) -> None:
    out_dir = _built_corpus(tmp_path)  # no parameters.csv written at all
    rc = build_registry_main(["verify", "--corpus-dir", str(out_dir)])
    assert rc == 0


def test_cmd_verify_itself_raises_on_tampering(tmp_path: Path) -> None:
    # main()'s try/except is what turns this into exit code 1 for a CLI
    # caller; this confirms the underlying mechanism a future refactor could
    # otherwise silently break is really KnowledgeCorpusError, not a printed
    # warning that nothing acts on.
    out_dir = _built_corpus(tmp_path)
    _write_signed_row(out_dir, value_token="9.5%", value="99.9")
    args = _parse_args(["verify", "--corpus-dir", str(out_dir)])
    with pytest.raises(KnowledgeCorpusError):
        _cmd_verify(args)


def test_extract_subcommand_requires_verified_on() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["extract", "--corpus-dir", "x", "--out", "y.csv"])


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
