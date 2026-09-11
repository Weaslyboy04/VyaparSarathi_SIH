"""Render a `DprDocument` to a polished, printable PDF (CLAUDE.md §25 Phase 8).

Deterministic: the same `DprDocument` produces the same bytes (reportlab's
``invariant`` mode fixes the PDF's own timestamp/ids; the visible date comes
from ``doc.generated_at``, which the assembler was handed by the caller). No
clock is read here, no network, no LLM — the text is a straight transcription
of the structured model.

Layout: a cover page, then one section per page-group with a repeated header
(report id) and footer (page X of Y + a one-line disclaimer). Long tables
(stress scenarios, slot history, citations) use `LongTable`, which splits
across pages and repeats its header row, so a report stays readable whether a
section is one line or three pages.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from functools import partial
from io import BytesIO
from typing import TypeVar

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.charts.legends import Legend
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas as _canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    HRFlowable,
    KeepTogether,
    LongTable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from vyaparsarathi.dpr.fonts import FONT_FAMILY, register_fonts
from vyaparsarathi.dpr.provenance import ProvenancedValue, ValueOrigin
from vyaparsarathi.dpr.report_models import (
    AnnexuresSection,
    AssumptionsSection,
    CoverPage,
    DistributionChannelSection,
    DprDocument,
    EntrepreneurProfileSection,
    ExecutiveSummary,
    FinancialAssessmentSection,
    MarketAssessmentSection,
    MarketPriceSection,
    OpportunitySection,
    ProjectPlanSection,
    ReportSection,
    RisksSwotSection,
    SchemeKnowledgeSection,
    SectionStatus,
)
from vyaparsarathi.utils.time import to_ist

_PAGE = A4
_MARGIN = 18 * mm

# --- palette ---------------------------------------------------------
# A single accent (a grounded teal — trustworthy, not corporate-blue,
# not agri-green cliché) carries the whole document: headings, rules,
# table header tints, and the cover. Everything else stays neutral ink /
# slate so the accent reads as deliberate, not decorative.
_INK = colors.HexColor("#20262E")
_MUTED = colors.HexColor("#5C6B78")
_RULE = colors.HexColor("#D9DFE6")
_ACCENT = colors.HexColor("#0E6D5A")
_ACCENT_DARK = colors.HexColor("#0A4E40")
_ACCENT_TINT = colors.HexColor("#E7F2EF")
_GAP_BG = colors.HexColor("#FBF0E4")
_GAP_BORDER = colors.HexColor("#D9A55C")
_GAP_INK = colors.HexColor("#7A4B1F")
_SUMMARY_BG = _ACCENT_TINT
_SUMMARY_BORDER = colors.HexColor("#8FC1B3")
_HEAD_BG = _ACCENT_TINT

_ORIGIN_TAG = {
    ValueOrigin.USER_PROVIDED: "user-stated",
    ValueOrigin.SOURCED: "sourced",
    ValueOrigin.CALCULATED: "calculated",
    ValueOrigin.ASSUMED: "assumed",
    ValueOrigin.DECLARED_CONFIG: "declared config",
    ValueOrigin.NOT_AVAILABLE: "not available",
}

# Closed lookup tables translating internal engine tokens into plain
# reader-facing phrases -- never a generic string-mangler, so a token this
# pass didn't anticipate falls back to a readable, if blunter, rendering of
# the token itself rather than raising or silently vanishing.
_RUNG_PLAIN = {
    "missing_core_driver": "a required financial input is missing",
    "funding_gap": "the loan and margin do not fully cover the project cost",
    "not_serviceable": "the plan cannot reliably cover its loan repayments",
    "cash_stress": "cash runs tight at some point in this plan",
    "stress_sensitive": "the plan holds up, but only just, under a bad month",
    "clears_all": "the plan clears every check comfortably",
}

_PARAM_STATUS_PLAIN = {
    "resolved": "confirmed",
    "no_evidence": "not confirmed",
    "conflicting": "conflicting sources",
    "stale_only": "outdated source only",
    "conditions_unresolved": "conditions not yet checked",
    "not_queried": "not looked up",
}


def _plain_rung(token: str) -> str:
    return _RUNG_PLAIN.get(token, token.replace("_", " "))


def _plain_param_status(token: str) -> str:
    return _PARAM_STATUS_PLAIN.get(token, token.replace("_", " "))


def _styles() -> dict[str, ParagraphStyle]:
    register_fonts()
    base = ParagraphStyle(
        "body",
        fontName=FONT_FAMILY,
        fontSize=9.5,
        leading=13.5,
        textColor=_INK,
        alignment=TA_LEFT,
        spaceAfter=4,
    )
    return {
        "body": base,
        "small": ParagraphStyle("small", parent=base, fontSize=7.8, textColor=_MUTED, leading=10.5),
        "h1": ParagraphStyle(
            "h1",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=13.5,
            leading=16,
            spaceBefore=16,
            spaceAfter=2,
            textColor=_ACCENT_DARK,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=10,
            leading=12.5,
            spaceBefore=9,
            spaceAfter=3,
            textColor=_INK,
        ),
        "gap": ParagraphStyle(
            "gap",
            parent=base,
            fontSize=9,
            textColor=_GAP_INK,
            leftIndent=4,
            rightIndent=4,
            spaceBefore=3,
            spaceAfter=3,
        ),
        "summary": ParagraphStyle(
            "summary",
            parent=base,
            fontSize=10,
            leading=14,
            textColor=_ACCENT_DARK,
            leftIndent=4,
            rightIndent=4,
            spaceBefore=2,
            spaceAfter=2,
        ),
        "cover_kicker": ParagraphStyle(
            "cover_kicker",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=9,
            leading=11,
            alignment=TA_CENTER,
            textColor=_ACCENT,
            spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=32,
            leading=36,
            alignment=TA_CENTER,
            textColor=_ACCENT_DARK,
            spaceAfter=4,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            parent=base,
            fontName=f"{FONT_FAMILY}-Italic",
            fontSize=13,
            alignment=TA_CENTER,
            textColor=_MUTED,
            spaceAfter=2,
        ),
        "cover_note": ParagraphStyle(
            "cover_note",
            parent=base,
            fontSize=9,
            alignment=TA_CENTER,
            textColor=_MUTED,
            spaceAfter=2,
        ),
        "cover_card_head": ParagraphStyle(
            "cover_card_head",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=8,
            leading=10,
            textColor=_ACCENT_DARK,
            spaceAfter=0,
        ),
        "cover_card_val": ParagraphStyle(
            "cover_card_val",
            parent=base,
            fontSize=10,
            leading=12.5,
            textColor=_INK,
            spaceAfter=0,
        ),
        "disclaimer": ParagraphStyle(
            "disclaimer",
            parent=base,
            fontSize=8,
            leading=11.5,
            textColor=_MUTED,
            spaceAfter=2,
        ),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.5, leading=11.5, spaceAfter=0),
        "cell_head": ParagraphStyle(
            "cell_head",
            parent=base,
            fontName=f"{FONT_FAMILY}-Bold",
            fontSize=8.5,
            leading=11.5,
            textColor=_ACCENT_DARK,
            spaceAfter=0,
        ),
    }


class _NumberedCanvas(_canvas.Canvas):
    """Two-pass canvas so the footer can say 'Page X of Y'."""

    def __init__(self, *args: object, header: str = "", footer: str = "", **kw: object) -> None:
        super().__init__(*args, **kw)
        self._header = header
        self._footer = footer
        self._pages: list[dict] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab API
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        for state in self._pages:
            self.__dict__.update(state)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        page = self._pageNumber
        # header: brand mark left, the generated timestamp right
        self.setFont(f"{FONT_FAMILY}-Bold", 7.5)
        self.setFillColor(_ACCENT_DARK)
        self.drawString(_MARGIN, _PAGE[1] - 12 * mm, "VyaparSarathi")
        self.setFont(FONT_FAMILY, 7.5)
        self.setFillColor(_MUTED)
        self.drawRightString(_PAGE[0] - _MARGIN, _PAGE[1] - 12 * mm, self._header)
        self.setStrokeColor(_ACCENT)
        self.setLineWidth(1.1)
        self.line(_MARGIN, _PAGE[1] - 13.5 * mm, _PAGE[0] - _MARGIN, _PAGE[1] - 13.5 * mm)
        # footer: one line -- brand, report id, and pagination. The full
        # disclaimer lives once on the cover page (plus a pointer to it at
        # the top of Quick Business Summary); repeating a paraphrase of it
        # on every page added length without adding information.
        self.setStrokeColor(_RULE)
        self.setLineWidth(0.5)
        self.line(_MARGIN, 15 * mm, _PAGE[0] - _MARGIN, 15 * mm)
        self.setFillColor(_MUTED)
        self.drawString(_MARGIN, 11 * mm, f"{self._footer} | Page {page} of {total}")


def render_pdf_bytes(doc: DprDocument) -> bytes:
    st = _styles()
    buf = BytesIO()

    header = f"Generated {to_ist(doc.generated_at).strftime('%d %b %Y %H:%M IST')}"
    footer = f"VyaparSarathi | Report ID: {doc.report_id}"

    frame = Frame(
        _MARGIN,
        _MARGIN,
        _PAGE[0] - 2 * _MARGIN,
        _PAGE[1] - 2 * _MARGIN - 6 * mm,
        id="main",
        leftPadding=0,
        rightPadding=0,
        topPadding=0,
        bottomPadding=0,
    )
    template = PageTemplate(id="main", frames=[frame])
    pdf = BaseDocTemplate(
        buf,
        pagesize=_PAGE,
        pageTemplates=[template],
        title=f"VyaparSarathi DPR {doc.report_id}",
        author="VyaparSarathi",
        subject=doc.cover.report_kind,
        invariant=1,
    )

    story: list[Flowable] = []
    story += _cover(doc.cover, st)
    story.append(PageBreak())
    # The main report is 7 short, reader-facing chapters that flow one
    # after another (no hard page break -- a colored rule + spacing marks
    # the boundary, so a chapter with little content costs a few lines,
    # not a wasted page). Only the switch from narrative report to
    # supporting appendix gets a hard break, since that's a genuine change
    # of register, not just the next topic.
    for chapter_builder in (
        _chapter_quick_summary,
        _chapter_local_market,
        _chapter_opportunity,
        _chapter_money,
        _chapter_risks,
        _chapter_next_steps,
        _chapter_data_limitations,
    ):
        story += chapter_builder(doc, st)
    story.append(PageBreak())
    story += _chapter_annexures(doc, st)

    pdf.build(
        story,
        canvasmaker=partial(_NumberedCanvas, header=header, footer=footer),
    )
    return buf.getvalue()


# --- helpers ---------------------------------------------------------


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(text), style)


def _p_labeled(label: str, body: str, style: ParagraphStyle) -> Paragraph:
    """A `<b>Label:</b> body` paragraph, built the correct way: the markup
    is added AFTER escaping only the two dynamic fragments, never by handing
    a string that already contains literal `<b>` tags to `_p()` — `_esc()`
    would escape those tags too, and ReportLab's own mini-XML parser then
    decodes that escape back into visible literal `<b>` text on the page
    instead of an actual bold run."""
    return Paragraph(f"<b>{_esc(label)}:</b> {_esc(body)}", style)


# Every string that reaches the PDF funnels through here (directly via `_p`,
# or via `_kv_table`/`_long_table`/`_bullets`/`_pv_text`), so this is the one
# place that needs to know the reader-facing house style avoids em dashes —
# every upstream module can keep writing them in comments/docstrings
# unaffected. A bare placeholder glyph becomes a word ("N/A"); an em dash
# used mid-sentence as a clause break reads more naturally as a comma; any
# stray survivor (no surrounding space, e.g. glued to a word) falls back to
# a plain hyphen rather than vanishing silently.
_MID_SENTENCE_EM_DASH_RE = re.compile(r"\s+—\s+")


def _de_emdash(text: str) -> str:
    if text == "—":
        return "N/A"
    text = _MID_SENTENCE_EM_DASH_RE.sub(", ", text)
    return text.replace("—", "-").replace("–", "-")


def _esc(text: str) -> str:
    text = _de_emdash(str(text))
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pv_text(pv: ProvenancedValue) -> str:
    tag = _ORIGIN_TAG[pv.origin]
    if pv.origin is ValueOrigin.SOURCED and pv.citation_id:
        tag = f"sourced · {pv.citation_id}"
    extra = f" — {pv.note}" if pv.note else ""
    return f"{_esc(pv.display)}  <font size=6 color='#7a869a'>[{_esc(tag)}]</font>{_esc(extra)}"


def _kv_table(rows: Iterable[tuple[str, object]], st: dict[str, ParagraphStyle]) -> Table:
    data: list[list[object]] = []
    for label, value in rows:
        if isinstance(value, ProvenancedValue):
            cell = Paragraph(_pv_text(value), st["cell"])
        else:
            cell = Paragraph(_esc(str(value)), st["cell"])
        data.append([Paragraph(_esc(label), st["cell_head"]), cell])
    tbl = Table(data, colWidths=[62 * mm, None], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.25, _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return tbl


def _long_table(
    headers: list[str], rows: list[list[object]], widths: list[float | None], st: dict
) -> LongTable:
    head = [Paragraph(_esc(h), st["cell_head"]) for h in headers]
    body = [
        [
            cell
            if isinstance(cell, Flowable)
            else Paragraph(
                _pv_text(cell) if isinstance(cell, ProvenancedValue) else _esc(str(cell)),
                st["cell"],
            )
            for cell in row
        ]
        for row in rows
    ]
    tbl = LongTable([head, *body], colWidths=widths, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), _HEAD_BG),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.25, _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return tbl


def _bullets(items: Iterable[str], st: dict[str, ParagraphStyle]) -> list[Flowable]:
    out: list[Flowable] = []
    for it in items:
        out.append(Paragraph("•&nbsp;" + _esc(it), st["body"]))
    return out


def _chapter_title(title: str, st: dict) -> Flowable:
    # The title + accent rule are kept together so a chapter header is
    # never stranded alone at the bottom of a page, separated from its own
    # first line of content — cheap insurance now that chapters flow
    # continuously instead of each starting a fresh page.
    return KeepTogether(
        [
            _p(title, st["h1"]),
            HRFlowable(width=38 * mm, color=_ACCENT, thickness=1.6, spaceAfter=8, hAlign="LEFT"),
        ]
    )


_S = TypeVar("_S", bound=ReportSection)


def _section_body(
    section: _S,
    doc: DprDocument,
    st: dict,
    builder: Callable[[_S, DprDocument, dict], list[Flowable]],
) -> list[Flowable]:
    """One underlying `ReportSection`'s own gap-box (if any) plus its body,
    WITHOUT a chapter-level title — several sections can share one reader-
    facing chapter heading (`_chapter`), each still carrying its own
    evidence-gap disclosure independently."""
    out: list[Flowable] = []
    if section.status is SectionStatus.EVIDENCE_GAP:
        out.append(_gap_box(section.gap_note or "Not available from current evidence.", st))
    elif section.status is SectionStatus.PARTIAL and section.gap_note:
        out.append(_gap_box(section.gap_note, st))
    out += builder(section, doc, st)
    return out


def _chapter(title: str, body: list[Flowable], *, st: dict) -> list[Flowable]:
    """One main-report (or annexure) chapter: a title, then its body —
    which may combine more than one underlying `ReportSection`'s content
    (`_section_body`) under a single reader-facing heading."""
    return [_chapter_title(title, st), *body]


def _h2_block(
    heading_text: str, *flowables: Flowable | list[Flowable], st: dict
) -> list[Flowable]:
    """A sub-heading kept together with its first piece of content, so the
    heading can never land alone at the bottom of a page — the same
    insurance `_heading` already gives the top-level section title, one
    level down. Returns a length-1 list so call sites keep using
    `out += _h2_block(...)` exactly like they use `out += [...]` today;
    each positional argument may be a single `Flowable` or a `list` of
    them (as `_bullets`/`_long_table` return), and either is accepted
    without the caller having to unpack it first."""
    flat: list[Flowable] = []
    for f in flowables:
        flat.extend(f if isinstance(f, list) else [f])
    return [KeepTogether([_p(heading_text, st["h2"]), *flat])]


def _gap_box(text: str, st: dict) -> Table:
    inner = Paragraph("<b>Evidence gap.</b> " + _esc(text), st["gap"])
    box = Table([[inner]], colWidths=[_PAGE[0] - 2 * _MARGIN])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _GAP_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, _GAP_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return box


def _summary_card(lines: tuple[str, ...], st: dict) -> Table | None:
    """A plain-language callout — 'In short' — at the top of the executive
    summary: what the recommendation means, whether the plan is affordable,
    what the local market looks like, and what to do next, each in one
    reader-facing sentence with no pipeline vocabulary. Purely a
    presentation of figures already computed and shown in full elsewhere in
    this report."""
    if not lines:
        return None
    inner: list[Flowable] = [Paragraph("<b>In short</b>", st["summary"])]
    inner += [Paragraph("• " + _esc(line), st["summary"]) for line in lines]
    box = Table([[inner]], colWidths=[_PAGE[0] - 2 * _MARGIN])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _SUMMARY_BG),
                ("BOX", (0, 0), (-1, -1), 0.5, _SUMMARY_BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return box


# --- cover ---------------------------------------------------------


def _cover_card(cover: CoverPage, st: dict) -> Table:
    rows: list[tuple[str, ProvenancedValue | str]] = [
        ("Proposed business", cover.proposed_business),
        ("Location", cover.location),
        ("Report ID", cover.report_id),
        ("Generated", cover.generated_on),
        ("Session", cover.session_id),
    ]
    data: list[list[Paragraph]] = []
    for label, value in rows:
        val_text = _pv_text(value) if isinstance(value, ProvenancedValue) else _esc(str(value))
        data.append(
            [
                Paragraph(_esc(label.upper()), st["cover_card_head"]),
                Paragraph(val_text, st["cover_card_val"]),
            ]
        )
    tbl = Table(data, colWidths=[42 * mm, None], hAlign="CENTER")
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _ACCENT_TINT),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, -2), 0.5, colors.white),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("LEFTPADDING", (0, 0), (-1, -1), 14),
                ("RIGHTPADDING", (0, 0), (-1, -1), 14),
                ("LINEABOVE", (0, 0), (-1, 0), 2, _ACCENT),
                ("LINEBELOW", (0, -1), (-1, -1), 2, _ACCENT),
            ]
        )
    )
    return tbl


def _cover(cover: CoverPage, st: dict) -> list[Flowable]:
    return [
        Spacer(1, 26 * mm),
        _p("DECISION-SUPPORT REPORT", st["cover_kicker"]),
        _p("VyaparSarathi", st["cover_title"]),
        HRFlowable(
            width=26 * mm, color=_ACCENT, thickness=1.4, spaceBefore=2, spaceAfter=10,
            hAlign="CENTER",
        ),
        _p("Detailed Project Report", st["cover_sub"]),
        Spacer(1, 22 * mm),
        _cover_card(cover, st),
        Spacer(1, 20 * mm),
        _p(cover.report_kind, st["cover_note"]),
        Spacer(1, 30 * mm),
        _disclaimer_box(cover.disclaimer, st),
    ]


def _disclaimer_box(text: str, st: dict) -> Table:
    inner = Paragraph("<b>Disclaimer.</b> " + _esc(text), st["disclaimer"])
    box = Table([[inner]], colWidths=[_PAGE[0] - 2 * _MARGIN])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F6F8")),
                ("BOX", (0, 0), (-1, -1), 0.4, _RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return box


# --- section builders (each renders ONE underlying `ReportSection`'s
# body; a chapter combines one or more of these under one reader-facing
# heading, see "chapter dispatch" below) --------------------------


def _exec(sec: ExecutiveSummary, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    card = _summary_card(sec.plain_summary, st)
    if card is not None:
        out.append(card)
    out += [
        _kv_table(
            [
                ("Recommendation", sec.recommendation),
                ("Reason", sec.verdict_reason or "—"),
                ("Market-data confidence", sec.market_data_confidence),
                ("Financial status", sec.financial_status),
            ],
            st,
        )
    ]
    if sec.strengths:
        out += _h2_block("Major strengths", _bullets(sec.strengths, st), st=st)
    if sec.risks:
        out += _h2_block("Major risks", _bullets(sec.risks, st), st=st)
    if sec.next_actions:
        out += _h2_block("Immediate next actions", _bullets(sec.next_actions, st), st=st)
    if sec.evidence_gaps:
        out += _h2_block("Open evidence gaps", _bullets(sec.evidence_gaps, st), st=st)
    return out


def _profile(sec: EntrepreneurProfileSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _kv_table(
            [
                ("Available Margin Capital", sec.available_margin_capital),
                ("Proposed enterprise", sec.proposed_enterprise),
                ("Standardised category", sec.resolved_category),
                ("Years of experience", sec.years_experience),
                (
                    "Trade experience",
                    ", ".join(sec.experience_categories) or "None stated",
                ),
            ],
            st,
        )
    ]
    if sec.owned_assets:
        out += _h2_block(
            "Owned assets (stated, unverified)",
            _bullets([f"{a.label}: {a.detail}" for a in sec.owned_assets], st),
            st=st,
        )
    if sec.constraints:
        out += _h2_block("Stated constraints", _bullets(sec.constraints, st), st=st)
    out.append(_p(sec.unverified_note, st["small"]))
    return out


def _market(sec: MarketAssessmentSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _kv_table(
            [
                ("Resolved location", sec.resolved_location),
                ("Market-data confidence", sec.data_confidence),
                ("Direct competitors", sec.direct_competitors),
                ("Adjacent competitors", sec.adjacent_competitors),
                ("Nearest competitor", sec.nearest_competitor),
                ("Competition signal", sec.competition_signal),
                ("Market reading", sec.market_label),
                ("Reading basis", sec.label_reason or "—"),
            ],
            st,
        )
    ]
    if sec.admin_hierarchy:
        out += _h2_block(
            "Administrative hierarchy",
            _long_table(
                ["Level", "Value"],
                [[i.label, i.detail] for i in sec.admin_hierarchy],
                [50 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.source_coverage:
        out += _h2_block(
            "Source coverage",
            _long_table(
                ["Source", "Detail"],
                [[i.label, i.detail] for i in sec.source_coverage],
                [42 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.demand_signals:
        out += _h2_block(
            "Demand signals",
            _long_table(
                ["Signal", "Value", "Origin"],
                [[i.label, i.detail, i.origin] for i in sec.demand_signals],
                [48 * mm, None, 26 * mm],
                st,
            ),
            st=st,
        )
    if sec.caveats:
        out += _h2_block("Data caveats", _bullets(sec.caveats, st), st=st)
    out.append(_p(sec.completeness_note, st["small"]))
    return out


def _market_price(sec: MarketPriceSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _kv_table([("District queried", sec.district_used)], st),
    ]
    if sec.benchmarks:
        out += _h2_block(
            "Commodity benchmarks (wholesale, per quintal)",
            _long_table(
                ["Commodity", "Price range", "Median price", "Most recent arrival", "Markets"],
                [
                    [
                        b.commodity,
                        b.price_range,
                        b.median_price,
                        b.most_recent_arrival + (" (stale)" if b.is_stale else ""),
                        ", ".join(b.markets_sampled),
                    ]
                    for b in sec.benchmarks
                ],
                [28 * mm, 40 * mm, 34 * mm, 32 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.not_a_retail_price_note:
        out.append(_p(sec.not_a_retail_price_note, st["small"]))
    if sec.caveats:
        out += _h2_block("Caveats", _bullets(sec.caveats, st), st=st)
    return out


def _opportunity(sec: OpportunitySection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _kv_table(
            [
                ("Proposed-business score", sec.proposed_score),
                ("Stance", sec.stance),
                ("Stance reason", sec.stance_reason or "—"),
                ("Recommended pivot", sec.recommended_pivot),
                ("Market-data confidence", sec.market_data_confidence),
            ],
            st,
        )
    ]
    if sec.factors:
        out += _h2_block(
            "Score factor breakdown",
            _long_table(
                ["Factor", "Weight", "Contribution", "Why"],
                [
                    [f.name, f.weight_pct, f.contribution, f.villager_reason or f.reason]
                    for f in sec.factors
                ],
                [34 * mm, 16 * mm, 34 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.alternatives:
        out += _h2_block(
            "Alternative businesses scored for this location & profile",
            _long_table(
                ["Rank", "Business", "Score", "Market reading", "Capital fit", "Reasons"],
                [
                    [
                        str(a.rank) if a.rank is not None else "—",
                        a.business,
                        a.score,
                        a.market_label,
                        a.capital_fit,
                        "; ".join(a.villager_reasons or a.reasons) or "—",
                    ]
                    for a in sec.alternatives
                ],
                [12 * mm, 30 * mm, 30 * mm, 26 * mm, 22 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.caveats:
        out += _h2_block("Caveats", _bullets(sec.caveats, st), st=st)
    return out


def _project_plan(sec: ProjectPlanSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out = [
        _kv_table(
            [
                ("Business category", sec.category),
                ("Catchment radius used", sec.catchment_radius),
                ("One-time project / setup cost", sec.stated_project_cost),
                ("Expected monthly revenue", sec.stated_monthly_revenue),
                ("Cost of goods (% of revenue)", sec.stated_cogs_pct),
                ("Monthly fixed operating cost", sec.stated_fixed_opex),
            ],
            st,
        ),
        _p(sec.note, st["small"]),
    ]
    if sec.margin_note:
        out.append(_p(sec.margin_note, st["small"]))
    return out


def _distribution_channels(
    sec: DistributionChannelSection, doc: DprDocument, st: dict
) -> list[Flowable]:
    out: list[Flowable] = [
        _kv_table(
            [
                ("Business category", sec.category),
                ("Primary channel", sec.primary_channel),
                ("B2B potential", sec.b2b_potential),
                ("Supply channel", sec.supply_channel),
            ],
            st,
        ),
    ]
    if sec.secondary_channels:
        out += _h2_block(
            "Secondary channels", _bullets(sec.secondary_channels, st), st=st
        )
    if sec.single_channel_risk_note:
        out.append(_p_labeled("Risk", sec.single_channel_risk_note, st["body"]))
    if sec.guidance_note:
        out.append(_p(sec.guidance_note, st["small"]))
    return out


def _financial(sec: FinancialAssessmentSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    if sec.incomplete_note:
        out.append(_gap_box(sec.incomplete_note, st))
    if sec.missing_core_drivers:
        out += _h2_block(
            "Missing core financial drivers", _bullets(sec.missing_core_drivers, st), st=st
        )
    out += _h2_block(
        "Feasibility & structure",
        _kv_table(
            [
                ("Feasibility status", sec.feasibility_status),
                ("Deciding rung", _plain_rung(sec.deciding_rung) if sec.deciding_rung else "—"),
                ("Project cost", sec.project_cost),
                ("Available promoter cash (stated)", sec.promoter_contribution),
                ("Capital remaining after margin", sec.capital_remaining_after_margin),
                ("Required promoter margin", sec.required_promoter_margin),
                ("Indicated loan", sec.indicated_loan),
                ("Margin shortfall vs stated cash", sec.margin_shortfall),
                ("Financing structure", sec.financing_scheme),
            ],
            st,
        ),
        st=st,
    )
    if sec.capacity_note:
        out += _h2_block(
            "Scheme capacity screen",
            _p(sec.capacity_note, st["small"]),
            _kv_table(
                [
                    ("Feasible project cost (capacity screen)", sec.capacity_feasible_project_cost),
                    ("Required promoter margin (capacity screen)", sec.capacity_required_margin),
                    ("Indicated loan (capacity screen)", sec.capacity_indicated_loan),
                ],
                st,
            ),
            st=st,
        )
    out += _h2_block(
        "Loan terms & servicing",
        _kv_table(
            [
                ("Loan principal", sec.loan_principal),
                ("Interest rate", sec.interest_rate),
                ("Tenure", sec.tenure),
                ("Moratorium", sec.moratorium),
                ("Monthly EMI", sec.monthly_emi),
            ],
            st,
        ),
        st=st,
    )
    if sec.repayment_schedule:
        out += _h2_block(
            "Repayment schedule (quarterly)",
            _long_table(
                ["Quarter", "Status", "Opening balance", "Principal paid", "Interest paid",
                 "Closing balance"],
                [
                    [
                        q.label,
                        q.status,
                        q.opening_balance,
                        q.principal_paid,
                        q.interest_paid,
                        q.closing_balance,
                    ]
                    for q in sec.repayment_schedule
                ],
                [28 * mm, 34 * mm, 26 * mm, 26 * mm, 26 * mm, None],
                st,
            ),
            st=st,
        )
        notes = [q.note for q in sec.repayment_schedule if q.note]
        if notes:
            out += [_p(_esc("; ".join(dict.fromkeys(notes))), st["small"])]
    out += _h2_block(
        "Coverage & viability",
        _kv_table(
            [
                ("Average annual DSCR", sec.average_annual_dscr),
                ("First post-moratorium year DSCR", sec.first_post_moratorium_dscr),
                ("Minimum cash balance", sec.minimum_cash_balance),
                ("Cash on hand at EMI start", sec.cash_at_emi_start),
                ("Operating break-even month", sec.operating_break_even_month),
                ("Cash break-even month", sec.cash_break_even_month),
                ("Share of plan resting on assumptions", sec.assumption_share),
            ],
            st,
        ),
        st=st,
    )
    if sec.breaking_point:
        out.append(_p_labeled("Named breaking point", sec.breaking_point, st["body"]))
    if sec.stress_scenarios:
        out += _h2_block(
            "Stress scenarios",
            _long_table(
                ["Scenario", "Applied", "Min cash", "Negative months", "Avg DSCR", "Outcome"],
                [
                    [
                        s.name,
                        "yes" if s.applied else f"no — {s.skipped_reason}",
                        s.minimum_cash,
                        s.negative_cash_months,
                        s.average_dscr,
                        s.outcome or "—",
                    ]
                    for s in sec.stress_scenarios
                ],
                [30 * mm, 24 * mm, 28 * mm, 24 * mm, 24 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.finance_findings:
        out += _h2_block("Findings", _bullets(sec.finance_findings, st), st=st)
    return out


def _scheme(sec: SchemeKnowledgeSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _p_labeled("Corpus", sec.corpus_note, st["body"]),
        _p(_esc(sec.query_summary or ""), st["small"]),
    ]
    out += _h2_block(
        "Resolved scheme parameters",
        _long_table(
            ["Parameter", "Value", "Status", "Citation", "Conditions"],
            [
                [
                    line.name,
                    line.value,
                    _plain_param_status(line.status),
                    line.citation_id or "—",
                    "; ".join(line.conditions) or "—",
                ]
                for line in sec.resolved_parameters
            ],
            [40 * mm, 34 * mm, 26 * mm, 18 * mm, None],
            st,
        ),
        st=st,
    )
    if sec.statutory_fees:
        out += _h2_block(
            "Statutory fees / deposits",
            _long_table(
                ["Item", "Value", "Status", "Citation"],
                [
                    [
                        line.name,
                        line.value,
                        _plain_param_status(line.status),
                        line.citation_id or "—",
                    ]
                    for line in sec.statutory_fees
                ],
                [46 * mm, 40 * mm, 30 * mm, None],
                st,
            ),
            st=st,
        )
    if sec.retrieved_passages:
        passage_flowables: list[Flowable] = []
        for p in sec.retrieved_passages:
            passage_flowables.append(
                Paragraph(
                    f"<b>[{_esc(p.citation_id)}] {_esc(p.heading)}</b> "
                    f"<font size=6 color='#7a869a'>[{_esc(p.tier)}]</font>",
                    st["body"],
                )
            )
            passage_flowables.append(_p(f"“{_esc(p.excerpt)}”", st["small"]))
        out += _h2_block(
            "Retrieved passages (verbatim, for citation)", passage_flowables, st=st
        )
    if sec.no_evidence_parameters:
        out += _h2_block(
            "No verified evidence for", _bullets(sec.no_evidence_parameters, st), st=st
        )
    out.append(_p(sec.declared_config_note, st["small"]))
    return out


def _risks(sec: RisksSwotSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    quads = [
        ("Strengths", sec.strengths),
        ("Weaknesses", sec.weaknesses),
        ("Opportunities", sec.opportunities),
        ("Threats", sec.threats),
    ]
    for title, items in quads:
        if items:
            # `source_ref` (a literal internal field path, e.g.
            # "opportunity.candidates[proposed].capital_fit") is audit
            # trail, not reader content — it stays on the model and in
            # Annexure B's calculation-provenance table, but is never
            # printed inline in front of the entrepreneur.
            out += _h2_block(title, _bullets([i.text for i in items], st), st=st)
    if sec.quadrant_notes:
        out += _h2_block("Quadrant notes", _bullets(sec.quadrant_notes, st), st=st)
    if sec.structured_risks:
        out += _h2_block("Structured risks", _bullets(sec.structured_risks, st), st=st)
    if sec.mitigations:
        out += _h2_block(
            "Mitigations / next checks", _bullets(sec.mitigations, st), st=st
        )
    if sec.data_caveats:
        out += _h2_block("Data caveats", _bullets(sec.data_caveats, st), st=st)
    return out


def _assumptions(sec: AssumptionsSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    groups = [
        ("User-provided inputs (unverified)", sec.user_inputs),
        ("Deterministic calculations", sec.calculated_results),
        ("Source-backed facts", sec.source_facts),
        (
            "Declared configuration (SIH problem statement, not a scheme rule)",
            sec.declared_configuration,
        ),
        ("Explicit assumptions", sec.assumptions),
    ]
    for title, items in groups:
        content = (
            _long_table(
                ["Item", "Detail"],
                [[i.label, i.detail] for i in items],
                [55 * mm, None],
                st,
            )
            if items
            else _p("None.", st["small"])
        )
        out += _h2_block(title, content, st=st)
    out += _h2_block(
        "Unavailable evidence",
        _bullets(sec.unavailable_evidence or ["None — every required input was available."], st),
        st=st,
    )
    out += _h2_block("Confidence notes", _bullets(sec.confidence_notes, st), st=st)
    return out


def _annexures(sec: AnnexuresSection, doc: DprDocument, st: dict) -> list[Flowable]:
    sources_content = (
        _long_table(
            ["ID", "Reference", "Publisher / tier", "Locator", "Dates"],
            [
                [
                    c.citation_id,
                    c.text,
                    (c.publisher or c.tier or "—"),
                    c.locator or "—",
                    " / ".join(x for x in (c.reference_date, c.retrieved_at) if x) or "—",
                ]
                for c in sec.sources
            ],
            [12 * mm, None, 34 * mm, 22 * mm, 30 * mm],
            st,
        )
        if sec.sources
        else _p("No external sources were cited in this report.", st["small"])
    )
    out: list[Flowable] = _h2_block("A. Sources & citations", sources_content, st=st)

    calc_content = (
        _long_table(
            ["Result", "Value", "Engine", "Inputs"],
            [
                [c.result, c.display.display, c.engine, ", ".join(c.inputs)]
                for c in sec.calculation_provenance
            ],
            [42 * mm, 34 * mm, 40 * mm, None],
            st,
        )
        if sec.calculation_provenance
        else _p("No deterministic calculations were recorded.", st["small"])
    )
    out += _h2_block("B. Calculation provenance", calc_content, st=st)

    history_content = (
        _long_table(
            ["Field", "State", "Value", "Stated as", "Turn", "Superseded"],
            [
                [
                    h.slot,
                    h.state,
                    h.value,
                    h.raw_text or "—",
                    str(h.set_on_turn),
                    "yes" if h.superseded else "",
                ]
                for h in sec.slot_history
            ],
            [40 * mm, 22 * mm, 26 * mm, None, 12 * mm, 20 * mm],
            st,
        )
        if sec.slot_history
        else _p("No fields were stated.", st["small"])
    )
    out += _h2_block("C. Stated-input history", history_content, st=st)

    out += _h2_block(
        "D. Glossary",
        _long_table(
            ["Term", "Definition"],
            [[g.term, g.definition] for g in sec.glossary],
            [42 * mm, None],
            st,
        ),
        st=st,
    )
    return out


# --- visualizations -------------------------------------------------
#
# Every chart below reads a value already computed and displayed in full
# elsewhere in this report (never a new calculation) and degrades to
# nothing (returns `None`, silently omitted) rather than plotting a
# fabricated or zeroed point when the underlying evidence is a gap.

_SCORE_RE = re.compile(r"^(-?\d+(?:\.\d+)?)/100$")


def _extract_score(pv: object) -> float | None:
    """`ProvenancedValue.display` is always exactly `f"{value}/100"` for an
    opportunity score, one fixed format used nowhere else -- a
    non-matching display (an evidence gap's "not available", say) yields
    `None`, so a gap is omitted from the chart, never plotted as 0."""
    if not isinstance(pv, ProvenancedValue):
        return None
    m = _SCORE_RE.match(pv.display)
    return float(m.group(1)) if m else None


def _extract_raw_amount(pv: object) -> float | None:
    if not isinstance(pv, ProvenancedValue) or pv.raw is None:
        return None
    try:
        return float(pv.raw)
    except ValueError:
        return None


def _market_snapshot_cards(sec: MarketAssessmentSection, st: dict) -> Table | None:
    cards = [
        ("Direct competitors", sec.direct_competitors),
        ("Nearest competitor", sec.nearest_competitor),
        ("Market reading", sec.market_label),
    ]
    row = []
    for label, pv in cards:
        text = pv.display if isinstance(pv, ProvenancedValue) else str(pv)
        row.append(
            [
                Paragraph(_esc(label.upper()), st["cover_card_head"]),
                Paragraph(_esc(text), st["cover_card_val"]),
            ]
        )
    if all(
        isinstance(pv, ProvenancedValue) and pv.origin is ValueOrigin.NOT_AVAILABLE
        for _, pv in cards
    ):
        return None
    width = (_PAGE[0] - 2 * _MARGIN) / 3
    tbl = Table([row], colWidths=[width, width, width], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _ACCENT_TINT),
                ("BOX", (0, 0), (-1, -1), 0.5, _SUMMARY_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.white),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return tbl


_OUTCOME_COLOR = {
    "feasible": colors.HexColor("#2E7D53"),
    "cash flow stress": colors.HexColor("#C77B22"),
    "unserviceable": colors.HexColor("#B3261E"),
}


def _stress_status_list(scenarios: tuple, st: dict) -> Table | None:
    rows: list[list[object]] = []
    for s in scenarios:
        outcome = s.outcome or "Not evaluated"
        swatch = Table([[""]], colWidths=[4 * mm], rowHeights=[4 * mm])
        swatch.setStyle(
            TableStyle(
                [("BACKGROUND", (0, 0), (-1, -1), _OUTCOME_COLOR.get(outcome.lower(), _MUTED))]
            )
        )
        rows.append(
            [swatch, Paragraph(_esc(s.name), st["cell"]), Paragraph(_esc(outcome), st["cell"])]
        )
    if not rows:
        return None
    tbl = Table(rows, colWidths=[6 * mm, 55 * mm, None], hAlign="LEFT")
    tbl.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return tbl


def _opportunity_bar_chart(sec: OpportunitySection) -> Drawing | None:
    ranked = sorted(sec.alternatives, key=lambda a: a.rank if a.rank is not None else 999)
    items: list[tuple[str, object]] = [("Proposed", sec.proposed_score)]
    items += [(a.business, a.score) for a in ranked[:3]]
    labels: list[str] = []
    values: list[float] = []
    for label, pv in items:
        score = _extract_score(pv)
        if score is None:
            continue
        labels.append(label if len(label) <= 16 else label[:15] + "…")
        values.append(score)
    if len(values) < 2:
        return None
    d = Drawing(150 * mm, 55 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y = 8 * mm, 12 * mm
    chart.width, chart.height = 138 * mm, 38 * mm
    chart.data = [values]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = FONT_FAMILY
    chart.categoryAxis.labels.fontSize = 7
    chart.valueAxis.valueMin = 0
    chart.valueAxis.valueMax = 100
    chart.valueAxis.labels.fontName = FONT_FAMILY
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = _ACCENT
    d.add(chart)
    return d


def _financial_structure_chart(fin: FinancialAssessmentSection) -> Drawing | None:
    pairs = (
        ("Project cost", fin.project_cost, fin.capacity_feasible_project_cost),
        ("Promoter margin", fin.required_promoter_margin, fin.capacity_required_margin),
        ("Loan", fin.indicated_loan, fin.capacity_indicated_loan),
    )
    labels: list[str] = []
    actual: list[float] = []
    capacity: list[float] = []
    for label, actual_pv, capacity_pv in pairs:
        a = _extract_raw_amount(actual_pv)
        c = _extract_raw_amount(capacity_pv)
        if a is None or c is None:
            continue
        labels.append(label)
        actual.append(a)
        capacity.append(c)
    if not labels:
        return None
    d = Drawing(150 * mm, 60 * mm)
    chart = VerticalBarChart()
    chart.x, chart.y = 8 * mm, 12 * mm
    chart.width, chart.height = 120 * mm, 40 * mm
    chart.data = [actual, capacity]
    chart.categoryAxis.categoryNames = labels
    chart.categoryAxis.labels.fontName = FONT_FAMILY
    chart.categoryAxis.labels.fontSize = 7.5
    chart.valueAxis.valueMin = 0
    chart.valueAxis.labels.fontName = FONT_FAMILY
    chart.valueAxis.labels.fontSize = 7
    chart.bars[0].fillColor = _ACCENT
    chart.bars[1].fillColor = _MUTED
    legend = Legend()
    legend.x, legend.y = 132 * mm, 40 * mm
    legend.fontName = FONT_FAMILY
    legend.fontSize = 7
    legend.dxTextSpace = 4
    legend.colorNamePairs = [
        (_ACCENT, "Actual business requirement"),
        (_MUTED, "Theoretical scheme capacity"),
    ]
    d.add(chart)
    d.add(legend)
    return d


# --- chapter dispatch ---------------------------------------------
#
# The 12 underlying `ReportSection`s are grouped into 8 reader-facing
# chapters (CLAUDE.md-external presentation pass, §see DPR polish plan) so
# the main report reads as a short narrative. Every builder above is reused
# unchanged; a chapter either (a) shows a section's body directly, (b)
# shows a trimmed `model_copy` of it with a pointer to the full version, or
# (c) marks a sub-section with the section's own original title (a plain
# h2 paragraph, not `_h2_block`, so a long inner table can still split
# across pages the same way it always could). Nothing computed by an
# engine changes; this only decides how much of it to show where.

_ANNEXURE_E_POINTER = "The complete detail is in Annexure E."


def _repayment_summary_rows(schedule: tuple) -> list:
    """First, middle, and last quarter only — the full quarter-by-quarter
    table moves to Annexure E; a 3-row snapshot is enough to see the shape
    of the repayment without dumping years of table rows into the main
    report."""
    if not schedule:
        return []
    n = len(schedule)
    idxs = sorted({0, n // 2, n - 1})
    return [schedule[i] for i in idxs]


def _chapter_quick_summary(doc: DprDocument, st: dict) -> list[Flowable]:
    sec = doc.executive_summary
    capped_gaps = sec.evidence_gaps[:3]
    capped_risks = sec.risks[:3]
    trimmed = sec.model_copy(
        update={"next_actions": (), "evidence_gaps": capped_gaps, "risks": capped_risks}
    )
    body = _section_body(trimmed, doc, st, _exec)
    # The full disclaimer is printed once, on the cover page -- this is
    # just a pointer to it, placed right after the "In short" callout card
    # (the card, if any, is always the first flowable `_exec` builds).
    insert_at = 1 if body and isinstance(body[0], Table) else 0
    body.insert(insert_at, _p("Full disclaimer on the cover page.", st["small"]))
    if len(sec.evidence_gaps) > len(capped_gaps) or len(sec.risks) > len(capped_risks):
        body.append(
            _p(
                "The full list of risks and evidence gaps is in the Risks & "
                "What Could Go Wrong chapter and Annexure E.",
                st["small"],
            )
        )
    return _chapter("Quick Business Summary", body, st=st)


def _chapter_local_market(doc: DprDocument, st: dict) -> list[Flowable]:
    market_trimmed = doc.market.model_copy(update={"admin_hierarchy": (), "source_coverage": ()})
    body = [_p(doc.market.title, st["h2"])]
    cards = _market_snapshot_cards(doc.market, st)
    if cards is not None:
        body.append(cards)
        body.append(Spacer(1, 4))
    body += _section_body(market_trimmed, doc, st, _market)
    if doc.market.admin_hierarchy or doc.market.source_coverage:
        body.append(_p(_ANNEXURE_E_POINTER, st["small"]))
    body.append(_p(doc.market_price.title, st["h2"]))
    body += _section_body(doc.market_price, doc, st, _market_price)
    return _chapter("Your Local Market", body, st=st)


def _chapter_opportunity(doc: DprDocument, st: dict) -> list[Flowable]:
    opp = doc.opportunity
    opp_trimmed = opp.model_copy(update={"factors": (), "alternatives": opp.alternatives[:3]})
    body = [_p(opp.title, st["h2"])]
    chart = _opportunity_bar_chart(opp)
    if chart is not None:
        body.append(chart)
    body += _section_body(opp_trimmed, doc, st, _opportunity)
    if opp.factors or len(opp.alternatives) > 3:
        body.append(_p(_ANNEXURE_E_POINTER, st["small"]))
    body.append(_p(doc.distribution_channels.title, st["h2"]))
    body += _section_body(doc.distribution_channels, doc, st, _distribution_channels)
    return _chapter("Business Opportunity & Competition", body, st=st)


def _chapter_money(doc: DprDocument, st: dict) -> list[Flowable]:
    fin = doc.financial
    fin_trimmed = fin.model_copy(update={"repayment_schedule": (), "finance_findings": ()})
    body = [_p(fin.title, st["h2"])]
    structure_chart = _financial_structure_chart(fin)
    if structure_chart is not None:
        body.append(_p("Actual requirement vs. theoretical scheme capacity", st["small"]))
        body.append(structure_chart)
    body += _section_body(fin_trimmed, doc, st, _financial)
    if fin.repayment_schedule:
        body += _h2_block(
            "Repayment snapshot (first, mid, and final quarter)",
            _long_table(
                ["Quarter", "Status", "Opening balance", "Principal paid", "Interest paid",
                 "Closing balance"],
                [
                    [q.label, q.status, q.opening_balance, q.principal_paid,
                     q.interest_paid, q.closing_balance]
                    for q in _repayment_summary_rows(fin.repayment_schedule)
                ],
                [28 * mm, 34 * mm, 26 * mm, 26 * mm, 26 * mm, None],
                st,
            ),
            st=st,
        )
        body.append(_p(_ANNEXURE_E_POINTER, st["small"]))
    body.append(_p(doc.project_plan.title, st["h2"]))
    body += _section_body(doc.project_plan, doc, st, _project_plan)
    return _chapter("Money & Loan Plan", body, st=st)


def _chapter_risks(doc: DprDocument, st: dict) -> list[Flowable]:
    body = [_p(doc.risks_swot.title, st["h2"])]
    body += _section_body(doc.risks_swot, doc, st, _risks)
    stress_list = _stress_status_list(doc.financial.stress_scenarios, st)
    if stress_list is not None:
        body += _h2_block("Stress-test outcomes at a glance", stress_list, st=st)
    if doc.financial.finance_findings:
        body += _h2_block(
            "Financial findings", _bullets(doc.financial.finance_findings, st), st=st
        )
    if doc.scheme_knowledge.no_evidence_parameters:
        body.append(
            _p(
                f"Scheme/compliance details could not be verified for "
                f"{len(doc.scheme_knowledge.no_evidence_parameters)} item(s) — "
                "see What You Should Do Next.",
                st["small"],
            )
        )
    return _chapter("Risks & What Could Go Wrong", body, st=st)


def _chapter_next_steps(doc: DprDocument, st: dict) -> list[Flowable]:
    es = doc.executive_summary
    body: list[Flowable] = (
        _bullets(es.next_actions, st)
        if es.next_actions
        else [_p("No specific next actions were generated.", st["small"])]
    )
    body.append(_p_labeled("Scheme evidence", doc.scheme_knowledge.corpus_note, st["body"]))
    body.append(
        _p(
            "Full scheme, compliance, and knowledge-evidence detail is in Annexure E.",
            st["small"],
        )
    )
    return _chapter("What You Should Do Next", body, st=st)


def _chapter_data_limitations(doc: DprDocument, st: dict) -> list[Flowable]:
    notes = doc.assumptions.confidence_notes
    body: list[Flowable] = (
        _bullets(notes, st)
        if notes
        else [_p("No specific confidence caveats were recorded.", st["small"])]
    )
    body.append(
        _p(
            "A full breakdown of inputs, calculations, sources, and "
            "assumptions is in Annexure E.",
            st["small"],
        )
    )
    return _chapter("Data Limitations", body, st=st)


def _chapter_annexures(doc: DprDocument, st: dict) -> list[Flowable]:
    body = _annexures(doc.annexures, doc, st)
    # Everything trimmed out of the main chapters above lives here in full,
    # under the underlying section's own original title, so every fact
    # shown before this restructuring still exists somewhere in the report.
    deferred: tuple[tuple[ReportSection, _SectionBuilder], ...] = (
        (doc.executive_summary, _exec),
        (doc.profile, _profile),
        (doc.market, _market),
        (doc.opportunity, _opportunity),
        (doc.financial, _financial),
        (doc.scheme_knowledge, _scheme),
        (doc.assumptions, _assumptions),
    )
    for sec, builder in deferred:
        body.append(_p(f"E. {sec.title} (full detail)", st["h2"]))
        body += _section_body(sec, doc, st, builder)
    return _chapter("Annexures", body, st=st)


_SectionBuilder = Callable[..., list[Flowable]]
_SECTION_BUILDERS: dict[type, _SectionBuilder] = {
    ExecutiveSummary: _exec,
    EntrepreneurProfileSection: _profile,
    MarketAssessmentSection: _market,
    MarketPriceSection: _market_price,
    OpportunitySection: _opportunity,
    ProjectPlanSection: _project_plan,
    DistributionChannelSection: _distribution_channels,
    FinancialAssessmentSection: _financial,
    SchemeKnowledgeSection: _scheme,
    RisksSwotSection: _risks,
    AssumptionsSection: _assumptions,
    AnnexuresSection: _annexures,
}

__all__ = ["render_pdf_bytes"]
