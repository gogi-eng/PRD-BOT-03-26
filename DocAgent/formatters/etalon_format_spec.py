# -*- coding: utf-8 -*-
"""
Эталон форматирования Word — инструкция/положение Дубовика:

N:\\…\\Дубовик В.В\\ИНСТРУКЦИЯ по эксплуатации силовых КЛ  0,4-10кВ  31.07.2026--- ПОЛОЖЕНИЕ.docx
Копия: DocAgent\\etalons\\Инструкция_по_эксплуатации_силовых_КЛ_0.4-10кВ_31.07.2026.docx

Все числовые параметры сняты с этого файла (python-docx + OOXML).
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, Twips

# --- пути эталона ---
_ETALON_NAME = "Инструкция_по_эксплуатации_силовых_КЛ_0.4-10кВ_31.07.2026.docx"
ETALON_LOCAL = str(Path(__file__).resolve().parents[1] / "etalons" / _ETALON_NAME)
ETALON_NETWORK = (
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В"
    r"\ИНСТРУКЦИЯ по эксплуатации силовых КЛ  0,4-10кВ  31.07.2026--- ПОЛОЖЕНИЕ.docx"
)

# --- страница ---
PAGE_WIDTH_MM = 210
PAGE_HEIGHT_MM = 297
# титул / основной портрет (секции 0 и 4 эталона)
MARGINS_TITLE_MM = {"left": 30, "right": 15, "top": 20, "bottom": 20}
# тело текста в эталоне (секции 1–2): верх/низ 15
MARGINS_BODY_MM = {"left": 30, "right": 15, "top": 15, "bottom": 15}
# по умолчанию агент ставит титульные поля (Инструкция + секция 0 эталона)
MARGINS_DEFAULT_MM = dict(MARGINS_TITLE_MM)

# --- шрифт ---
FONT_NAME = "Times New Roman"
FONT_SIZE_PT = 14
FONT_SIZE_HALF_POINTS = 28  # w:sz = 28 → 14 пт
TABLE_FONT_PT = 12
TITLE_NUMBER_LABEL_PT = 11  # «номер инструкции» в эталоне

# --- абзац тела (стиль Body Text: w:ind w:firstLine="709") ---
FIRST_INDENT_TWIPS = 709  # 709 twips ≈ 1,25 см
FIRST_INDENT_CM = 1.25
BODY_ALIGN = WD_ALIGN_PARAGRAPH.JUSTIFY

# --- Normal: spacing after=0, line=240, lineRule=auto (одинарный) ---
NORMAL_SPACE_AFTER_PT = 0
NORMAL_SPACE_BEFORE_PT = 0
NORMAL_LINE_TWIPS = 240  # одинарный

# --- Heading 1 (главы): center, bold, caps, keepNext, outlineLvl=0 ---
CHAPTER_ALIGN = WD_ALIGN_PARAGRAPH.CENTER
CHAPTER_BOLD = True
CHAPTER_ALL_CAPS = True
CHAPTER_KEEP_WITH_NEXT = True
CHAPTER_OUTLINE_LEVEL = 0
CHAPTER_SPACE_BEFORE_PT = 0
CHAPTER_SPACE_AFTER_PT = 0
CHAPTER_FIRST_INDENT_CM = 0

# --- подписанты ---
SIGN_INDENT_CM = 0
SIGN_FIO_TAB_CM = 12.0
SIGN_TITLE_MAX_CHARS = 34

# --- таблицы ---
TABLE_FIRST_INDENT_CM = 0
TABLE_SPACE_BEFORE_PT = 0
TABLE_SPACE_AFTER_PT = 0


def resolve_etalon_path() -> str:
    if Path(ETALON_LOCAL).is_file():
        return ETALON_LOCAL
    if Path(ETALON_NETWORK).is_file():
        return ETALON_NETWORK
    return ETALON_LOCAL


def _set_style_spacing_single(style) -> None:
    """Normal/абзац: одинарный интервал, before/after = 0 (как в эталоне)."""
    pf = style.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pf.line_spacing = 1.0
    pf.space_before = Pt(NORMAL_SPACE_BEFORE_PT)
    pf.space_after = Pt(NORMAL_SPACE_AFTER_PT)
    pPr = style.element.get_or_add_pPr()
    spacing = pPr.find(qn("w:spacing"))
    if spacing is None:
        spacing = OxmlElement("w:spacing")
        pPr.append(spacing)
    spacing.set(qn("w:after"), "0")
    spacing.set(qn("w:before"), "0")
    spacing.set(qn("w:line"), str(NORMAL_LINE_TWIPS))
    spacing.set(qn("w:lineRule"), "auto")


def ensure_etalon_styles(doc: Document) -> dict:
    """
    Привести стили Normal / Heading 1 / Body Text к параметрам эталона КЛ.
    """
    report: dict = {"normal": False, "heading1": False, "body": False}
    try:
        normal = doc.styles["Normal"]
        normal.font.name = FONT_NAME
        normal.font.size = Pt(FONT_SIZE_PT)
        _set_style_spacing_single(normal)
        rPr = normal.element.get_or_add_rPr()
        rFonts = rPr.find(qn("w:rFonts"))
        if rFonts is None:
            rFonts = OxmlElement("w:rFonts")
            rPr.insert(0, rFonts)
        for attr in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
            rFonts.set(qn(attr), FONT_NAME)
        sz = rPr.find(qn("w:sz"))
        if sz is None:
            sz = OxmlElement("w:sz")
            rPr.append(sz)
        sz.set(qn("w:val"), str(FONT_SIZE_HALF_POINTS))
        report["normal"] = True
    except Exception as e:
        report["normal_error"] = str(e)

    try:
        h1 = doc.styles["Heading 1"]
        pf = h1.paragraph_format
        pf.alignment = CHAPTER_ALIGN
        pf.first_line_indent = Cm(CHAPTER_FIRST_INDENT_CM)
        pf.space_before = Pt(CHAPTER_SPACE_BEFORE_PT)
        pf.space_after = Pt(CHAPTER_SPACE_AFTER_PT)
        h1.font.bold = True
        h1.font.all_caps = True
        h1.font.size = Pt(FONT_SIZE_PT)
        h1.font.name = FONT_NAME
        pPr = h1.element.get_or_add_pPr()
        if pPr.find(qn("w:keepNext")) is None:
            pPr.append(OxmlElement("w:keepNext"))
        jc = pPr.find(qn("w:jc"))
        if jc is None:
            jc = OxmlElement("w:jc")
            pPr.append(jc)
        jc.set(qn("w:val"), "center")
        outline = pPr.find(qn("w:outlineLvl"))
        if outline is None:
            outline = OxmlElement("w:outlineLvl")
            pPr.append(outline)
        outline.set(qn("w:val"), str(CHAPTER_OUTLINE_LEVEL))
        rPr = h1.element.get_or_add_rPr()
        if rPr.find(qn("w:b")) is None:
            rPr.append(OxmlElement("w:b"))
        if rPr.find(qn("w:caps")) is None:
            rPr.append(OxmlElement("w:caps"))
        report["heading1"] = True
    except Exception as e:
        report["heading1_error"] = str(e)

    try:
        body = doc.styles["Body Text"]
        bpf = body.paragraph_format
        bpf.alignment = BODY_ALIGN
        bpf.first_line_indent = Twips(FIRST_INDENT_TWIPS)
        body.font.name = FONT_NAME
        body.font.size = Pt(FONT_SIZE_PT)
        pPr = body.element.get_or_add_pPr()
        ind = pPr.find(qn("w:ind"))
        if ind is None:
            ind = OxmlElement("w:ind")
            pPr.append(ind)
        ind.set(qn("w:firstLine"), str(FIRST_INDENT_TWIPS))
        jc = pPr.find(qn("w:jc"))
        if jc is None:
            jc = OxmlElement("w:jc")
            pPr.append(jc)
        jc.set(qn("w:val"), "both")
        report["body"] = True
    except Exception:
        report["body"] = False

    return report


def apply_chapter_heading_format(paragraph, doc: Document | None = None) -> None:
    """Формат главы как Heading 1 эталона: центр, жирный, капслок, keepNext, отступ 0."""
    pf = paragraph.paragraph_format
    paragraph.alignment = CHAPTER_ALIGN
    pf.first_line_indent = Cm(0)
    pf.left_indent = Cm(0)
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)
    pf.line_spacing_rule = WD_LINE_SPACING.SINGLE
    pPr = paragraph._element.get_or_add_pPr()
    if CHAPTER_KEEP_WITH_NEXT and pPr.find(qn("w:keepNext")) is None:
        pPr.append(OxmlElement("w:keepNext"))
    try:
        host = doc or paragraph.part.document
        paragraph.style = host.styles["Heading 1"]
    except Exception:
        pass
    for run in paragraph.runs:
        run.bold = CHAPTER_BOLD
        try:
            run.font.all_caps = CHAPTER_ALL_CAPS
        except Exception:
            pass
        run.font.name = FONT_NAME
        if run.font.size is None:
            run.font.size = Pt(FONT_SIZE_PT)
