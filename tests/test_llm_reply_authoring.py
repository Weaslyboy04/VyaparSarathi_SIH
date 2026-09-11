"""`llm/reply_authoring.py` — optional, opt-in, grounded LLM reply phrasing
(CLAUDE.md §3.1, §5.4, §25 Phase 6 Priority 5). All fakes; no network.
"""

from __future__ import annotations

import pytest

from tests.test_conversation_render import _session_with_structure_and_swot
from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.bundle import build_bundle
from vyaparsarathi.conversation.planner import NextAction, NextActionKind
from vyaparsarathi.conversation.render import Narrative, render_reply
from vyaparsarathi.llm.fake import ScriptedLlmProvider
from vyaparsarathi.llm.llm_models import LlmResponse
from vyaparsarathi.llm.reply_authoring import author_reply_sections


def _bundle():
    return build_bundle(_session_with_structure_and_swot())


def _scheme_structure_only_narrative() -> Narrative:
    """A single-section `Narrative` built from the same fixture's real
    `render_reply` output, isolating just `scheme_structure` — so a test can
    queue exactly one scripted response without depending on how many OTHER
    sections `_render_summary` also happens to populate."""
    session = _session_with_structure_and_swot()
    _, full_narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_FINAL))
    return Narrative(
        sections={"scheme_structure": full_narrative.sections["scheme_structure"]},
        generated_by={"scheme_structure": "template"},
    )


def test_accepted_rewrite_swaps_in_and_marks_generated_by_llm(settings: Settings) -> None:
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    # A rewrite that only uses numerals already present in the bundle's
    # scheme_structure facts, in the SAME numeral form the render strings
    # use ("Rs 32300.00" — the decimal suffix is part of the numeral
    # grounding.py compares against, not just the digits before it).
    provider = ScriptedLlmProvider(
        {
            "explanation": [
                LlmResponse(
                    text="Under Test Declared Structure, the promoter puts in 32300.00 "
                    "and the indicated loan is 290700.00.",
                    prompt_id="explanation",
                )
            ]
        }
    )
    result = author_reply_sections(narrative, bundle, provider, settings)
    assert result.generated_by["scheme_structure"] == "llm"
    assert "32300" in result.sections["scheme_structure"]


def test_ungrounded_numeral_is_rejected_and_template_kept(settings: Settings) -> None:
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    original_text = narrative.sections["scheme_structure"]
    provider = ScriptedLlmProvider(
        {
            "explanation": [
                LlmResponse(
                    text="The promoter margin is 99999 rupees.",  # not in the bundle
                    prompt_id="explanation",
                )
            ]
        }
    )
    result = author_reply_sections(narrative, bundle, provider, settings)
    assert result.generated_by["scheme_structure"] == "template"
    assert result.sections["scheme_structure"] == original_text


def test_banned_phrase_is_rejected(settings: Settings) -> None:
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    original_text = narrative.sections["scheme_structure"]
    provider = ScriptedLlmProvider(
        {
            "explanation": [
                LlmResponse(
                    text="This structure is guaranteed to succeed.", prompt_id="explanation"
                )
            ]
        }
    )
    result = author_reply_sections(narrative, bundle, provider, settings)
    assert result.generated_by["scheme_structure"] == "template"
    assert result.sections["scheme_structure"] == original_text


def test_provider_unavailable_falls_back_to_template_silently(settings: Settings) -> None:
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    provider = ScriptedLlmProvider({})  # no responses queued -> LlmUnavailableError internally
    result = author_reply_sections(narrative, bundle, provider, settings)
    # Never raises; the section stays template.
    assert result.generated_by["scheme_structure"] == "template"


def test_empty_response_falls_back_to_template(settings: Settings) -> None:
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    provider = ScriptedLlmProvider(
        {"explanation": [LlmResponse(text="   ", prompt_id="explanation")]}
    )
    result = author_reply_sections(narrative, bundle, provider, settings)
    assert result.generated_by["scheme_structure"] == "template"


def test_uncited_sections_are_never_sent_to_the_provider(settings: Settings) -> None:
    """'fallback'/'partial_notice' cite no bundle facts — never eligible."""
    narrative = Narrative(
        sections={"fallback": "I don't have enough evidence yet."},
        generated_by={"fallback": "template"},
    )
    bundle = _bundle()
    provider = ScriptedLlmProvider({})  # would raise if ever called
    result = author_reply_sections(narrative, bundle, provider, settings)
    assert result.sections["fallback"] == "I don't have enough evidence yet."
    assert result.generated_by["fallback"] == "template"
    assert provider.calls == []


def test_request_for_one_section_never_contains_another_sections_facts(
    settings: Settings,
) -> None:
    """The scheme_structure request must not carry SWOT-only facts — the
    per-section citation isolation this module inherits from
    `conversation/grounding.py::check_section`, never the whole bundle."""
    narrative = _scheme_structure_only_narrative()
    bundle = _bundle()
    provider = ScriptedLlmProvider(
        {"explanation": [LlmResponse(text="ok 32300", prompt_id="explanation")]}
    )
    author_reply_sections(narrative, bundle, provider, settings)
    assert len(provider.calls) == 1
    prompt_text = provider.calls[0].messages[1].content
    assert "32300" in prompt_text or "290700" in prompt_text
    assert "clears financing" not in prompt_text  # a SWOT fact, not scheme_structure's


def test_multi_section_narrative_authors_each_section_independently(
    settings: Settings,
) -> None:
    """The realistic case: a narrative with several real sections (this
    fixture yields finance, scheme_structure and swot), each getting its own
    provider call and its own accept/reject outcome — never a single
    combined request across sections."""
    session = _session_with_structure_and_swot()
    _, narrative = render_reply(session, NextAction(kind=NextActionKind.DELIVER_FINAL))
    bundle = build_bundle(session)
    eligible = ("finance", "scheme_structure", "swot")
    assert set(eligible) <= set(narrative.sections)  # sanity-check the fixture's own shape

    provider = ScriptedLlmProvider(
        {"explanation": [LlmResponse(text="ok", prompt_id="explanation") for _ in eligible]}
    )
    result = author_reply_sections(narrative, bundle, provider, settings)
    # "ok" contains no digits and no banned phrase, so every eligible
    # section with at least one grounded fact accepts it.
    for name in eligible:
        assert result.generated_by[name] == "llm", name
    assert len(provider.calls) == len(eligible)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
