"""Unicode font registration for the DPR PDF (CLAUDE.md §25 Phase 8).

Every base-14 PDF font (Helvetica, Times, ...) uses WinAnsiEncoding — a
legacy single-byte encoding that predates Unicode's 2010 addition of the
Indian Rupee sign (U+20B9). Without a Unicode TrueType font, `format.py`'s
`RUPEE = "₹"` renders as a missing-glyph box wherever a currency figure
appears. Noto Sans (Google, SIL Open Font License 1.1 — see
`assets/fonts/LICENSE_OFL.txt`) is bundled here specifically because it
covers ₹; the four weights below cover every `ParagraphStyle` this report
uses (body/heading regular+bold, note/caveat italic).
"""

from __future__ import annotations

from pathlib import Path

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

FONT_FAMILY = "NotoSans"

_ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"

_WEIGHTS: tuple[tuple[str, str], ...] = (
    (FONT_FAMILY, "NotoSans-Regular.ttf"),
    (f"{FONT_FAMILY}-Bold", "NotoSans-Bold.ttf"),
    (f"{FONT_FAMILY}-Italic", "NotoSans-Italic.ttf"),
    (f"{FONT_FAMILY}-BoldItalic", "NotoSans-BoldItalic.ttf"),
)

_registered = False


def register_fonts() -> None:
    """Register all four Noto Sans weights with ReportLab and map them as
    one family, so `<b>`/`<i>` inline markup resolves to the right physical
    font instead of silently falling back to a rupee-less base-14 font.
    Idempotent — safe to call from every module that renders a PDF."""
    global _registered
    if _registered:
        return
    for name, filename in _WEIGHTS:
        pdfmetrics.registerFont(TTFont(name, str(_ASSETS_DIR / filename)))
    pdfmetrics.registerFontFamily(
        FONT_FAMILY,
        normal=FONT_FAMILY,
        bold=f"{FONT_FAMILY}-Bold",
        italic=f"{FONT_FAMILY}-Italic",
        boldItalic=f"{FONT_FAMILY}-BoldItalic",
    )
    _registered = True


__all__ = ["FONT_FAMILY", "register_fonts"]
