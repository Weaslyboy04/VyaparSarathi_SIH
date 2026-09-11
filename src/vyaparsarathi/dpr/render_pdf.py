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

from vyaparsarathi.dpr.provenance import ProvenancedValue, ValueOrigin
from vyaparsarathi.dpr.report_models import (
    AnnexuresSection,
    AssumptionsSection,
    CoverPage,
    DprDocument,
    EntrepreneurProfileSection,
    ExecutiveSummary,
    FinancialAssessmentSection,
    MarketAssessmentSection,
    OpportunitySection,
    ProjectPlanSection,
    ReportSection,
    RisksSwotSection,
    SchemeKnowledgeSection,
    SectionStatus,
)

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


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body",
        fontName="Helvetica",
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
            fontName="Helvetica-Bold",
            fontSize=13.5,
            leading=16,
            spaceBefore=16,
            spaceAfter=2,
            textColor=_ACCENT_DARK,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="Helvetica-Bold",
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
            fontName="Helvetica-Bold",
            fontSize=9,
            leading=11,
            alignment=TA_CENTER,
            textColor=_ACCENT,
            spaceAfter=8,
        ),
        "cover_title": ParagraphStyle(
            "cover_title",
            parent=base,
            fontName="Times-Bold",
            fontSize=32,
            leading=36,
            alignment=TA_CENTER,
            textColor=_ACCENT_DARK,
            spaceAfter=4,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            parent=base,
            fontName="Times-Italic",
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
            fontName="Helvetica-Bold",
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
            fontName="Helvetica-Bold",
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
        # header
        self.setFont("Helvetica-Bold", 7.5)
        self.setFillColor(_ACCENT_DARK)
        self.drawString(_MARGIN, _PAGE[1] - 12 * mm, "VyaparSarathi")
        self.setFont("Helvetica", 7.5)
        self.setFillColor(_MUTED)
        self.drawRightString(_PAGE[0] - _MARGIN, _PAGE[1] - 12 * mm, self._header)
        self.setStrokeColor(_ACCENT)
        self.setLineWidth(1.1)
        self.line(_MARGIN, _PAGE[1] - 13.5 * mm, _PAGE[0] - _MARGIN, _PAGE[1] - 13.5 * mm)
        # footer
        self.setStrokeColor(_RULE)
        self.setLineWidth(0.5)
        self.line(_MARGIN, 15 * mm, _PAGE[0] - _MARGIN, 15 * mm)
        self.setFillColor(_MUTED)
        self.drawString(_MARGIN, 11 * mm, self._footer)
        self.drawRightString(_PAGE[0] - _MARGIN, 11 * mm, f"Page {page} of {total}")
        self.drawCentredString(
            _PAGE[0] / 2, 11 * mm, "Decision-support material, not a loan sanction"
        )


def render_pdf_bytes(doc: DprDocument) -> bytes:
    st = _styles()
    buf = BytesIO()

    header = f"{doc.report_id}"
    footer = f"Generated {doc.generated_at.strftime('%d %b %Y %H:%M UTC')}"

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
    sections = doc.ordered_sections()
    for i, section in enumerate(sections):
        # A hard break only where the document genuinely changes register —
        # narrative report -> supporting appendix. Everywhere else, sections
        # flow one after another (a colored rule + spacing marks the
        # boundary), so a section with one line of content costs one line,
        # not a wasted page — the single biggest lever on report length.
        if isinstance(section, AnnexuresSection) and i > 0:
            story.append(PageBreak())
        story += _section_flowables(section, doc, st)

    pdf.build(
        story,
        canvasmaker=partial(_NumberedCanvas, header=header, footer=footer),
    )
    return buf.getvalue()


# --- helpers ---------------------------------------------------------


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(_esc(text), style)


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


def _heading(section: ReportSection, st: dict) -> list[Flowable]:
    # The title + accent rule are kept together so a section header is
    # never stranded alone at the bottom of a page, separated from its own
    # first line of content — cheap insurance now that sections flow
    # continuously instead of each starting a fresh page.
    head = KeepTogether(
        [
            _p(section.title, st["h1"]),
            HRFlowable(width=38 * mm, color=_ACCENT, thickness=1.6, spaceAfter=8, hAlign="LEFT"),
        ]
    )
    out: list[Flowable] = [head]
    if section.status is SectionStatus.EVIDENCE_GAP:
        out.append(_gap_box(section.gap_note or "Not available from current evidence.", st))
    elif section.status is SectionStatus.PARTIAL and section.gap_note:
        out.append(_gap_box(section.gap_note, st))
    return out


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


# --- section dispatch --------------------------------------------


def _section_flowables(section: ReportSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out = _heading(section, st)
    builder = _SECTION_BUILDERS.get(type(section))
    if builder is not None:
        out += builder(section, doc, st)
    return out


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
        out += [_p("Major strengths", st["h2"]), *_bullets(sec.strengths, st)]
    if sec.risks:
        out += [_p("Major risks", st["h2"]), *_bullets(sec.risks, st)]
    if sec.next_actions:
        out += [_p("Immediate next actions", st["h2"]), *_bullets(sec.next_actions, st)]
    if sec.evidence_gaps:
        out += [_p("Open evidence gaps", st["h2"]), *_bullets(sec.evidence_gaps, st)]
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
        out.append(_p("Owned assets (stated, unverified)", st["h2"]))
        out += _bullets([f"{a.label}: {a.detail}" for a in sec.owned_assets], st)
    if sec.constraints:
        out += [_p("Stated constraints", st["h2"]), *_bullets(sec.constraints, st)]
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
        out += [_p("Administrative hierarchy", st["h2"])]
        out.append(
            _long_table(
                ["Level", "Value"],
                [[i.label, i.detail] for i in sec.admin_hierarchy],
                [50 * mm, None],
                st,
            )
        )
    if sec.source_coverage:
        out += [_p("Source coverage", st["h2"])]
        out.append(
            _long_table(
                ["Source", "Detail"],
                [[i.label, i.detail] for i in sec.source_coverage],
                [42 * mm, None],
                st,
            )
        )
    if sec.demand_signals:
        out += [_p("Demand signals", st["h2"])]
        out.append(
            _long_table(
                ["Signal", "Value", "Origin"],
                [[i.label, i.detail, i.origin] for i in sec.demand_signals],
                [48 * mm, None, 26 * mm],
                st,
            )
        )
    if sec.caveats:
        out += [_p("Data caveats", st["h2"]), *_bullets(sec.caveats, st)]
    out.append(_p(sec.completeness_note, st["small"]))
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
        out += [_p("Score factor breakdown", st["h2"])]
        out.append(
            _long_table(
                ["Factor", "Weight", "Contribution", "Why"],
                [
                    [f.name, f.weight_pct, f.contribution, f.villager_reason or f.reason]
                    for f in sec.factors
                ],
                [34 * mm, 16 * mm, 34 * mm, None],
                st,
            )
        )
    if sec.alternatives:
        out += [_p("Alternative businesses scored for this location & profile", st["h2"])]
        out.append(
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
            )
        )
    if sec.caveats:
        out += [_p("Caveats", st["h2"]), *_bullets(sec.caveats, st)]
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


def _financial(sec: FinancialAssessmentSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = []
    if sec.incomplete_note:
        out.append(_gap_box(sec.incomplete_note, st))
    if sec.missing_core_drivers:
        out += [
            _p("Missing core financial drivers", st["h2"]),
            *_bullets(sec.missing_core_drivers, st),
        ]
    out += [
        _p("Feasibility & structure", st["h2"]),
        _kv_table(
            [
                ("Feasibility status", sec.feasibility_status),
                ("Deciding rung", sec.deciding_rung or "—"),
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
    ]
    if sec.capacity_note:
        out += [
            _p("Scheme capacity screen", st["h2"]),
            _p(sec.capacity_note, st["small"]),
            _kv_table(
                [
                    ("Feasible project cost (capacity screen)", sec.capacity_feasible_project_cost),
                    ("Required promoter margin (capacity screen)", sec.capacity_required_margin),
                    ("Indicated loan (capacity screen)", sec.capacity_indicated_loan),
                ],
                st,
            ),
        ]
    out += [
        _p("Loan terms & servicing", st["h2"]),
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
        _p("Coverage & viability", st["h2"]),
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
    ]
    if sec.breaking_point:
        out.append(_p(f"<b>Named breaking point:</b> {_esc(sec.breaking_point)}", st["body"]))
    if sec.stress_scenarios:
        out += [_p("Stress scenarios", st["h2"])]
        out.append(
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
            )
        )
    if sec.finance_findings:
        out += [_p("Findings", st["h2"]), *_bullets(sec.finance_findings, st)]
    return out


def _scheme(sec: SchemeKnowledgeSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [
        _p(f"<b>Corpus:</b> {_esc(sec.corpus_note)}", st["body"]),
        _p(_esc(sec.query_summary or ""), st["small"]),
        _p("Resolved scheme parameters", st["h2"]),
        _long_table(
            ["Parameter", "Value", "Status", "Citation", "Conditions"],
            [
                [
                    line.name,
                    line.value,
                    line.status,
                    line.citation_id or "—",
                    "; ".join(line.conditions) or "—",
                ]
                for line in sec.resolved_parameters
            ],
            [40 * mm, 34 * mm, 26 * mm, 18 * mm, None],
            st,
        ),
    ]
    if sec.statutory_fees:
        out += [_p("Statutory fees / deposits", st["h2"])]
        out.append(
            _long_table(
                ["Item", "Value", "Status", "Citation"],
                [
                    [line.name, line.value, line.status, line.citation_id or "—"]
                    for line in sec.statutory_fees
                ],
                [46 * mm, 40 * mm, 30 * mm, None],
                st,
            )
        )
    if sec.retrieved_passages:
        out += [_p("Retrieved passages (verbatim, for citation)", st["h2"])]
        for p in sec.retrieved_passages:
            out.append(
                _p(
                    f"<b>[{_esc(p.citation_id)}] {_esc(p.heading)}</b> "
                    f"<font size=6 color='#7a869a'>[{_esc(p.tier)}]</font>",
                    st["body"],
                )
            )
            out.append(_p(f"“{_esc(p.excerpt)}”", st["small"]))
    if sec.no_evidence_parameters:
        out += [
            _p("No verified evidence for", st["h2"]),
            *_bullets(sec.no_evidence_parameters, st),
        ]
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
            out.append(_p(title, st["h2"]))
            # `source_ref` (a literal internal field path, e.g.
            # "opportunity.candidates[proposed].capital_fit") is audit
            # trail, not reader content — it stays on the model and in
            # Annexure B's calculation-provenance table, but is never
            # printed inline in front of the entrepreneur.
            out += _bullets([i.text for i in items], st)
    if sec.quadrant_notes:
        out += [_p("Quadrant notes", st["h2"]), *_bullets(sec.quadrant_notes, st)]
    if sec.structured_risks:
        out += [_p("Structured risks", st["h2"]), *_bullets(sec.structured_risks, st)]
    if sec.mitigations:
        out += [_p("Mitigations / next checks", st["h2"]), *_bullets(sec.mitigations, st)]
    if sec.data_caveats:
        out += [_p("Data caveats", st["h2"]), *_bullets(sec.data_caveats, st)]
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
        out.append(_p(title, st["h2"]))
        if items:
            out.append(
                _long_table(
                    ["Item", "Detail"],
                    [[i.label, i.detail] for i in items],
                    [55 * mm, None],
                    st,
                )
            )
        else:
            out.append(_p("None.", st["small"]))
    out.append(_p("Unavailable evidence", st["h2"]))
    out += _bullets(sec.unavailable_evidence or ["None — every required input was available."], st)
    out.append(_p("Confidence notes", st["h2"]))
    out += _bullets(sec.confidence_notes, st)
    return out


def _annexures(sec: AnnexuresSection, doc: DprDocument, st: dict) -> list[Flowable]:
    out: list[Flowable] = [_p("A. Sources & citations", st["h2"])]
    if sec.sources:
        out.append(
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
        )
    else:
        out.append(_p("No external sources were cited in this report.", st["small"]))

    out.append(_p("B. Calculation provenance", st["h2"]))
    if sec.calculation_provenance:
        out.append(
            _long_table(
                ["Result", "Value", "Engine", "Inputs"],
                [
                    [c.result, c.display.display, c.engine, ", ".join(c.inputs)]
                    for c in sec.calculation_provenance
                ],
                [42 * mm, 34 * mm, 40 * mm, None],
                st,
            )
        )
    else:
        out.append(_p("No deterministic calculations were recorded.", st["small"]))

    out.append(_p("C. Stated-input history", st["h2"]))
    if sec.slot_history:
        out.append(
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
        )
    else:
        out.append(_p("No fields were stated.", st["small"]))

    out.append(_p("D. Glossary", st["h2"]))
    out.append(
        _long_table(
            ["Term", "Definition"],
            [[g.term, g.definition] for g in sec.glossary],
            [42 * mm, None],
            st,
        )
    )
    return out


_SectionBuilder = Callable[..., list[Flowable]]
_SECTION_BUILDERS: dict[type, _SectionBuilder] = {
    ExecutiveSummary: _exec,
    EntrepreneurProfileSection: _profile,
    MarketAssessmentSection: _market,
    OpportunitySection: _opportunity,
    ProjectPlanSection: _project_plan,
    FinancialAssessmentSection: _financial,
    SchemeKnowledgeSection: _scheme,
    RisksSwotSection: _risks,
    AssumptionsSection: _assumptions,
    AnnexuresSection: _annexures,
}

__all__ = ["render_pdf_bytes"]
