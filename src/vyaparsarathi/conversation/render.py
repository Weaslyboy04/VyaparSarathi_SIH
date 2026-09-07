"""Deterministic prose from a `NextAction` + `EvidenceBundle` (CLAUDE.md §5.4,
§25 Phase 6). PURE.

Every section here is template text built directly from a `Fact.render`
string — never free-form. This is the renderer `app/service.py` always has
available; when `llm_enabled=True` and grounding accepts the LLM's version
of a section, `llm/orchestrator.py` swaps that section's text in and marks
its `generated_by` "llm" — but the whole reply is always at least this,
never blank and never an exception (CLAUDE.md §3.1: absence of an LLM is a
supported mode, not a degraded one).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from vyaparsarathi.conversation.bundle import EvidenceBundle, build_bundle
from vyaparsarathi.conversation.clarify import question_for_missing_driver
from vyaparsarathi.conversation.planner import NextAction, NextActionKind
from vyaparsarathi.conversation.session_models import ConversationSession


class Narrative(BaseModel):
    """`sections` are keyed by a stable name (`"market"`, `"finance"`,
    `"recommendation"`, ...); `generated_by[section]` is `"template"` or
    `"llm"` — CLAUDE.md §5.4's "always distinguishable from the evidence it
    rests on", at the granularity a reader actually experiences."""

    model_config = ConfigDict(extra="forbid")

    sections: dict[str, str] = Field(default_factory=dict)
    generated_by: dict[str, str] = Field(default_factory=dict)


def _question_lines(action: NextAction) -> list[str]:
    lines = [action.message]
    for i, option in enumerate(action.options, start=1):
        lines.append(f"{i}. {option}")
    return lines


def render_reply(session: ConversationSession, action: NextAction) -> tuple[list[str], Narrative]:
    """`(message_lines, narrative)` for one turn's `NextAction`."""
    if action.kind in (
        NextActionKind.ASK_CONTRADICTION,
        NextActionKind.ASK_DISAMBIGUATION,
        NextActionKind.ASK_CLARIFICATION,
    ):
        return _question_lines(action), Narrative(
            sections={"question": action.message}, generated_by={"question": "template"}
        )
    if action.kind in (NextActionKind.DELIVER_FINAL, NextActionKind.DELIVER_PARTIAL):
        bundle = build_bundle(session)
        return _render_summary(bundle, partial=action.kind is NextActionKind.DELIVER_PARTIAL)
    if action.kind is NextActionKind.END:
        return ["This conversation has ended."], Narrative()
    return [], Narrative()  # pragma: no cover — RUN_STEP never reaches render_reply


def _render_summary(bundle: EvidenceBundle, *, partial: bool) -> tuple[list[str], Narrative]:
    lines: list[str] = []
    sections: dict[str, str] = {}
    generated_by: dict[str, str] = {}

    def add(name: str, text: str) -> None:
        lines.append(text)
        sections[name] = text
        generated_by[name] = "template"

    stance = bundle.get("opportunity.stance")
    if stance is not None:
        score = bundle.get("opportunity.proposed_score")
        text = f"Market stance: {stance.render}"
        if score is not None:
            text += f" (opportunity score {score.render})"
        add("market", text + ".")

    confidence = bundle.get("opportunity.market_data_confidence")
    if confidence is not None:
        add(
            "market_confidence",
            f"Market-data confidence: {confidence.render} — how well the local market is "
            "observed here, not a prediction of success.",
        )

    pivot = bundle.get("opportunity.recommended_pivot")
    if pivot is not None:
        add("pivot", f"A materially better local alternative was found: {pivot.render}.")

    fin_status = bundle.get("finance.status")
    if fin_status is not None:
        text = f"Financial status: {fin_status.render}."
        dscr = bundle.get("finance.average_annual_dscr")
        if dscr is not None:
            text += f" Average annual DSCR: {dscr.render}."
        add("finance", text)

    breaking = bundle.get("finance.breaking_point")
    if breaking is not None:
        add("breaking_point", f"Named breaking point: {breaking.render}")

    missing = [f for f in bundle.facts if f.key.startswith("finance.missing_core_driver.")]
    if missing:
        text_lines = ["To give a real financial verdict, I still need:"]
        text_lines.extend(f"- {question_for_missing_driver(m.render)}" for m in missing)
        add("missing_drivers", "\n".join(text_lines))

    verdict = bundle.get("recommend.verdict")
    if verdict is not None:
        reason = bundle.get("recommend.reason")
        text = f"Recommendation: {verdict.render}."
        if reason is not None:
            text += f" {reason.render}"
        add("recommendation", text)

    if not lines:
        add("fallback", "I don't have enough evidence yet to say anything about this business.")

    if partial:
        add(
            "partial_notice",
            "This is a partial picture — some analysis is still incomplete or blocked.",
        )

    return lines, Narrative(sections=sections, generated_by=generated_by)


__all__ = ["Narrative", "render_reply"]
