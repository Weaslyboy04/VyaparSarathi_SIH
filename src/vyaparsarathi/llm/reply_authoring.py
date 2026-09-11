"""Optional, opt-in LLM-authored reply phrasing over already-computed facts
(CLAUDE.md §3.1, §5.4, §25 Phase 6 Priority 5). Impure (calls a provider) —
this is why it lives here rather than in `conversation/render.py`, which
stays pure and remains the guaranteed fallback for every section always.

The loop per section is: ask the LLM to phrase ONLY the facts already cited
for that section (`conversation/bundle.py`'s `Fact.render` strings — never
the raw engine result, never another section's facts) → check the result
with `conversation/grounding.py::check_section` (no invented numeral, no
banned phrase) → swap it in only if accepted, otherwise keep the
deterministic template text unchanged. A provider error, timeout, or
malformed/empty response degrades the same way — silently, to the template,
never surfaced as a user-facing failure (CLAUDE.md §3.1: an LLM's absence,
or a rejected rewrite, is a fully supported, undegraded outcome for the
*facts* — only the *phrasing* falls back).

Gated entirely by `Settings.llm_reply_authoring_enabled` (default `False`,
CLAUDE.md §30 — never a prerequisite for safe extraction); `app/service.py`
only calls this when both that flag and a configured provider are present.
"""

from __future__ import annotations

from vyaparsarathi.config import Settings
from vyaparsarathi.conversation.bundle import EvidenceBundle
from vyaparsarathi.conversation.conversation_config import (
    DEFAULT_CONVERSATION_CONFIG,
    ConversationConfig,
)
from vyaparsarathi.conversation.grounding import check_section
from vyaparsarathi.conversation.render import Narrative
from vyaparsarathi.errors import LlmPayloadError, LlmUnavailableError
from vyaparsarathi.llm.llm_models import LlmMessage, LlmRequest, LlmRole
from vyaparsarathi.llm.prompts import EXPLANATION_INSTRUCTIONS, SYSTEM_PROMPT
from vyaparsarathi.llm.provider import LlmProvider
from vyaparsarathi.utils.logging import get_logger

logger = get_logger(__name__)

# Which bundle keys a section may cite — mirrors `conversation/render.py::
# _render_summary`'s own `bundle.get(...)` calls exactly (duplicated rather
# than imported so this impure module never needs render.py to expose
# anything beyond the `Narrative` it already returns; `test_reply_authoring
# .py` asserts this stays a subset of the section names render.py actually
# produces, catching drift if a section is added/renamed there).
_FIXED_SECTION_KEYS: dict[str, tuple[str, ...]] = {
    "market": ("opportunity.stance", "opportunity.proposed_score"),
    "market_confidence": ("opportunity.market_data_confidence",),
    "pivot": ("opportunity.recommended_pivot",),
    "finance": ("finance.status", "finance.average_annual_dscr"),
    "breaking_point": ("finance.breaking_point",),
    "scheme_structure": (
        "structure.scheme_name",
        "structure.required_promoter_margin_inr",
        "structure.indicated_loan_inr",
        "structure.margin_shortfall_inr",
    ),
    "recommendation": ("recommend.verdict", "recommend.reason"),
}
# Sections whose bundle keys are dynamic (one per missing driver / SWOT
# item) — resolved against the live bundle by prefix, never hardcoded.
_PREFIX_SECTION_KEYS: dict[str, str] = {
    "missing_drivers": "finance.missing_core_driver.",
    "swot": "swot.",
}
# Sections with no bundle facts to cite at all — never eligible for
# rewriting (there is nothing for `check_section` to ground them against).
_UNCITED_SECTIONS = frozenset({"fallback", "partial_notice"})


def _cited_keys_for_section(name: str, bundle: EvidenceBundle) -> tuple[str, ...]:
    if name in _FIXED_SECTION_KEYS:
        return _FIXED_SECTION_KEYS[name]
    prefix = _PREFIX_SECTION_KEYS.get(name)
    if prefix is not None:
        return tuple(f.key for f in bundle.facts if f.key.startswith(prefix))
    return ()


def _authoring_request(allowed_renders: tuple[str, ...], settings: Settings) -> LlmRequest:
    facts_block = "\n".join(f"- {r}" for r in allowed_renders)
    user_content = (
        f"{EXPLANATION_INSTRUCTIONS}\n\nFacts:\n{facts_block}\n\n"
        "Write the section now. Return only the prose — no heading, no markdown fencing."
    )
    return LlmRequest(
        messages=(
            LlmMessage(role=LlmRole.SYSTEM, content=SYSTEM_PROMPT),
            LlmMessage(role=LlmRole.USER, content=user_content),
        ),
        max_output_tokens=settings.llm_max_output_tokens,
        temperature=settings.llm_temperature,
        prompt_id="explanation",
    )


def author_reply_sections(
    narrative: Narrative,
    bundle: EvidenceBundle,
    provider: LlmProvider,
    settings: Settings,
    *,
    cfg: ConversationConfig = DEFAULT_CONVERSATION_CONFIG,
) -> Narrative:
    """Never raises. Returns a `Narrative` with each eligible section
    replaced by an LLM rewrite iff one was produced and it passed grounding
    — every other section (including every ASK_*/STAGE_FAILED narrative,
    which cite nothing) is returned byte-for-byte unchanged."""
    sections = dict(narrative.sections)
    generated_by = dict(narrative.generated_by)

    for name in narrative.sections:
        if name in _UNCITED_SECTIONS:
            continue
        cited_keys = _cited_keys_for_section(name, bundle)
        if not cited_keys:
            continue
        allowed_renders = tuple(f.render for f in bundle.facts if f.key in set(cited_keys))
        if not allowed_renders:
            continue

        request = _authoring_request(allowed_renders, settings)
        try:
            response = provider.complete(request)
        except (LlmUnavailableError, LlmPayloadError) as exc:
            logger.warning("reply authoring unavailable for section %r: %s", name, exc)
            continue

        candidate_text = response.text.strip()
        if not candidate_text:
            continue

        result = check_section(
            candidate_text, bundle, cited_keys, min_digit_run=cfg.grounding_min_digit_run
        )
        if result.accepted:
            sections[name] = candidate_text
            generated_by[name] = "llm"
        else:
            logger.warning("reply authoring rejected for section %r: %s", name, result.reason)

    return Narrative(sections=sections, generated_by=generated_by)


__all__ = ["author_reply_sections"]
