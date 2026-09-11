"""DPR PDF font registration (CLAUDE.md §25 Phase 8) — the presentation-pass
fix for ₹ rendering as a missing-glyph box. Base-14 PDF fonts (Helvetica/
Times) use WinAnsiEncoding, which predates Unicode's 2010 addition of the
Indian Rupee sign (U+20B9); a bundled Unicode TrueType font is required.
Offline — reads only the font files already bundled in `assets/fonts/`.
"""

from __future__ import annotations

from reportlab.lib import fonts as reportlab_fonts
from reportlab.pdfbase import pdfmetrics

from vyaparsarathi.dpr.fonts import FONT_FAMILY, register_fonts

_RUPEE = 0x20B9


def test_register_fonts_is_idempotent() -> None:
    register_fonts()
    register_fonts()  # must not raise on a second call
    assert pdfmetrics.getFont(FONT_FAMILY) is not None


def test_regular_and_bold_have_the_rupee_glyph() -> None:
    register_fonts()
    assert _RUPEE in pdfmetrics.getFont(FONT_FAMILY).face.charWidths
    assert _RUPEE in pdfmetrics.getFont(f"{FONT_FAMILY}-Bold").face.charWidths


def test_italic_and_bold_italic_have_the_rupee_glyph() -> None:
    register_fonts()
    assert _RUPEE in pdfmetrics.getFont(f"{FONT_FAMILY}-Italic").face.charWidths
    assert _RUPEE in pdfmetrics.getFont(f"{FONT_FAMILY}-BoldItalic").face.charWidths


def test_font_family_is_registered_for_reportlab_markup() -> None:
    """`registerFontFamily` is what lets ReportLab's `<b>`/`<i>` inline
    markup resolve to the right physical font for this family, instead of
    silently falling back to Helvetica (which lacks the rupee glyph)."""
    register_fonts()
    assert reportlab_fonts.tt2ps(FONT_FAMILY, 0, 0) == FONT_FAMILY
    assert reportlab_fonts.tt2ps(FONT_FAMILY, 1, 0) == f"{FONT_FAMILY}-Bold"
    assert reportlab_fonts.tt2ps(FONT_FAMILY, 0, 1) == f"{FONT_FAMILY}-Italic"
    assert reportlab_fonts.tt2ps(FONT_FAMILY, 1, 1) == f"{FONT_FAMILY}-BoldItalic"
