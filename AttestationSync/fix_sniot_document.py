# -*- coding: utf-8 -*-
"""
Автоисправление документов СНиОТ (ДИ, РИ, Положения, инструкции…) по sniot-di-documents.mdc.

Источник правил: .cursor/rules/sniot-di-documents.mdc — **важнее** любого docx-образца.
Главный API: process_sniot_document(path), validate_sniot_document(doc).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import zipfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from docx.text.paragraph import Paragraph
from lxml import etree

RULES = """
Правила оформления документов СНиОТ (sniot-di-documents.mdc)
============================================================

Область: ВСЕ документы СНиОТ — ДИ, РИ, Положения, инструкции по эксплуатации и т.п.
Правила агента и этот скрипт — **важнее** любого docx-образца на N:\\.
Каждый пункт → функция(и) в fix_sniot_document.py (DocAgent — п. DocAgent ниже).

--- ТИТУЛ И СТРУКТУРА ---

 1. Приоритет правил над образцом docx на N:\\
    validate_sniot_document — проверка по mdc, не по образцу.

 2. Титул один раз (sdt, стр. 1); тело с первой главы; без дубля УТВЕРЖДАЮ на стр. 2
    remove_duplicate_body_title, _is_title_duplicate_paragraph, _is_body_start,
    body_starts_with_first_chapter, validate_sniot_document (дубль титула).

 3. «ОБЩИЕ ПОЛОЖЕНИЯ» → «1 ОБЩИЕ ПОЛОЖЕНИЯ»
    normalize_first_chapter_heading.

 4. Безопасное remove_duplicate_body_title — НИКОГДА не стирать тело без маркера главы
    remove_duplicate_body_title, apply_sniot_rules_to_file (откат при body_after_clean==0).

--- ШРИФТ И АБЗАЦЫ ---

 5. Шрифт Times New Roman 14 pt везде; при замене текста сохранять rPr
    normalize_document_fonts, apply_run_font, set_paragraph_text, validate_fonts.

 6. Тело (не заголовки глав/разделов, не подписанты): выравнивание **по ширине**
    apply_body_paragraph_format, ensure_paragraph_justified,
    validate_body_paragraph_format, is_paragraph_justified.

 7. Отступ первой строки **1,25 см** (709 twips) на абзацах тела; НЕ на заголовках глав
    apply_body_paragraph_format, ensure_first_line_indent, first_line_indent_cm,
    validate_body_paragraph_format. Заголовки разделов «1.5. …» — без 1,25 см.

 8. Заголовки глав 1–5: **по центру**, жирный, капс («1 ОБЩИЕ ПОЛОЖЕНИЯ» …)
    center_chapter_headers, ensure_paragraph_centered, restore_chapter_headers,
    is_chapter_header, validate_chapter_headers.

 9. Перед заголовком главы — ровно одна пустая строка; после — **не добавлять** (только сжатие хвоста)
    ensure_chapter_header_spacing, remove_extra_empty_lines_in_body,
    needs_signatory_layout_compression, validate_chapter_headers.

 9a. Заголовок главы **никогда** не сирота внизу страницы без текста следом
    prevent_chapter_header_orphan, validate_chapter_header_orphan.
    page_break_before — на первый абзац текста (или пустую строку перед ним), **не** на заголовок.

--- НУМЕРАЦИЯ ПУНКТОВ (ДИ САТП) ---

10. Нумерация ДИ САТП: **сначала проверка**; если OK — **не трогать**;
    если есть ошибки — **только выборочная правка** проблемных абзацев/блоков
    validate_numbering_blocks (gate), analyze_numbering_block, fix_numbering_selective,
    fix_missing_section_headers, numbering_block_ranges, validate_di_satp_numbering_count.
    Блоки: 1.4.x, 1.5.x (не 1.9.x), 2.1.x/2.2.x, 3.1.x, 5.1.x.

11. Заголовки разделов («1.5. Старший мастер…») — без подномера в списке ниже
    find_section_header_index (regex ^N.N.\\s+[А-Я]), is_section_header, renumber_block.

12. Блокировка сохранения, если нумерация/тело резко пропали
    validate_save_integrity, count_numbered_paragraphs, count_nonempty_body_paragraphs.

--- СТРАНИЦЫ ---

13. Нумерация страниц: со 2-й, верх по центру, titlePg, пустой header3, футеры без номеров
    fix_page_numbering, validate_page_numbering.

14. Переносы: шаг 1 natural (снять keep/page_break); шаг 2 --fix-page-breaks только если подписанты оторваны
    fix_last_pages_and_signatories (mode=natural), fix_last_pages_page_breaks,
    clear_paragraph_page_layout, apply_signatory_page_break, prevent_chapter_header_orphan,
    validate_page_layout_flags, validate_chapter_header_orphan, validate_last_two_pages_layout.
    Запрещён разрыв **только** перед «Разработал:»; запрещён разрыв **только** на заголовке главы.

15. Стратегия переноса (шаг 2, только если подписанты оторваны):
    гл. 5 небольшая → page_break_before на **первый абзац текста** гл. 5 (не на заголовок);
    гл. 5 большая → page_break_before на абзац(ы) в гл. 5 + подписи следом;
    рвётся один длинный абзац → page_break_before на этот абзац.

--- ПОДПИСАНТЫ ---

16. «Согласовано:» с двоеточием, **не жирным**
    fix_soglasovano, validate_signatory_block.

17. Ровно одна пустая строка **перед** «Разработал:» и **перед** «Согласовано:»
    ensure_single_empty_line_before, validate_signatory_block.

18. **Без** пустой строки **после** «Разработал:» и **после** «Согласовано:»
    remove_empty_paragraphs_after_marker, fix_signatory_block_format,
    validate_signatory_block.

19. Межстрочный интервал **1,5** только на блоке подписантов (от «Разработал:» до последней подписи)
    apply_signatory_line_spacing, set_one_point_five_line_spacing, paragraph_has_one_point_five_spacing,
    fix_signatory_block_format, validate_signatory_line_spacing, validate_signatory_block.
    Тело документа и заголовки глав — **не** 1,5; после align_spacing_to_etalon интервал подписантов восстанавливается.

20. Подписанты всегда связаны с текстом — не сироты на отдельной странице без текста
    fix_last_pages_and_signatories, fix_last_pages_page_breaks, signatories_appear_orphaned,
    validate_last_two_pages_layout, validate_page_layout_flags.

20a. Конец документа — шаг 1 (всегда): естественная вёрстка предпоследней/последней страниц
    fix_last_pages_and_signatories(mode=natural): снять разрывы/keep, оформить подписантов 1,5,
    пустые строки перед «Разработал:»/«Согласовано:», без пустых после них.

20b. Конец документа — шаг 2 (только если оторваны): fix_last_pages_page_breaks / --fix-page-breaks
    Перенос абзаца(ов) или гл. 5 целиком; **никогда** разрыв только перед «Разработал:».

--- DOCAGENT / ПУТЬ ---

21. Консервативный режим ДИ САТП «Старший мастер»: без вредных text_edits / finalize
    is_senior_master_di_path, is_conservative_di_satp (DocAgent: sniot_document.py, agent_core).

22. Кнопка «Оформить документ» — финальный проход fix_sniot_document с always_apply
    apply_sniot_rules_to_file (--always-apply), agent_core.apply_sniot_rules_to_output.

23. Путь из поля «1. Документ» / handoff, не из чата Cursor
    resolve_from_handoff, resolve_target, DOCAGENT_HANDOFF.

24. Латинская «i» в «СНiОТ» в путях → кириллическая «и»
    normalize_sniot_path_text, resolve_target (alt path).

--- ПОРЯДОК ОБРАБОТКИ ---

25. process_sniot_document:
    title (remove_duplicate_body_title до Document) → numbering → font → chapters (center)
    → body (justify + 1,25 см) → spacing → fix_last_pages_and_signatories (шаг 1)
    → fix_last_pages_page_breaks при --fix-page-breaks или signatories_appear_orphaned (шаг 2)
    → fix_page_numbering после save.

--- ПРАВИЛА РУССКОГО ЯЗЫКА (канцелярские фразы) ---

26. Запрещённые фразы → правильные (подстрока в тексте абзаца, нумерация не ломается)
    apply_russian_phrase_rules, formatters/russian_phrase_rules.py (DocAgent).
    Пример: «Выполняет локальные правовые акты» → «Выполняет требования локальных правовых актов».

Коды выхода: 0 OK, 1 ошибки валидации, 2 файл занят Word, 3 файл не найден.
"""

EMPTY_FIRST_HEADER = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p/>
</w:hdr>"""

CENTERED_PAGE_HEADER = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p>
    <w:pPr><w:jc w:val="center"/></w:pPr>
    <w:r><w:fldChar w:fldCharType="begin"/></w:r>
    <w:r><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>
    <w:r><w:fldChar w:fldCharType="separate"/></w:r>
    <w:r><w:t>2</w:t></w:r>
    <w:r><w:fldChar w:fldCharType="end"/></w:r>
  </w:p>
</w:hdr>"""

EMPTY_FOOTER = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:p/>
</w:ftr>"""

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}
FONT_NAME = "Times New Roman"
FONT_SIZE = Pt(14)
FONT_SIZE_EMU = int(FONT_SIZE)
NUM_PREFIX = re.compile(r"^\d+(?:\.\d+)+\.\s*")
SECTION_HEADER = re.compile(r"^\d+\.\d+\.\s+[А-ЯЁA-Z]")
CHAPTER_HEADER = re.compile(r"^\d+\s+[А-ЯЁA-Z][А-ЯЁA-Z\s\-]+$")
FIRST_LINE_INDENT_CM = 1.25
FIRST_LINE_INDENT_TWIPS = 709
FIRST_LINE_INDENT_TOLERANCE_CM = 0.08
SIGNATORY_LINE_SPACING = 1.5
SIGNATORY_LINE_SPACING_TOLERANCE = 0.05
TITLE_DUPLICATE_MARKERS = ("УТВЕРЖД", "ДОЛЖНОСТН", "номер инструкции", "Минсккоммунтеплосеть")

DEFAULT_TARGET_NAME = "ПРОЕКТ Старший мастер_оформлен.docx"
DEFAULT_TARGET_PLUS_NAME = "ПРОЕКТ Старший мастер_оформлен+.docx"
USER_AGENT_DIR = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В\Агент"
)
READONLY_SAMPLE_DIRS: tuple[Path, ...] = (
    Path(
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ"
    ),
)
WORK_DIR = Path(r"C:\Users\v.dubovik\AttestationSync")
DOCAGENT_HANDOFF = Path(r"C:\Users\v.dubovik\DocAgent\handoff\request_latest.json")
DOCAGENT_FORMATTERS = Path(r"C:\Users\v.dubovik\DocAgent\formatters")
WORK_FILE = WORK_DIR / "_work_sniot_document.docx"
OUT_FILE = WORK_DIR / "_work_sniot_document_fixed.docx"
SENIOR_MASTER_DUMP = WORK_DIR / "_work_senior_master_fixed.txt"
MIN_NUMBERED_SENIOR_MASTER = 80

CHAPTER_TITLES: tuple[tuple[int, str], ...] = (
    (1, "ОБЩИЕ ПОЛОЖЕНИЯ"),
    (2, "ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ"),
    (3, "ПРАВА"),
    (4, "ВЗАИМООТНОШЕНИЯ"),
    (5, "ОТВЕТСТВЕННОСТЬ"),
)


def paragraph_text_normalized(paragraph: Paragraph) -> str:
    """Текст абзаца без nbsp/нулевой ширины — для проверки «пустой» строки."""
    return (paragraph.text or "").replace("\xa0", " ").replace("\u200b", "").strip()


def is_paragraph_empty(paragraph: Paragraph) -> bool:
    return not paragraph_text_normalized(paragraph)


def find_body_start_index(doc: Document) -> int:
    for i, paragraph in enumerate(doc.paragraphs):
        text = paragraph_text_normalized(paragraph)
        if text and (is_chapter_header(text) or text.upper().startswith("1 ОБЩИЕ")):
            return i
    return 0


def find_signatory_tail_start(doc: Document) -> int | None:
    """
    Начало блока подписантов: «Разработал:», иначе первая строка подписи перед «Согласовано».
    """
    for i, paragraph in enumerate(doc.paragraphs):
        upper = paragraph_text_normalized(paragraph).upper()
        if upper.startswith("РАЗРАБОТАЛ"):
            return i
    try:
        soglas_idx = find_soglasovano_index(doc)
    except ValueError:
        return None
    idx = soglas_idx - 1
    while idx >= 0 and is_paragraph_empty(doc.paragraphs[idx]):
        idx -= 1
    if idx < 0:
        return soglas_idx
    text = paragraph_text_normalized(doc.paragraphs[idx])
    upper = text.upper()
    if "\t" in doc.paragraphs[idx].text or "начальник" in upper or "инженер" in upper:
        return idx
    return soglas_idx


def get_body_spacing_end_index(doc: Document) -> int:
    """Верхняя граница (не включая) для схлопывания пустых строк в теле."""
    tail = find_signatory_tail_start(doc)
    return tail if tail is not None else len(doc.paragraphs)


@dataclass
class DocumentProfile:
    """Профиль документа — какие правила применять."""

    kind: str  # di | ri | polozhenie | generic
    first_chapter: str | None
    has_signatories: bool
    has_di_satp_numbering: bool
    tail_chapter_idx: int | None


def chapter_header_body(text: str) -> str:
    return re.sub(r"^\d+\.?\s*", "", text.strip()).upper()


def is_chapter_header(text: str) -> bool:
    t = text.strip()
    if not t or re.match(r"^\d+\.\d", t):
        return False
    if CHAPTER_HEADER.match(t):
        return True
    body = chapter_header_body(t)
    return any(
        body == title or (title in body and len(body) <= len(title) + 8)
        for _, title in CHAPTER_TITLES
    )


def is_section_header(text: str) -> bool:
    """Заголовок раздела «1.5. Старший мастер…», не пункт «1.5.1.»."""
    t = text.strip()
    if not t or is_chapter_header(t):
        return False
    return bool(SECTION_HEADER.match(t))


def get_signatory_start_index(doc: Document) -> int | None:
    try:
        return find_razrabotal_index(doc)
    except ValueError:
        return None


def should_apply_body_paragraph_format(text: str, idx: int, doc: Document) -> bool:
    """Абзацы тела: по ширине + отступ 1,25 см; не главы, не подписанты."""
    t = text.strip()
    if not t:
        return False
    if is_chapter_header(t) or is_section_header(t):
        return False
    razrab_idx = get_signatory_start_index(doc)
    if razrab_idx is not None and idx >= razrab_idx:
        return False
    upper = t.upper()
    if upper.startswith(("РАЗРАБОТАЛ", "СОГЛАСОВАН")):
        return False
    return True


def is_paragraph_justified(paragraph: Paragraph) -> bool:
    jc = paragraph_jc(paragraph)
    if jc in ("both", "justify", "distribute"):
        return True
    try:
        return paragraph.alignment == WD_ALIGN_PARAGRAPH.JUSTIFY
    except Exception:
        return False


def ensure_paragraph_justified(paragraph: Paragraph) -> None:
    """Выравнивание по ширине: python-docx + w:jc val=both."""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    p_pr = paragraph._p.get_or_add_pPr()
    jc = p_pr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        p_pr.append(jc)
    jc.set(qn("w:val"), "both")


def ensure_first_line_indent(paragraph: Paragraph, cm: float = FIRST_LINE_INDENT_CM) -> None:
    paragraph.paragraph_format.first_line_indent = Cm(cm)
    p_pr = paragraph._p.get_or_add_pPr()
    ind = p_pr.find(qn("w:ind"))
    if ind is None:
        ind = OxmlElement("w:ind")
        p_pr.append(ind)
    ind.set(qn("w:firstLine"), str(FIRST_LINE_INDENT_TWIPS))
    # hanging/left из стиля списка конфликтуют с отступом первой строки
    for attr in ("w:hanging", "w:left", "w:start"):
        key = qn(attr)
        if ind.get(key) is not None:
            del ind.attrib[key]


def first_line_indent_cm(paragraph: Paragraph) -> float:
    pf = paragraph.paragraph_format
    if pf.first_line_indent is not None:
        return pf.first_line_indent.cm
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is not None:
        ind = p_pr.find(qn("w:ind"))
        if ind is not None:
            raw = ind.get(qn("w:firstLine"))
            if raw:
                try:
                    return int(raw) / 567.0
                except ValueError:
                    pass
    return 0.0


def paragraph_jc(paragraph: Paragraph) -> str | None:
    p_pr = paragraph._p.pPr
    if p_pr is not None and p_pr.jc is not None:
        val = p_pr.jc.val
        if val is None:
            pass
        elif isinstance(val, str):
            return val.lower()
        else:
            name = getattr(val, "name", None)
            if name:
                return str(name).lower()
            return str(val).lower()
    if paragraph.alignment is not None:
        al = paragraph.alignment
        name = getattr(al, "name", None)
        if name:
            return str(name).lower()
    return None


def is_paragraph_centered(paragraph: Paragraph) -> bool:
    jc = paragraph_jc(paragraph)
    if jc == "center":
        return True
    try:
        return paragraph.alignment == WD_ALIGN_PARAGRAPH.CENTER
    except Exception:
        return False


def ensure_paragraph_centered(paragraph: Paragraph) -> None:
    """Выравнивание по центру: python-docx + w:jc в pPr (устойчиво в Word)."""
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p_pr = paragraph._p.get_or_add_pPr()
    jc = p_pr.find(qn("w:jc"))
    if jc is None:
        jc = OxmlElement("w:jc")
        p_pr.append(jc)
    jc.set(qn("w:val"), "center")


def find_chapter_header_indices(doc: Document) -> list[int]:
    return [i for i, p in enumerate(doc.paragraphs) if is_chapter_header(p.text)]


def find_first_nonempty_paragraph_after(doc: Document, idx: int) -> int | None:
    for j in range(idx + 1, len(doc.paragraphs)):
        if not is_paragraph_empty(doc.paragraphs[j]):
            return j
    return None


def paragraph_has_page_break_before(paragraph: Paragraph) -> bool:
    if paragraph.paragraph_format.page_break_before:
        return True
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is not None and p_pr.find(qn("w:pageBreakBefore")) is not None:
        return True
    return False


def page_break_target_after_chapter_header(doc: Document, hdr_idx: int) -> int | None:
    """
    Куда ставить page_break_before после заголовка главы:
    пустая строка перед текстом (если есть) или первый непустой абзац.
    """
    content_idx = find_first_nonempty_paragraph_after(doc, hdr_idx)
    if content_idx is None:
        return None
    if content_idx > hdr_idx + 1:
        return hdr_idx + 1
    return content_idx


def set_page_break_before(paragraph: Paragraph, enabled: bool) -> None:
    paragraph.paragraph_format.page_break_before = enabled
    p_pr = paragraph._p.get_or_add_pPr()
    el = p_pr.find(qn("w:pageBreakBefore"))
    if enabled:
        if el is None:
            el = OxmlElement("w:pageBreakBefore")
            p_pr.append(el)
    elif el is not None:
        p_pr.remove(el)


EXIT_OK = 0
EXIT_VALIDATION_FAIL = 1
EXIT_FILE_LOCKED = 2
EXIT_NOT_FOUND = 3


def strip_all_number_prefixes(text: str) -> str:
    """Снять все ведущие «1.5.1.» (в т.ч. дубли «1.5.1. 1.5.1.»)."""
    t = text.strip()
    while NUM_PREFIX.match(t):
        t = NUM_PREFIX.sub("", t, count=1).strip()
    return t


def strip_number(text: str) -> str:
    return strip_all_number_prefixes(text)


def paragraph_has_manual_number(text: str) -> bool:
    return bool(NUM_PREFIX.match((text or "").strip()))


def collapse_duplicate_manual_prefix(text: str) -> str:
    """«1.5.1. 1.5.1. Текст» → «1.5.1. Текст»."""
    t = (text or "").strip()
    if not t:
        return t
    prefixes: list[str] = []
    rest = t
    while True:
        match = NUM_PREFIX.match(rest)
        if not match:
            break
        prefixes.append(match.group(0).strip())
        rest = rest[match.end() :].strip()
    if len(prefixes) <= 1:
        return t
    return f"{prefixes[0]} {rest}".strip() if rest else prefixes[0]


def has_word_list_numbering(paragraph: Paragraph) -> bool:
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is None:
        return False
    return p_pr.find(qn("w:numPr")) is not None


def remove_word_list_numbering(paragraph: Paragraph) -> bool:
    """Убрать w:numPr — иначе Word показывает номер списка + номер в тексте."""
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is None:
        return False
    num_pr = p_pr.find(qn("w:numPr"))
    if num_pr is None:
        return False
    p_pr.remove(num_pr)
    return True


def deduplicate_manual_and_list_numbering(doc: Document) -> int:
    """
    Двойная нумерация: авто-список Word (numPr) + ручной префикс в тексте.
    Оставляем текст; numPr снимаем. Дубли префикса в тексте схлопываем.
    """
    changed = 0
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        collapsed = collapse_duplicate_manual_prefix(text)
        if collapsed != text:
            set_paragraph_text(paragraph, collapsed)
            text = collapsed
            changed += 1
        if not has_word_list_numbering(paragraph):
            continue
        if paragraph_has_manual_number(text) or is_chapter_header(text) or is_section_header(text):
            if remove_word_list_numbering(paragraph):
                changed += 1
    return changed


def apply_number(text: str, number: str) -> str:
    return f"{number}. {strip_number(text)}"


def is_path_in_user_agent_dir(path: Path | str) -> bool:
    """Путь внутри разрешённой папки Агент (sniot-user-folder-only.mdc)."""
    try:
        resolved = normalize_sniot_path_text(path).resolve()
        base = normalize_sniot_path_text(USER_AGENT_DIR).resolve()
        return resolved.is_relative_to(base)
    except (OSError, ValueError, TypeError):
        return False


def is_path_readonly_sample(path: Path | str) -> bool:
    """Образец в ОБМЕН — только чтение."""
    try:
        resolved = normalize_sniot_path_text(path).resolve()
        for root in READONLY_SAMPLE_DIRS:
            base = normalize_sniot_path_text(root).resolve()
            if resolved.is_relative_to(base):
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def assert_path_writable(path: Path | str) -> Path:
    """Guard: запись только в USER_AGENT_DIR."""
    p = normalize_sniot_path_text(path)
    if not is_path_in_user_agent_dir(p):
        raise PermissionError(
            f"Запись запрещена вне папки Агент: {p}\n"
            f"Разрешено только: {USER_AGENT_DIR}\n"
            "Образцы из ОБМЕН — только для чтения."
        )
    return p.resolve()


def resolve_from_handoff() -> Path | None:
    """Путь из DocAgent handoff/request_latest.json (только папка Агент)."""
    if not DOCAGENT_HANDOFF.is_file():
        return None
    try:
        data = json.loads(DOCAGENT_HANDOFF.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = (data.get("source_path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if path.is_file() and is_path_in_user_agent_dir(path):
        return path
    return None


def find_user_agent_default() -> Path | None:
    """Файл по умолчанию в папке Агент пользователя."""
    preferred = USER_AGENT_DIR / DEFAULT_TARGET_NAME
    if preferred.is_file():
        return preferred
    plus = USER_AGENT_DIR / DEFAULT_TARGET_PLUS_NAME
    if plus.is_file():
        return plus
    if not USER_AGENT_DIR.is_dir():
        return None
    matches = sorted(
        (
            f
            for f in USER_AGENT_DIR.glob("*оформлен*.docx")
            if "_backup_" not in f.name.lower()
        ),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def detect_document_kind(path: Path) -> str:
    name = path.name.lower()
    if "рабоч" in name and "инструкц" in name:
        return "ri"
    if "должностн" in name or name.startswith("ди ") or "ди_" in name:
        return "di"
    if "положен" in name:
        return "polozhenie"
    if "проект" in name and any(w in name for w in ("мастер", "диспетчер", "инженер", "начальник")):
        return "di"
    if "старш" in name and "мастер" in name:
        return "di"
    return "generic"


def find_first_chapter_text(doc: Document) -> str | None:
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if is_chapter_header(text):
            return text
        upper = text.upper()
        if upper.startswith("ОБЩИЕ ПОЛОЖЕНИЯ"):
            return "1 ОБЩИЕ ПОЛОЖЕНИЯ"
    return None


def count_nonempty_body_paragraphs(doc: Document) -> int:
    return sum(1 for paragraph in doc.paragraphs if paragraph.text.strip())


def count_numbered_paragraphs(doc: Document) -> int:
    """Абзацы с текстовой нумерацией вида 1.4.1."""
    return sum(1 for p in doc.paragraphs if NUM_PREFIX.match((p.text or "").strip()))


def is_senior_master_di_path(path: Path | str) -> bool:
    """ДИ «Старший мастер» САТП — консервативный режим без learned text_edits."""
    name = Path(path).name.lower()
    if "мастер" in name and ("проект" in name or "оформлен" in name):
        return True
    low = str(path).lower()
    return "сатп" in low and "мастер" in name


def is_conservative_di_satp(path: Path | str) -> bool:
    """DocAgent: пропускать text_edits/finalize для ДИ «Старший мастер» САТП."""
    return is_senior_master_di_path(path)


def normalize_sniot_path_text(path: Path | str) -> Path:
    """Латинская «i» в «СНiОТ» → кириллическая «и» (СНиОТ)."""
    normalized = str(path)
    for wrong in ("СНiОТ", "СНIОТ", "СNiОТ", "СNiOT"):
        normalized = normalized.replace(wrong, "СНиОТ")
    return Path(normalized)


def find_marker_index(doc: Document, *markers: str, contains: bool = False) -> int:
    """Найти абзац по одному из маркеров (startswith или contains)."""
    last_err: ValueError | None = None
    for marker in markers:
        try:
            return find_paragraph_index(doc, marker, contains=contains)
        except ValueError as exc:
            last_err = exc
    raise ValueError(f"Paragraph not found: {markers!r}") from last_err


def validate_save_integrity(
    *,
    before_nonempty: int,
    before_numbered: int,
    after_doc: Document,
    profile: DocumentProfile,
) -> list[str]:
    """
    Запрет сохранения, если тело или нумерация резко пропали.
    Для ДИ САТП: если была нумерация — нельзя сохранять с нулём пунктов.
    """
    issues: list[str] = []
    after_nonempty = count_nonempty_body_paragraphs(after_doc)
    after_numbered = count_numbered_paragraphs(after_doc)

    if before_nonempty >= 10:
        drop = (before_nonempty - after_nonempty) / before_nonempty
        if drop > 0.10:
            issues.append(
                f"Потеря текста: было {before_nonempty} абзацев, стало {after_nonempty} "
                f"({drop:.0%}) — сохранение отменено"
            )

    if profile.has_di_satp_numbering or before_numbered >= 5:
        if before_numbered >= 5 and after_numbered == 0:
            issues.append(
                f"Нумерация исчезла: было {before_numbered} пунктов, стало 0 — сохранение отменено"
            )
        elif before_numbered >= 10 and after_numbered < before_numbered * 0.5:
            issues.append(
                f"Нумерация сильно уменьшилась: было {before_numbered}, стало {after_numbered}"
            )
    return issues


def parse_debug_dump(dump_path: Path) -> list[tuple[bool, str]]:
    entries: list[tuple[bool, str]] = []
    for line in dump_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*\d+\s+b=(\d+)\s*\|\s*(.*)$", line)
        if match:
            entries.append((bool(int(match.group(1))), match.group(2)))
            continue
        match = re.match(r"^\s*\d+\s+font=\{[^}]*\}\s*\|\s*(.*)$", line)
        if match:
            text = match.group(1)
            bold = "b=1" in line or "Heading" in line
            entries.append((bold, text))
    return entries


def replace_body_from_debug_dump(
    docx_path: Path,
    dump_path: Path,
    *,
    from_marker: str = "1 ОБЩИЕ ПОЛОЖЕНИЯ",
) -> int:
    """
    Заменить тело документа из дампа export_debug (_work_*.txt с нумерацией).
    Титул (sdt/таблицы) не трогаем — только абзацы от первой главы до «Разработал:».
    """
    if not dump_path.is_file():
        raise FileNotFoundError(dump_path)

    body_entries: list[tuple[bool, str]] = []
    started = False
    for bold, text in parse_debug_dump(dump_path):
        t = text.strip()
        if not started:
            if from_marker in text or t.startswith("1 ОБЩИЕ"):
                started = True
            else:
                continue
        if t.lower().startswith("разработал"):
            break
        body_entries.append((bold, t))

    if not body_entries:
        raise ValueError(f"В дампе нет тела от «{from_marker}»: {dump_path}")

    doc = Document(docx_path)
    body_start: int | None = None
    for i, paragraph in enumerate(doc.paragraphs):
        t = paragraph.text.strip()
        if "1 ОБЩИЕ ПОЛОЖЕНИЯ" in t or t.startswith("1 ОБЩИЕ"):
            body_start = i
            break
    if body_start is None:
        for i, paragraph in enumerate(doc.paragraphs):
            if _is_body_start(paragraph.text, "1 ОБЩИЕ ПОЛОЖЕНИЯ"):
                body_start = i
                break
    if body_start is None:
        raise ValueError("Не найдено начало тела (глава 1) в docx")

    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        razrab_idx = len(doc.paragraphs)

    for idx in range(razrab_idx - 1, body_start - 1, -1):
        el = doc.paragraphs[idx]._element
        el.getparent().remove(el)

    try:
        razrab_idx = find_razrabotal_index(doc)
        anchor = doc.paragraphs[razrab_idx]
    except ValueError:
        tail = find_signatory_tail_start(doc)
        anchor = doc.paragraphs[tail] if tail is not None else doc.paragraphs[-1]

    for bold, text in reversed(body_entries):
        new_p = insert_empty_paragraph_before(anchor)
        set_paragraph_text(new_p, text, bold=bold)
        anchor = new_p

    doc.save(docx_path)
    return len(body_entries)


def _is_body_start(text: str, first_chapter: str | None) -> bool:
    t = text.strip()
    if not t:
        return False
    if first_chapter and (first_chapter in t or t.startswith(first_chapter[: min(20, len(first_chapter))])):
        return True
    if is_chapter_header(t):
        return True
    upper = t.upper()
    if upper.startswith("ОБЩИЕ ПОЛОЖЕНИЯ"):
        return True
    if upper in (
        "ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ",
        "ПРАВА",
        "ВЗАИМООТНОШЕНИЯ",
        "ОТВЕТСТВЕННОСТЬ",
    ):
        return True
    if re.match(r"^\d+\s+[А-ЯЁ]", t):
        return True
    if re.match(r"^\d+\.\d+\.", t):
        return True
    return False


def _is_title_duplicate_paragraph(text: str) -> bool:
    upper = text.upper()
    return any(marker in upper for marker in TITLE_DUPLICATE_MARKERS)


def has_signatory_block(doc: Document) -> bool:
    if find_signatory_tail_start(doc) is not None:
        return True
    try:
        find_soglasovano_index(doc)
        return True
    except ValueError:
        return False


def has_di_satp_numbering_structure(doc: Document) -> bool:
    """ДИ старшего мастера САТП — по номерам или по заголовкам разделов."""
    found = 0
    checks = (
        ("1.4", "в своей деятельности руководствуется"),
        ("1.5", "должен знать"),
        ("2 ФУНКЦИИ", "ФУНКЦИИ И ДОЛЖНОСТНЫЕ"),
    )
    for exact, fuzzy in checks:
        try:
            if exact.startswith("2 "):
                find_paragraph_index(doc, exact)
            else:
                find_section_header_index(doc, exact)
            found += 1
        except ValueError:
            try:
                find_paragraph_index(doc, fuzzy, contains=True)
                found += 1
            except ValueError:
                pass
    return found >= 2


def find_tail_chapter_index(doc: Document) -> int | None:
    try:
        return find_paragraph_index(doc, "5 ОТВЕТСТВЕННОСТЬ")
    except ValueError:
        pass
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        razrab_idx = len(doc.paragraphs)
    chapters = [i for i in find_chapter_header_indices(doc) if i < razrab_idx]
    return chapters[-1] if chapters else None


def detect_profile(doc: Document, path: Path) -> DocumentProfile:
    return DocumentProfile(
        kind=detect_document_kind(path),
        first_chapter=find_first_chapter_text(doc),
        has_signatories=has_signatory_block(doc),
        has_di_satp_numbering=has_di_satp_numbering_structure(doc),
        tail_chapter_idx=find_tail_chapter_index(doc),
    )


def resolve_target(
    explicit: Path | None = None,
    *,
    use_handoff: bool = False,
) -> Path:
    if use_handoff:
        handoff_path = resolve_from_handoff()
        if handoff_path is not None:
            return handoff_path
        raise FileNotFoundError(f"Handoff не найден или файл недоступен: {DOCAGENT_HANDOFF}")

    if explicit is not None:
        path = explicit.expanduser()
        if not path.is_absolute():
            path = USER_AGENT_DIR / path
        if not path.exists():
            alt = normalize_sniot_path_text(path)
            if alt != path and alt.exists():
                path = alt
        if not path.exists():
            raise FileNotFoundError(path)
        if not is_path_in_user_agent_dir(path):
            raise FileNotFoundError(
                f"Путь вне разрешённой папки Агент: {path}\n"
                f"Разрешено только: {USER_AGENT_DIR}"
            )
        return path

    handoff_path = resolve_from_handoff()
    if handoff_path is not None:
        return handoff_path

    default_path = find_user_agent_default()
    if default_path is not None:
        return default_path

    raise FileNotFoundError(
        f"Не найден документ в папке Агент: укажите --target, handoff или положите файл в {USER_AGENT_DIR}"
    )


def remove_duplicate_body_title(docx_bytes: bytes, first_chapter: str | None = None) -> bytes:
    """
    Удалить только повтор шапки/УТВЕРЖДАЮ на стр. 2 после sdt-титула.
    НИКОГДА не удалять всё тело, если маркер первой главы не найден.
    """
    with zipfile.ZipFile(BytesIO(docx_bytes), "r") as zin:
        xml = zin.read("word/document.xml")
        root = etree.fromstring(xml)
        body = root.find("w:body", NS)
        if body is None:
            return docx_bytes

        to_remove = []
        passed_sdt = False
        for child in list(body):
            tag = child.tag.split("}")[-1]
            if tag == "sectPr":
                break
            if tag == "sdt":
                passed_sdt = True
                continue
            if not passed_sdt:
                continue
            text = "".join(child.xpath(".//w:t/text()", namespaces=NS))
            text_stripped = text.strip()
            if not text_stripped:
                continue
            if _is_body_start(text_stripped, first_chapter):
                break
            if _is_title_duplicate_paragraph(text_stripped):
                to_remove.append(child)
            else:
                break

        if not to_remove:
            return docx_bytes

        for child in to_remove:
            body.remove(child)

        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = xml if item.filename == "word/document.xml" else zin.read(item.filename)
                if item.filename == "word/document.xml":
                    data = etree.tostring(
                        root, xml_declaration=True, encoding="UTF-8", standalone="yes"
                    )
                zout.writestr(item, data)
        return out.getvalue()


def fix_page_numbering(docx_bytes: bytes) -> bytes:
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(docx_bytes), "r") as zin:
        root = etree.fromstring(zin.read("word/document.xml"))
        sect = root.find(".//w:sectPr", NS)
        if sect is not None:
            if sect.find("w:titlePg", NS) is None:
                sect.insert(0, etree.Element(f"{{{W_NS}}}titlePg"))
            for pg in sect.findall("w:pgNumType", NS):
                sect.remove(pg)

        doc_xml = etree.tostring(
            root, xml_declaration=True, encoding="UTF-8", standalone="yes"
        )

        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                name = item.filename
                if name == "word/document.xml":
                    data = doc_xml
                elif name.startswith("word/header") and name.endswith(".xml"):
                    data = EMPTY_FIRST_HEADER if name == "word/header3.xml" else CENTERED_PAGE_HEADER
                elif name.startswith("word/footer") and name.endswith(".xml"):
                    data = EMPTY_FOOTER
                else:
                    data = zin.read(name)
                zout.writestr(item, data)
    return out.getvalue()


def _xml_has_page_field(xml_bytes: bytes) -> bool:
    text = xml_bytes.decode("utf-8", errors="ignore")
    return " PAGE " in text or ">PAGE<" in text or "instrText" in text and "PAGE" in text


def validate_page_numbering(docx_bytes: bytes) -> list[str]:
    issues: list[str] = []
    try:
        with zipfile.ZipFile(BytesIO(docx_bytes), "r") as zin:
            root = etree.fromstring(zin.read("word/document.xml"))
            sect = root.find(".//w:sectPr", NS)
            if sect is None or sect.find("w:titlePg", NS) is None:
                issues.append("Нет titlePg — номер страницы может появиться на титуле")

            header_names = sorted(
                n for n in zin.namelist() if n.startswith("word/header") and n.endswith(".xml")
            )
            footer_names = sorted(
                n for n in zin.namelist() if n.startswith("word/footer") and n.endswith(".xml")
            )

            body_headers = [n for n in header_names if n != "word/header3.xml"]
            if not body_headers:
                issues.append("Нет колонтитула для номера страницы (header1/header2)")

            for name in header_names:
                data = zin.read(name)
                if name == "word/header3.xml":
                    if _xml_has_page_field(data):
                        issues.append("header3 (титул) не должен содержать номер страницы")
                else:
                    if not _xml_has_page_field(data):
                        issues.append(f"{name}: нет поля PAGE")
                    xml_text = data.decode("utf-8", errors="ignore")
                    if 'w:val="center"' not in xml_text and "<w:jc" not in xml_text:
                        issues.append(f"{name}: номер страницы не по центру")

            for name in footer_names:
                if _xml_has_page_field(zin.read(name)):
                    issues.append(f"{name}: номер страницы в футере запрещён")
    except (zipfile.BadZipFile, OSError, etree.XMLSyntaxError) as exc:
        issues.append(f"Не удалось проверить нумерацию страниц: {exc}")
    return issues


def clear_paragraph_page_layout(paragraph: Paragraph) -> None:
    paragraph.paragraph_format.keep_with_next = False
    paragraph.paragraph_format.keep_together = False
    paragraph.paragraph_format.page_break_before = False
    p_pr = paragraph._p.find(qn("w:pPr"))
    if p_pr is not None:
        for tag in ("w:keepNext", "w:keepLines", "w:pageBreakBefore"):
            el = p_pr.find(qn(tag))
            if el is not None:
                p_pr.remove(el)
    for run in paragraph.runs:
        for br in run._element.findall(qn("w:br")):
            if br.get(qn("w:type")) == "page":
                run._element.remove(br)


def get_run_properties(run) -> object | None:
    r_pr = run._r.find(qn("w:rPr"))
    return deepcopy(r_pr) if r_pr is not None else None


def apply_run_font(run) -> None:
    run.font.name = FONT_NAME
    run.font.size = FONT_SIZE
    run.bold = False


def set_paragraph_text(paragraph: Paragraph, text: str, *, bold: bool | None = False) -> None:
    saved_r_pr = get_run_properties(paragraph.runs[0]) if paragraph.runs else None
    for run in list(paragraph.runs):
        run._element.getparent().remove(run._element)
    run = paragraph.add_run(text)
    if saved_r_pr is not None:
        old = run._r.find(qn("w:rPr"))
        if old is not None:
            run._r.remove(old)
        run._r.insert(0, saved_r_pr)
    apply_run_font(run)
    if bold is True:
        run.bold = True
    elif bold is False:
        run.bold = False


def insert_empty_paragraph_before(paragraph: Paragraph) -> Paragraph:
    new_p = OxmlElement("w:p")
    paragraph._p.addprevious(new_p)
    return Paragraph(new_p, paragraph._parent)


def normalize_document_fonts(doc: Document) -> None:
    for paragraph in doc.paragraphs:
        if not paragraph.text.strip():
            continue
        for run in paragraph.runs:
            if run.text:
                apply_run_font(run)


def find_paragraph_index(doc: Document, startswith: str, *, contains: bool = False) -> int:
    for i, paragraph in enumerate(doc.paragraphs):
        text = paragraph.text.strip()
        if (contains and startswith in text) or text.startswith(startswith):
            return i
    raise ValueError(f"Paragraph not found: {startswith!r}")


def find_section_header_index(
    doc: Document,
    section: str,
    *fuzzy_contains: str,
) -> int:
    """
    Заголовок раздела «1.5. Старший мастер…», не пункт «1.5.1.».
    section = «1.4», «1.5», «2.1» …
    """
    pat = re.compile(rf"^{re.escape(section)}\.\s+[А-ЯЁA-Z]")
    for i, paragraph in enumerate(doc.paragraphs):
        if pat.match(paragraph.text.strip()):
            return i
    for fuzzy in fuzzy_contains:
        try:
            return find_paragraph_index(doc, fuzzy, contains=True)
        except ValueError:
            continue
    raise ValueError(f"Section header not found: {section!r}")


def find_razrabotal_index(doc: Document) -> int:
    try:
        return find_paragraph_index(doc, "Разработал:")
    except ValueError:
        pass
    for i, paragraph in enumerate(doc.paragraphs):
        upper = paragraph_text_normalized(paragraph).upper()
        if upper.startswith("РАЗРАБОТАЛ"):
            return i
    tail = find_signatory_tail_start(doc)
    if tail is not None:
        text = paragraph_text_normalized(doc.paragraphs[tail])
        if text.upper().startswith("РАЗРАБОТАЛ"):
            return tail
        if "\t" in doc.paragraphs[tail].text:
            return tail
    raise ValueError("Paragraph not found: 'Разработал:'")


def find_soglasovano_index(doc: Document) -> int:
    try:
        return find_paragraph_index(doc, "Согласовано:")
    except ValueError:
        for i, paragraph in enumerate(doc.paragraphs):
            if paragraph.text.strip().upper().startswith("СОГЛАСОВАН"):
                return i
    raise ValueError("Paragraph not found: 'Согласовано:'")


def body_starts_with_first_chapter(doc: Document, first_chapter: str | None) -> bool:
    if not first_chapter:
        return True
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if text.startswith(first_chapter) or first_chapter in text:
            return True
        if is_chapter_header(text) and text.startswith(first_chapter.split()[0]):
            return True
        upper = text.upper()
        if upper.startswith("ОБЩИЕ ПОЛОЖЕНИЯ") and "ОБЩИЕ" in first_chapter.upper():
            return True
        return False
    return False


def normalize_first_chapter_heading(doc: Document) -> None:
    """«ОБЩИЕ ПОЛОЖЕНИЯ» → «1 ОБЩИЕ ПОЛОЖЕНИЯ» в первом абзаце тела."""
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        upper = text.upper()
        if upper.startswith("ОБЩИЕ ПОЛОЖЕНИЯ") and not re.match(r"^\d+\s+", text):
            set_paragraph_text(paragraph, "1 " + text)
        break


def count_empty_lines_before(doc: Document, idx: int) -> int:
    count = 0
    pos = idx - 1
    while pos >= 0 and is_paragraph_empty(doc.paragraphs[pos]):
        count += 1
        pos -= 1
    return count


def count_empty_lines_between(doc: Document, start_idx: int, end_idx: int) -> int:
    """Пустые абзацы строго между start_idx и end_idx (не включая границы)."""
    if end_idx <= start_idx + 1:
        return 0
    return sum(
        1
        for i in range(start_idx + 1, end_idx)
        if is_paragraph_empty(doc.paragraphs[i])
    )


def find_etalon_path(target: Path) -> Path | None:
    """
    Авто-поиск *_образец.docx в той же папке, что и целевой файл.
    Пример: ПРОЕЕКТ …_оформлен.docx → ПРОЕКТ …_образец.docx
    """
    if not target.parent.is_dir():
        return None
    stem = target.stem
    for suffix in ("_оформлен+", "_оформлен"):
        if stem.endswith(suffix):
            candidate = target.parent / f"{stem[: -len(suffix)]}_образец.docx"
            if candidate.is_file():
                return candidate
    base = re.sub(r"_оформлен\+?$", "", stem, flags=re.IGNORECASE)
    for candidate in sorted(target.parent.glob("*_образец.docx")):
        if candidate.stem.replace("_образец", "") == base:
            return candidate
    return None


def paragraph_match_key(text: str) -> str:
    """Нормализованный ключ для сопоставления абзацев с образцом."""
    t = (text or "").replace("\xa0", " ").replace("\u200b", "").strip()
    if not t:
        return ""
    if is_chapter_header(t):
        t = chapter_header_body(t)
    else:
        t = NUM_PREFIX.sub("", t).strip()
    return re.sub(r"\s+", " ", t).lower()


def paragraph_keys_match(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    if a == b:
        return True
    if len(a) < 12 or len(b) < 12:
        return a == b
    if a[:48] == b[:48]:
        return True
    return a in b or b in a


def _nonempty_paragraph_sequence(doc: Document) -> list[tuple[int, str]]:
    return [
        (i, paragraph_match_key(p.text))
        for i, p in enumerate(doc.paragraphs)
        if not is_paragraph_empty(p)
    ]


def copy_paragraph_format_from_etalon(
    etalon_paragraph: Paragraph,
    target_paragraph: Paragraph,
    *,
    profile: DocumentProfile,
    target_idx: int,
    doc: Document,
) -> None:
    """Скопировать выравнивание/отступ/интервал с образца, где структура совпадает."""
    text = paragraph_text_normalized(target_paragraph)
    if not text:
        return
    if is_chapter_header(text):
        if is_paragraph_centered(etalon_paragraph):
            ensure_paragraph_centered(target_paragraph)
        return
    upper = text.upper()
    if upper.startswith(("РАЗРАБОТАЛ", "СОГЛАСОВАН")) or (
        profile.has_signatories
        and target_idx >= (get_signatory_start_index(doc) or len(doc.paragraphs))
    ):
        epf = etalon_paragraph.paragraph_format
        tpf = target_paragraph.paragraph_format
        tpf.line_spacing_rule = epf.line_spacing_rule
        tpf.line_spacing = epf.line_spacing
        tpf.space_before = epf.space_before
        tpf.space_after = epf.space_after
        return
    if should_apply_body_paragraph_format(text, target_idx, doc):
        if is_paragraph_justified(etalon_paragraph):
            ensure_paragraph_justified(target_paragraph)
        # Правило mdc: 1,25 см — образец может иметь numPr без firstLine в pPr
        ensure_first_line_indent(target_paragraph)


def align_spacing_to_etalon(
    doc: Document,
    etalon_doc: Document,
    profile: DocumentProfile,
) -> int:
    """
    Выровнять пустые строки и формат абзацев по образцу в той же папке.
    Вставляет недостающие и удаляет лишние пустые строки между парами абзацев.
    """
    from difflib import SequenceMatcher

    e_seq = _nonempty_paragraph_sequence(etalon_doc)
    d_seq = _nonempty_paragraph_sequence(doc)
    if len(e_seq) < 3 or len(d_seq) < 3:
        return 0

    e_keys = [k for _, k in e_seq]
    d_keys = [k for _, k in d_seq]
    sm = SequenceMatcher(None, e_keys, d_keys, autojunk=False)
    if sm.ratio() < 0.72:
        return 0

    insertions: list[tuple[int, int]] = []
    removals: list[int] = []

    def _empty_indices_between(start_idx: int, end_idx: int) -> list[int]:
        return [
            i
            for i in range(start_idx + 1, end_idx)
            if is_paragraph_empty(doc.paragraphs[i])
        ]

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "delete":
            continue

        def _pair_range(e_start: int, e_end: int, d_start: int) -> None:
            block = min(e_end - e_start, j2 - j1 if tag == "equal" else j2 - d_start)
            for offset in range(block):
                e_idx, e_key = e_seq[e_start + offset]
                d_idx, d_key = d_seq[d_start + offset]
                if not paragraph_keys_match(e_key, d_key):
                    continue
                copy_paragraph_format_from_etalon(
                    etalon_doc.paragraphs[e_idx],
                    doc.paragraphs[d_idx],
                    profile=profile,
                    target_idx=d_idx,
                    doc=doc,
                )

        def _sync_empty_gaps(e_idx: int, e_next_idx: int, d_idx: int, d_next_idx: int) -> None:
            need = count_empty_lines_between(etalon_doc, e_idx, e_next_idx) - count_empty_lines_between(
                doc, d_idx, d_next_idx
            )
            if need > 0:
                insertions.append((d_next_idx, need))
            elif need < 0:
                empties = _empty_indices_between(d_idx, d_next_idx)
                for idx in empties[: -need]:
                    removals.append(idx)

        if tag == "equal":
            _pair_range(i1, i2, j1)
            for k in range(i1, i2):
                if k + 1 >= i2:
                    break
                e_idx, _ = e_seq[k]
                e_next_idx, _ = e_seq[k + 1]
                d_idx, _ = d_seq[j1 + (k - i1)]
                d_next_idx, _ = d_seq[j1 + (k - i1) + 1]
                _sync_empty_gaps(e_idx, e_next_idx, d_idx, d_next_idx)
        elif tag == "replace":
            block = min(i2 - i1, j2 - j1)
            _pair_range(i1, i1 + block, j1)
            for offset in range(block - 1):
                e_idx, _ = e_seq[i1 + offset]
                e_next_idx, _ = e_seq[i1 + offset + 1]
                d_idx, _ = d_seq[j1 + offset]
                d_next_idx, _ = d_seq[j1 + offset + 1]
                _sync_empty_gaps(e_idx, e_next_idx, d_idx, d_next_idx)

    for idx in sorted(set(removals), reverse=True):
        el = doc.paragraphs[idx]._element
        el.getparent().remove(el)

    inserted = 0
    for d_next_idx, need in sorted(insertions, key=lambda item: item[0], reverse=True):
        for _ in range(need):
            insert_empty_paragraph_before(doc.paragraphs[d_next_idx])
            inserted += 1
    return inserted + len(removals)


def collect_spacing_metrics(doc: Document) -> dict:
    """Метрики интервалов для сравнения с образцом."""
    empty_idxs = [i for i, p in enumerate(doc.paragraphs) if is_paragraph_empty(p)]
    ch_idxs = find_chapter_header_indices(doc)
    empty_after_ch = sum(
        1
        for ci in ch_idxs
        if ci + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[ci + 1])
    )
    razrab_empty = 0
    soglas_empty = 0
    try:
        razrab_idx = find_paragraph_index(doc, "Разработал:")
        razrab_empty = count_empty_lines_before(doc, razrab_idx)
    except ValueError:
        razrab_idx = None
    try:
        sog_idx = find_soglasovano_index(doc)
        soglas_empty = count_empty_lines_before(doc, sog_idx)
    except ValueError:
        sog_idx = None
    return {
        "total_paras": len(doc.paragraphs),
        "empty_count": len(empty_idxs),
        "empty_after_chapter": empty_after_ch,
        "razrab_empty_before": razrab_empty,
        "soglas_empty_before": soglas_empty,
        "razrab_idx": razrab_idx,
        "soglas_idx": sog_idx,
    }


def compare_spacing_to_etalon(doc: Document, etalon_doc: Document) -> dict:
    """Сравнить метрики целевого документа с образцом."""
    target = collect_spacing_metrics(doc)
    etalon = collect_spacing_metrics(etalon_doc)
    delta = {
        key: target.get(key, 0) - etalon.get(key, 0)
        for key in (
            "total_paras",
            "empty_count",
            "empty_after_chapter",
            "razrab_empty_before",
            "soglas_empty_before",
        )
    }
    return {"target": target, "etalon": etalon, "delta": delta}


def ensure_di_satp_section_headers(doc: Document) -> None:
    """Проставить номера заголовков разделов, если они потеряны (1.4., 1.5., 2.1., 2.2.)."""
    headers = (
        ("1.4", "в своей деятельности руководствуется"),
        ("1.5", "должен знать"),
        ("2.1", "выполняет следующие функции"),
        ("2.2", "Для выполнения возложенных на него функций"),
        ("3.1", "имеет право"),
        ("5.1", "несет ответственность"),
    )
    for num, fuzzy in headers:
        try:
            find_section_header_index(doc, num)
        except ValueError:
            try:
                idx = find_paragraph_index(doc, fuzzy, contains=True)
            except ValueError:
                continue
            text = doc.paragraphs[idx].text.strip()
            if not re.match(rf"^{re.escape(num)}\.", text):
                set_paragraph_text(doc.paragraphs[idx], apply_number(text, num))


def numbering_block_ranges(doc: Document) -> list[tuple[int, int, str]]:
    """Диапазоны для проверки/исправления нумерации (start, end, prefix)."""
    ranges: list[tuple[int, int, str]] = []
    try:
        i14 = find_section_header_index(doc, "1.4", "в своей деятельности руководствуется")
        i15 = find_section_header_index(doc, "1.5", "должен знать")
        i2 = find_marker_index(doc, "2 ФУНКЦИИ", "ФУНКЦИИ И ДОЛЖНОСТНЫЕ", contains=True)
        ranges.append((i14 + 1, i15, "1.4"))
        ranges.append((i15 + 1, i2, "1.5"))
        start_funcs = find_section_header_index(doc, "2.1", "выполняет следующие функции")
        start_duties = find_section_header_index(
            doc, "2.2", "Для выполнения возложенных на него функций"
        )
        end_ch2 = find_marker_index(doc, "3 ПРАВА", "3 ПРАВА", contains=True)
        ranges.append((start_funcs + 1, start_duties, "2.1"))
        ranges.append((start_duties + 1, end_ch2, "2.2"))
        ranges.append(
            (
                find_section_header_index(doc, "3.1", "имеет право") + 1,
                find_marker_index(doc, "4 ВЗАИМООТНОШЕНИЯ", "4 ВЗАИМООТНОШЕНИЯ", contains=True),
                "3.1",
            )
        )
        ranges.append(
            (
                find_section_header_index(doc, "5.1", "несет ответственность") + 1,
                find_razrabotal_index(doc),
                "5.1",
            )
        )
    except ValueError:
        pass
    return ranges


def block_list_paragraph_indices(doc: Document, start: int, end: int) -> list[int]:
    """Индексы нумеруемых пунктов внутри блока (без заголовков раздела/главы)."""
    indices: list[int] = []
    for idx in range(start, end):
        text = doc.paragraphs[idx].text.strip()
        if not text:
            continue
        if is_section_header(text) or is_chapter_header(text):
            continue
        indices.append(idx)
    return indices


def parse_numbered_item(text: str) -> tuple[str, int] | None:
    """Из «1.5.3. Текст…» → («1.5», 3)."""
    match = re.match(r"^(\d+(?:\.\d+)+)\.\s*", text.strip())
    if not match:
        return None
    parts = match.group(1).split(".")
    return ".".join(parts[:-1]), int(parts[-1])


def analyze_numbering_block(
    doc: Document,
    start: int,
    end: int,
    prefix: str,
) -> tuple[list[str], list[tuple[int, str]]]:
    """
    Проверка одного блока нумерации.
    Возвращает (сообщения об ошибках, список (idx, ожидаемый_номер) для правки).
    """
    issues: list[str] = []
    fixes: list[tuple[int, str]] = []
    indices = block_list_paragraph_indices(doc, start, end)
    expected_counter = 1
    collected: list[int] = []

    for idx in indices:
        text = doc.paragraphs[idx].text.strip()
        expected_num = f"{prefix}.{expected_counter}"
        parsed = parse_numbered_item(text)

        if parsed is None:
            issues.append(f"Без номера в блоке {prefix}.x: {text[:50]}")
            fixes.append((idx, expected_num))
            expected_counter += 1
            continue

        actual_prefix, actual_sub = parsed
        if actual_prefix != prefix:
            issues.append(
                f"Чужой префикс в блоке {prefix}.x: {actual_prefix}.x → {text[:50]}"
            )
            fixes.append((idx, expected_num))
        elif actual_sub != expected_counter:
            issues.append(
                f"Неверный номер в блоке {prefix}.x: ожидался {expected_counter}, "
                f"есть {actual_sub}: {text[:50]}"
            )
            fixes.append((idx, expected_num))
        else:
            collected.append(actual_sub)
        expected_counter += 1

    if collected and collected != list(range(1, len(collected) + 1)):
        summary = f"Пропуски в нумерации {prefix}.x: {collected}"
        if summary not in issues and not any(prefix in i for i in issues):
            issues.append(summary)

    return issues, fixes


def validate_numbering_blocks(doc: Document, profile: DocumentProfile) -> list[str]:
    """Проверка нумерации по блокам — gate перед любой правкой."""
    if not profile.has_di_satp_numbering:
        return []
    issues: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if re.match(r"^1\.9\.\d", text):
            issues.append(f"Нумерация 1.9.x вместо 1.5.x: {text[:60]}")
            break

    for start, end, prefix in numbering_block_ranges(doc):
        block_issues, _ = analyze_numbering_block(doc, start, end, prefix)
        issues.extend(block_issues)
    return issues


def fix_missing_section_headers(doc: Document) -> int:
    """Проставить номер заголовка раздела только если он потерян."""
    headers = (
        ("1.4", "в своей деятельности руководствуется"),
        ("1.5", "должен знать"),
        ("2.1", "выполняет следующие функции"),
        ("2.2", "Для выполнения возложенных на него функций"),
        ("3.1", "имеет право"),
        ("5.1", "несет ответственность"),
    )
    changed = 0
    for num, fuzzy in headers:
        try:
            find_section_header_index(doc, num)
        except ValueError:
            try:
                idx = find_paragraph_index(doc, fuzzy, contains=True)
            except ValueError:
                continue
            text = doc.paragraphs[idx].text.strip()
            if not re.match(rf"^{re.escape(num)}\.", text):
                set_paragraph_text(doc.paragraphs[idx], apply_number(text, num))
                changed += 1
    return changed


def fix_numbering_selective(doc: Document, profile: DocumentProfile) -> int:
    """
    Выборочная правка нумерации: только абзацы с ошибками в проблемных блоках.
    Если validate_numbering_blocks пуст — не вызывать (process_sniot_document).
    """
    if not profile.has_di_satp_numbering:
        return 0

    changed = fix_missing_section_headers(doc)
    fix_map: dict[int, str] = {}

    for start, end, prefix in numbering_block_ranges(doc):
        _, fixes = analyze_numbering_block(doc, start, end, prefix)
        for idx, expected_num in fixes:
            fix_map[idx] = expected_num

    for idx, expected_num in sorted(fix_map.items()):
        paragraph = doc.paragraphs[idx]
        old_text = paragraph.text.strip()
        new_text = apply_number(old_text, expected_num)
        if old_text != new_text.strip():
            set_paragraph_text(paragraph, new_text)
            changed += 1

    try:
        start_duties = find_section_header_index(
            doc, "2.2", "Для выполнения возложенных на него функций"
        )
        duties_text = doc.paragraphs[start_duties].text.strip()
        canonical = apply_number(duties_text, "2.2")
        if duties_text != canonical.strip() and not re.match(r"^2\.2\.\s", duties_text):
            set_paragraph_text(doc.paragraphs[start_duties], canonical)
            changed += 1
    except ValueError:
        pass

    return changed


def fix_numbering(doc: Document, profile: DocumentProfile) -> int:
    """Обратная совместимость — только выборочная правка по результатам проверки."""
    if validate_numbering_blocks(doc, profile):
        return fix_numbering_selective(doc, profile)
    return 0


def validate_di_satp_numbering_count(
    doc: Document, profile: DocumentProfile, path: Path | None
) -> list[str]:
    if not profile.has_di_satp_numbering or path is None:
        return []
    if not is_senior_master_di_path(path):
        return []
    numbered = count_numbered_paragraphs(doc)
    if numbered < MIN_NUMBERED_SENIOR_MASTER:
        return [f"Мало нумерованных пунктов: {numbered} (ожидается >={MIN_NUMBERED_SENIOR_MASTER})"]
    return []


def validate_duplicate_list_numbering(doc: Document) -> list[str]:
    """numPr + ручной префикс в тексте → двойная нумерация в Word."""
    issues: list[str] = []
    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        if collapse_duplicate_manual_prefix(text) != text:
            issues.append(f"Дубль номера в тексте: {text[:60]}")
            continue
        if has_word_list_numbering(paragraph) and (
            paragraph_has_manual_number(text) or is_chapter_header(text) or is_section_header(text)
        ):
            issues.append(f"Двойная нумерация (список Word + текст): {text[:60]}")
    return issues


def validate_fonts(doc: Document) -> list[str]:
    issues: list[str] = []
    for paragraph in doc.paragraphs:
        if not paragraph.text.strip():
            continue
        for run in paragraph.runs:
            if not run.text.strip():
                continue
            if run.font.name and run.font.name != FONT_NAME:
                issues.append(f"Шрифт не {FONT_NAME}: {run.font.name!r} в «{run.text[:30]}…»")
                return issues
            if run.font.size and int(run.font.size) != FONT_SIZE_EMU:
                issues.append(f"Размер не 14 pt в «{run.text[:30]}…»")
                return issues
    return issues


def validate_chapter_headers(doc: Document) -> list[str]:
    issues: list[str] = []
    for idx in find_chapter_header_indices(doc):
        text = doc.paragraphs[idx].text.strip()
        if idx > 0:
            empty_before = count_empty_lines_before(doc, idx)
            if empty_before == 0:
                issues.append(f"Нет пустой строки перед главой: {text[:40]}")
            elif empty_before > 1:
                issues.append(f"Больше одной пустой строки перед главой: {text[:40]}")
        jc = paragraph_jc(doc.paragraphs[idx])
        if not is_paragraph_centered(doc.paragraphs[idx]):
            issues.append(f"Заголовок главы не по центру: {text[:40]}")
    return issues


def validate_chapter_header_orphan(doc: Document) -> list[str]:
    """Заголовок главы не отрывается от текста; page_break_before не на заголовке."""
    issues: list[str] = []
    for hdr_idx in find_chapter_header_indices(doc):
        hdr = doc.paragraphs[hdr_idx]
        text = hdr.text.strip()[:40]
        if paragraph_has_page_break_before(hdr):
            issues.append(f"Разрыв страницы на заголовке главы (сирота): {text}")
        if hdr.paragraph_format.keep_with_next or hdr.paragraph_format.keep_together:
            issues.append(f"keep_with_next/keep_together на заголовке главы: {text}")
        content_idx = find_first_nonempty_paragraph_after(doc, hdr_idx)
        if content_idx is None:
            issues.append(f"Заголовок главы без текста следом: {text}")
            continue
        if content_idx > hdr_idx + 1:
            issues.append(f"Пустая строка между заголовком и текстом главы: {text}")
    return issues


def validate_page_layout_flags(doc: Document, profile: DocumentProfile) -> list[str]:
    if not profile.has_signatories:
        return []
    issues: list[str] = []
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return issues

    if doc.paragraphs[razrab_idx].paragraph_format.page_break_before:
        issues.append("Запрещён разрыв страницы только перед «Разработал:»")

    for idx, paragraph in enumerate(doc.paragraphs):
        pf = paragraph.paragraph_format
        if pf.keep_with_next or pf.keep_together:
            snippet = paragraph.text.strip()[:40] or f"абзац {idx + 1}"
            issues.append(f"keep_with_next/keep_together запрещены: «{snippet}»")
            break
    return issues


def validate_signatory_block(doc: Document, profile: DocumentProfile) -> list[str]:
    if not profile.has_signatories:
        return []
    issues: list[str] = []
    try:
        razrab_idx = find_razrabotal_index(doc)
        soglas_idx = find_soglasovano_index(doc)
    except ValueError as exc:
        issues.append(str(exc))
        return issues

    for p in doc.paragraphs:
        t = p.text.strip()
        if t.upper().startswith("СОГЛАСОВАН") and t != "Согласовано:":
            issues.append(f"«Согласовано» без двоеточия или не то написание: {t!r}")
            break

    if razrab_idx + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[razrab_idx + 1]):
        issues.append("Пустая строка после «Разработал:»")
    if soglas_idx + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[soglas_idx + 1]):
        issues.append("Пустая строка после «Согласовано:»")

    if count_empty_lines_before(doc, razrab_idx) != 1:
        issues.append("Должна быть ровно одна пустая строка перед «Разработал:»")
    if count_empty_lines_before(doc, soglas_idx) != 1:
        issues.append("Должна быть ровно одна пустая строка перед «Согласовано:»")

    issues.extend(validate_signatory_line_spacing(doc, profile))

    for p in doc.paragraphs[soglas_idx : soglas_idx + 1]:
        for run in p.runs:
            if run.text and run.bold:
                issues.append("«Согласовано:» не должно быть жирным")
                break

    return issues


def validate_body_not_empty(doc: Document, profile: DocumentProfile) -> list[str]:
    n = count_nonempty_body_paragraphs(doc)
    if n == 0:
        return ["Тело документа пустое (остался только титул в content control)"]
    if profile.kind == "di" and n < 8:
        return [f"Подозрительно мало текста в теле документа ({n} непустых абзацев)"]
    return []


def recover_body_from_text_dump(docx_path: Path, dump_path: Path) -> int:
    """
    Восстановить абзацы тела из текстового дампа (_full__work_*.txt).
    Титул (sdt) в docx не трогаем — добавляем абзацы через python-docx.
    """
    if not dump_path.is_file():
        raise FileNotFoundError(dump_path)
    entries: list[tuple[bool, str]] = []
    for line in dump_path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^\s*\d+\s+b=(\d+)\s*\|\s*(.*)$", line)
        if not match:
            continue
        entries.append((bool(int(match.group(1))), match.group(2)))

    doc = Document(docx_path)
    added = 0
    for bold, text in entries:
        paragraph = doc.add_paragraph()
        run = paragraph.add_run(text)
        run.font.name = FONT_NAME
        run.font.size = FONT_SIZE
        if bold:
            run.bold = True
        added += 1
    doc.save(docx_path)
    return added


def validate_sniot_document(
    doc: Document,
    *,
    docx_bytes: bytes | None = None,
    profile: DocumentProfile | None = None,
    path: Path | None = None,
) -> list[str]:
    """Проверка всех правил sniot-di-documents.mdc для любого документа СНиОТ."""
    if profile is None:
        profile = detect_profile(doc, path or Path("_unknown_.docx"))
    issues: list[str] = []

    issues.extend(validate_body_not_empty(doc, profile))

    if profile.first_chapter and not body_starts_with_first_chapter(doc, profile.first_chapter):
        head = " ".join(p.text for p in doc.paragraphs[:12])
        if any(k in head for k in TITLE_DUPLICATE_MARKERS):
            issues.append(
                f"Дубль титула: текст не начинается с «{profile.first_chapter[:40]}»"
            )

    issues.extend(validate_numbering_blocks(doc, profile))
    issues.extend(validate_duplicate_list_numbering(doc))
    issues.extend(validate_di_satp_numbering_count(doc, profile, path))
    issues.extend(validate_fonts(doc))
    issues.extend(validate_chapter_headers(doc))
    issues.extend(validate_chapter_header_orphan(doc))
    issues.extend(validate_body_paragraph_format(doc, profile))
    issues.extend(validate_empty_lines_in_body(doc))
    issues.extend(validate_last_two_pages_layout(doc, profile))

    if docx_bytes is not None:
        issues.extend(validate_page_numbering(docx_bytes))

    return issues


def validate_di_document(doc: Document, *, docx_bytes: bytes | None = None) -> list[str]:
    """Обратная совместимость."""
    return validate_sniot_document(doc, docx_bytes=docx_bytes)


def restore_chapter_headers(doc: Document) -> int:
    """Восстановить «2 ФУНКЦИИ…», «3 ПРАВА» и др., если номера/капс потеряны."""
    fixed = 0
    search_from = 0
    for num, title in CHAPTER_TITLES:
        canonical = f"{num} {title}"
        found_idx: int | None = None
        for i in range(search_from, len(doc.paragraphs)):
            text = doc.paragraphs[i].text.strip()
            if not text:
                continue
            if re.match(r"^\d+\.\d", text):
                continue
            body = chapter_header_body(text)
            if body == title or (title in body and len(body) <= len(title) + 8):
                found_idx = i
                break
        if found_idx is None:
            continue
        search_from = found_idx + 1
        if doc.paragraphs[found_idx].text.strip() != canonical:
            set_paragraph_text(doc.paragraphs[found_idx], canonical, bold=True)
            fixed += 1
    return fixed


def center_chapter_headers(doc: Document) -> int:
    """Заголовки глав 1–5 — по центру, жирный, капслок (sniot-di-documents.mdc + эталон)."""
    centered = 0
    for idx in find_chapter_header_indices(doc):
        paragraph = doc.paragraphs[idx]
        canonical = paragraph.text.strip()
        body = chapter_header_body(canonical)
        for num, title in CHAPTER_TITLES:
            if body == title or (title in body and len(body) <= len(title) + 8):
                canonical = f"{num} {title}"
                break
        if paragraph.text.strip() != canonical:
            set_paragraph_text(paragraph, canonical, bold=True)
        ensure_paragraph_centered(paragraph)
        pf = paragraph.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.left_indent = Pt(0)
        pf.space_before = Pt(0)
        pf.space_after = Pt(0)
        for run in paragraph.runs:
            if run.text:
                run.bold = True
        centered += 1
    return centered


def _load_russian_phrase_rules_module():
    """PHRASE_REPLACEMENTS из DocAgent/formatters/russian_phrase_rules.py."""
    phrase_path = DOCAGENT_FORMATTERS / "russian_phrase_rules.py"
    if phrase_path.is_file():
        import importlib.util

        spec = importlib.util.spec_from_file_location("russian_phrase_rules", phrase_path)
        if spec is not None and spec.loader is not None:
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    return None


def apply_russian_phrase_rules(doc: Document, profile: DocumentProfile) -> int:
    """Канцелярские фразы в тексте абзацев тела (не заголовки, не подписанты)."""
    mod = _load_russian_phrase_rules_module()
    if mod is None:
        return 0
    apply_fn = getattr(mod, "apply_phrase_replacements", None)
    if apply_fn is None:
        return 0
    changed = 0
    for idx, paragraph in enumerate(doc.paragraphs):
        if not should_apply_body_paragraph_format(paragraph.text, idx, doc):
            continue
        original = paragraph.text
        new_text, details = apply_fn(original)
        if new_text != original and details:
            set_paragraph_text(paragraph, new_text)
            changed += len(details)
    return changed


def apply_body_paragraph_format(doc: Document, profile: DocumentProfile) -> int:
    """Тело документа: по ширине + отступ первой строки 1,25 см (не главы, не подписанты)."""
    changed = 0
    for idx, paragraph in enumerate(doc.paragraphs):
        if not should_apply_body_paragraph_format(paragraph.text, idx, doc):
            continue
        ensure_paragraph_justified(paragraph)
        ensure_first_line_indent(paragraph)
        changed += 1
    return changed


def validate_body_paragraph_format(doc: Document, profile: DocumentProfile) -> list[str]:
    issues: list[str] = []
    for idx, paragraph in enumerate(doc.paragraphs):
        if not should_apply_body_paragraph_format(paragraph.text, idx, doc):
            continue
        text = paragraph.text.strip()
        snippet = text[:50] + ("…" if len(text) > 50 else "")
        if not is_paragraph_justified(paragraph):
            issues.append(f"Абзац не по ширине: «{snippet}»")
        indent = first_line_indent_cm(paragraph)
        if abs(indent - FIRST_LINE_INDENT_CM) > FIRST_LINE_INDENT_TOLERANCE_CM:
            issues.append(
                f"Отступ первой строки не 1,25 см ({indent:.2f} см): «{snippet}»"
            )
    return issues


def validate_empty_lines_in_body(doc: Document) -> list[str]:
    """В теле документа не должно быть двойных/тройных пустых строк."""
    issues: list[str] = []
    body_start = find_body_start_index(doc)
    body_end = get_body_spacing_end_index(doc)

    empty_run = 0
    for i in range(body_start, body_end):
        if not is_paragraph_empty(doc.paragraphs[i]):
            if empty_run >= 2:
                issues.append(
                    f"Двойные/тройные пустые строки в теле (абз. {i - empty_run + 1}…)"
                )
            empty_run = 0
        else:
            empty_run += 1
    return issues


def _collapse_empty_run(
    doc: Document,
    run_start: int,
    run_len: int,
    *,
    body_start: int,
    body_end: int,
) -> list[int]:
    """Какие индексы пустых абзацев удалить в серии run_len подряд."""
    if run_len <= 0:
        return []
    to_remove: list[int] = []
    prev_nonempty = run_start - 1
    while prev_nonempty >= body_start and is_paragraph_empty(doc.paragraphs[prev_nonempty]):
        prev_nonempty -= 1
    if prev_nonempty >= body_start and is_chapter_header(
        paragraph_text_normalized(doc.paragraphs[prev_nonempty])
    ):
        return list(range(run_start, run_start + run_len))

    needs_one = False
    next_idx = run_start + run_len
    if next_idx < len(doc.paragraphs):
        nxt = paragraph_text_normalized(doc.paragraphs[next_idx])
        if is_chapter_header(nxt) or nxt in ("Разработал:", "Согласовано:"):
            needs_one = True
        elif nxt.upper().startswith("СОГЛАСОВАН") or nxt.upper().startswith("РАЗРАБОТАЛ"):
            needs_one = True

    if needs_one:
        if run_len > 1:
            to_remove.extend(range(run_start + 1, run_start + run_len))
    else:
        to_remove.extend(range(run_start, run_start + run_len))
    return to_remove


def remove_empty_lines_after_chapter_headers(doc: Document) -> int:
    """Убрать все пустые строки сразу после заголовков глав."""
    removed = 0
    for hdr_idx in sorted(find_chapter_header_indices(doc), reverse=True):
        while hdr_idx + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[hdr_idx + 1]):
            el = doc.paragraphs[hdr_idx + 1]._element
            el.getparent().remove(el)
            removed += 1
    return removed


def remove_extra_empty_lines_in_body(doc: Document) -> int:
    """Убрать лишние пустые строки в теле; оставить ровно одну перед главой/подписантами."""
    body_start = find_body_start_index(doc)
    body_end = get_body_spacing_end_index(doc)

    to_remove: list[int] = []
    i = body_start
    while i < body_end:
        if not is_paragraph_empty(doc.paragraphs[i]):
            i += 1
            continue
        run_start = i
        while i < body_end and is_paragraph_empty(doc.paragraphs[i]):
            i += 1
        run_len = i - run_start
        to_remove.extend(
            _collapse_empty_run(
                doc, run_start, run_len, body_start=body_start, body_end=body_end
            )
        )

    for idx in reversed(sorted(set(to_remove))):
        el = doc.paragraphs[idx]._element
        el.getparent().remove(el)

    body_end = get_body_spacing_end_index(doc)
    extra: list[int] = []
    i = body_start
    while i < body_end:
        if not is_paragraph_empty(doc.paragraphs[i]):
            i += 1
            continue
        run_start = i
        while i < body_end and is_paragraph_empty(doc.paragraphs[i]):
            i += 1
        run_len = i - run_start
        extra.extend(
            _collapse_empty_run(
                doc, run_start, run_len, body_start=body_start, body_end=body_end
            )
        )

    for idx in reversed(sorted(set(extra))):
        el = doc.paragraphs[idx]._element
        el.getparent().remove(el)

    removed_after_headers = remove_empty_lines_after_chapter_headers(doc)
    return len(to_remove) + len(extra) + removed_after_headers


def maybe_restore_senior_master_body(docx_path: Path) -> tuple[int, str]:
    """
    Если в ДИ «Старший мастер» пропала нумерация или главы — восстановить тело из дампа.
    Не трогаем документ, если главы на месте (только нумерация без номеров — чинит fix_numbering_selective).
    """
    if not is_senior_master_di_path(docx_path):
        return 0, ""
    doc = Document(docx_path)
    numbered = count_numbered_paragraphs(doc)
    chapters = len(find_chapter_header_indices(doc))
    if numbered >= MIN_NUMBERED_SENIOR_MASTER and chapters >= 5:
        return 0, ""
    if chapters >= 5:
        return 0, ""
    if not SENIOR_MASTER_DUMP.is_file():
        return 0, f"Дамп не найден: {SENIOR_MASTER_DUMP.name}"
    try:
        added = replace_body_from_debug_dump(docx_path, SENIOR_MASTER_DUMP)
    except Exception as exc:
        return 0, f"Восстановление из дампа пропущено: {exc}"
    return added, f"Восстановлено {added} абзацев из {SENIOR_MASTER_DUMP.name}"


def renumber_block(doc: Document, start_idx: int, end_idx: int, prefix: str) -> int:
    """Полная перенумерация блока — только для ручного/legacy вызова."""
    counter = 1
    changed = 0
    for idx in range(start_idx, end_idx):
        text = doc.paragraphs[idx].text.strip()
        if not text or is_section_header(text) or is_chapter_header(text):
            continue
        new_text = apply_number(text, f"{prefix}.{counter}")
        if text != new_text.strip():
            set_paragraph_text(doc.paragraphs[idx], new_text)
            changed += 1
        counter += 1
    return changed


def fix_soglasovano(doc: Document) -> None:
    for paragraph in doc.paragraphs:
        if paragraph.text.strip().upper().startswith("СОГЛАСОВАН"):
            set_paragraph_text(paragraph, "Согласовано:", bold=False)
            return
    raise ValueError("Блок «Согласовано» не найден")


def fix_razrabotal(doc: Document) -> None:
    for paragraph in doc.paragraphs:
        if paragraph.text.strip().upper().startswith("РАЗРАБОТАЛ"):
            set_paragraph_text(paragraph, "Разработал:", bold=False)
            return
    raise ValueError("Блок «Разработал:» не найден")


def count_nonempty_paragraphs(doc: Document, start: int, end: int) -> int:
    return sum(1 for i in range(start, end) if doc.paragraphs[i].text.strip())


def remove_empty_paragraphs_after_index(doc: Document, idx: int) -> None:
    while idx + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[idx + 1]):
        el = doc.paragraphs[idx + 1]._element
        el.getparent().remove(el)


def ensure_single_empty_line_before(doc: Document, marker: str) -> None:
    idx = find_paragraph_index(doc, marker)
    if idx == 0:
        insert_empty_paragraph_before(doc.paragraphs[idx])
        return
    empty_count = count_empty_lines_before(doc, idx)
    if empty_count == 0:
        insert_empty_paragraph_before(doc.paragraphs[idx])
    elif empty_count > 1:
        cur = idx
        for _ in range(empty_count - 1):
            el = doc.paragraphs[cur - 1]._element
            el.getparent().remove(el)
            cur -= 1


def needs_signatory_layout_compression(doc: Document) -> bool:
    try:
        ch4_idx = find_paragraph_index(doc, "4 ВЗАИМООТНОШЕНИЯ")
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return False
    if count_nonempty_paragraphs(doc, ch4_idx, razrab_idx) >= 8:
        return True
    return count_empty_lines_before(doc, razrab_idx) > 1


def ensure_chapter_header_spacing(doc: Document) -> None:
    """Ровно одна пустая строка перед главой; после заголовка пустую не добавляем."""
    markers = [doc.paragraphs[i].text.strip() for i in find_chapter_header_indices(doc)]
    for marker in markers:
        idx = find_paragraph_index(doc, marker)
        if idx != 0:
            ensure_single_empty_line_before(doc, marker)
        remove_empty_paragraphs_after_index(doc, idx)


def prevent_chapter_header_orphan(doc: Document) -> int:
    """
    Заголовок главы не остаётся сиротой: page_break_before переносится на текст главы
    (пустую строку перед текстом, если она есть), не на заголовок.
    keep_with_next на заголовках снимается (минимально, без keep_together на длинных абзацах).
    """
    changed = 0
    for hdr_idx in find_chapter_header_indices(doc):
        hdr = doc.paragraphs[hdr_idx]
        target_idx = page_break_target_after_chapter_header(doc, hdr_idx)

        if hdr.paragraph_format.keep_with_next or hdr.paragraph_format.keep_together:
            hdr.paragraph_format.keep_with_next = False
            hdr.paragraph_format.keep_together = False
            p_pr = hdr._p.find(qn("w:pPr"))
            if p_pr is not None:
                for tag in ("w:keepNext", "w:keepLines"):
                    el = p_pr.find(qn(tag))
                    if el is not None:
                        p_pr.remove(el)
            changed += 1

        if not paragraph_has_page_break_before(hdr):
            continue

        set_page_break_before(hdr, False)
        if target_idx is not None:
            set_page_break_before(doc.paragraphs[target_idx], True)
        changed += 1

    return changed


def remove_empty_paragraphs_after_marker(doc: Document, marker: str) -> None:
    idx = find_paragraph_index(doc, marker)
    while idx + 1 < len(doc.paragraphs) and is_paragraph_empty(doc.paragraphs[idx + 1]):
        el = doc.paragraphs[idx + 1]._element
        el.getparent().remove(el)


def ensure_razrabotal_marker(doc: Document) -> bool:
    """Поставить «Разработал:», если блок подписи есть, а маркера нет."""
    try:
        find_paragraph_index(doc, "Разработал:")
        return False
    except ValueError:
        pass
    tail = find_signatory_tail_start(doc)
    if tail is None:
        return False
    text = paragraph_text_normalized(doc.paragraphs[tail])
    if text.upper().startswith("РАЗРАБОТАЛ"):
        if text != "Разработал:":
            set_paragraph_text(doc.paragraphs[tail], "Разработал:", bold=False)
            return True
        return False
    marker = insert_empty_paragraph_before(doc.paragraphs[tail])
    set_paragraph_text(marker, "Разработал:", bold=False)
    return True


def set_one_point_five_line_spacing(paragraph: Paragraph) -> None:
    pf = paragraph.paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.ONE_POINT_FIVE
    pf.line_spacing = SIGNATORY_LINE_SPACING
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)


def paragraph_has_one_point_five_spacing(paragraph: Paragraph) -> bool:
    pf = paragraph.paragraph_format
    if pf.line_spacing_rule != WD_LINE_SPACING.ONE_POINT_FIVE:
        return False
    ls = pf.line_spacing
    if ls is None:
        return True
    try:
        return abs(float(ls) - SIGNATORY_LINE_SPACING) <= SIGNATORY_LINE_SPACING_TOLERANCE
    except (TypeError, ValueError):
        return True


def apply_signatory_line_spacing(doc: Document, profile: DocumentProfile) -> int:
    """
    Межстрочный интервал 1,5 на блоке подписантов — от «Разработал:» до последней подписи.
    На абзацы тела документа не распространяется.
    """
    if not profile.has_signatories:
        return 0
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return 0
    changed = 0
    for paragraph in doc.paragraphs[razrab_idx:]:
        if not paragraph_text_normalized(paragraph):
            continue
        if not paragraph_has_one_point_five_spacing(paragraph):
            set_one_point_five_line_spacing(paragraph)
            changed += 1
    return changed


def validate_signatory_line_spacing(doc: Document, profile: DocumentProfile) -> list[str]:
    if not profile.has_signatories:
        return []
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return []
    for paragraph in doc.paragraphs[razrab_idx:]:
        if not paragraph_text_normalized(paragraph):
            continue
        if not paragraph_has_one_point_five_spacing(paragraph):
            snippet = paragraph.text.strip()[:40] or "подписант"
            return [f"Блок подписантов: интервал не 1,5 у «{snippet}»"]
    return []


def fix_signatory_block_format(
    doc: Document, profile: DocumentProfile, *, use_etalon_spacing: bool = False
) -> None:
    if not profile.has_signatories:
        return
    ensure_razrabotal_marker(doc)
    try:
        fix_razrabotal(doc)
        fix_soglasovano(doc)
    except ValueError:
        return
    apply_signatory_line_spacing(doc, profile)
    try:
        razrab_idx = find_paragraph_index(doc, "Разработал:")
    except ValueError:
        razrab_idx = len(doc.paragraphs)
    for paragraph in doc.paragraphs[razrab_idx:]:
        if paragraph_text_normalized(paragraph):
            for run in paragraph.runs:
                if run.text:
                    apply_run_font(run)
    remove_empty_paragraphs_after_marker(doc, "Разработал:")
    remove_empty_paragraphs_after_marker(doc, "Согласовано:")
    ensure_single_empty_line_before(doc, "Разработал:")
    ensure_single_empty_line_before(doc, "Согласовано:")
    remove_extra_empty_lines_in_body(doc)


def find_last_body_paragraph_before_signatories(doc: Document) -> int | None:
    """Последний непустой абзац тела перед блоком подписантов (не «Разработал:»)."""
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return None
    for idx in range(razrab_idx - 1, -1, -1):
        text = paragraph_text_normalized(doc.paragraphs[idx])
        if not text:
            continue
        upper = text.upper()
        if upper.startswith(("РАЗРАБОТАЛ", "СОГЛАСОВАН")):
            continue
        return idx
    return None


def _tail_chapter_indices_before_signatories(doc: Document) -> list[int]:
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        razrab_idx = len(doc.paragraphs)
    return [i for i in find_chapter_header_indices(doc) if i < razrab_idx]


def validate_last_two_pages_layout(doc: Document, profile: DocumentProfile) -> list[str]:
    """
    Проверка предпоследней/последней страницы (эвристики без рендера Word).
    Цель: текст и подписанты вместе; нет разрыва только перед «Разработал:».
    """
    if not profile.has_signatories:
        return []

    issues: list[str] = []
    issues.extend(validate_signatory_block(doc, profile))
    issues.extend(validate_page_layout_flags(doc, profile))

    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError as exc:
        issues.append(str(exc))
        return issues

    if paragraph_has_page_break_before(doc.paragraphs[razrab_idx]):
        issues.append(
            "Запрещён разрыв страницы только перед «Разработал:» — подписанты оторваны от текста"
        )

    last_body_idx = find_last_body_paragraph_before_signatories(doc)
    if last_body_idx is None:
        issues.append("Подписанты без текста перед ними — блок оторван от содержания")
    elif is_chapter_header(paragraph_text_normalized(doc.paragraphs[last_body_idx])):
        issues.append(
            "Перед «Разработал:» только заголовок главы без текста — подписанты оторваны"
        )

    tail_chapters = _tail_chapter_indices_before_signatories(doc)
    if tail_chapters:
        ch5_idx = tail_chapters[-1]
        ch5_text = paragraph_text_normalized(doc.paragraphs[ch5_idx])
        if paragraph_has_page_break_before(doc.paragraphs[ch5_idx]):
            issues.append(f"Разрыв страницы на заголовке главы (сирота): {ch5_text[:40]}")
        content_idx = find_first_nonempty_paragraph_after(doc, ch5_idx)
        if content_idx is None or content_idx >= razrab_idx:
            issues.append(f"Заголовок главы без текста перед подписантами: {ch5_text[:40]}")
        elif content_idx > ch5_idx + 1:
            issues.append(
                f"Пустая строка между заголовком главы и текстом в хвосте: {ch5_text[:40]}"
            )

    return issues


def signatories_appear_orphaned(doc: Document, profile: DocumentProfile) -> bool:
    """Эвристика: подписанты оторваны от текста (без рендера Word)."""
    if not profile.has_signatories:
        return False
    markers = (
        "оторван",
        "отдельной странице",
        "только заголовок",
        "только перед «разработал",
        "без текста перед",
        "запрещён разрыв страницы только перед «разработал",
    )
    for issue in validate_last_two_pages_layout(doc, profile):
        low = issue.lower()
        if any(m in low for m in markers):
            return True
    return False


def fix_last_pages_and_signatories(
    doc: Document,
    profile: DocumentProfile,
    *,
    mode: str = "natural",
    use_etalon_spacing: bool = False,
) -> int:
    """
    Шаг 1 — естественная вёрстка хвоста и блок подписантов.
    Снимает принудительные разрывы/keep, оформляет подписантов (1,5, пустые строки).
    """
    changed = 0
    for paragraph in doc.paragraphs:
        before = (
            paragraph.paragraph_format.keep_with_next
            or paragraph.paragraph_format.keep_together
            or paragraph.paragraph_format.page_break_before
        )
        clear_paragraph_page_layout(paragraph)
        if before:
            changed += 1

    if mode != "natural":
        mode = "natural"

    fix_signatory_block_format(doc, profile, use_etalon_spacing=use_etalon_spacing)
    changed += prevent_chapter_header_orphan(doc)

    ensure_chapter_header_spacing(doc)
    removed = remove_extra_empty_lines_in_body(doc)
    changed += removed

    changed += apply_signatory_line_spacing(doc, profile)

    try:
        ensure_single_empty_line_before(doc, "Разработал:")
        ensure_single_empty_line_before(doc, "Согласовано:")
    except ValueError:
        pass

    try:
        remove_empty_paragraphs_after_marker(doc, "Разработал:")
    except ValueError:
        pass
    try:
        remove_empty_paragraphs_after_marker(doc, "Согласовано:")
    except ValueError:
        pass
    return changed


def fix_last_pages_page_breaks(
    doc: Document, profile: DocumentProfile, *, force: bool = False
) -> str:
    """
    Шаг 2 — перенос страниц только если подписанты оторваны от текста
    (или force=True / --fix-page-breaks).
    Никогда не ставит разрыв только перед «Разработал:».
    """
    if not profile.has_signatories:
        return "natural"

    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return "natural"

    set_page_break_before(doc.paragraphs[razrab_idx], False)

    if not force and not signatories_appear_orphaned(doc, profile):
        return "natural"

    if profile.tail_chapter_idx is None:
        return "natural"

    return apply_signatory_page_break(doc, profile)


def apply_signatory_page_break(doc: Document, profile: DocumentProfile) -> str:
    """
    Шаг 2: ровно один page_break_before — на один абзац перед подписантами,
    чтобы текст и подписи оказались на одной странице.
    """
    if profile.tail_chapter_idx is None or not profile.has_signatories:
        return "natural"
    try:
        razrab_idx = find_razrabotal_index(doc)
    except ValueError:
        return "natural"

    for idx in range(profile.tail_chapter_idx, razrab_idx):
        set_page_break_before(doc.paragraphs[idx], False)

    last_body = find_last_body_paragraph_before_signatories(doc)
    if last_body is None:
        return "natural"

    target = last_body
    if is_chapter_header(paragraph_text_normalized(doc.paragraphs[target])):
        content_idx = find_first_nonempty_paragraph_after(doc, profile.tail_chapter_idx)
        if content_idx is None or content_idx >= razrab_idx:
            return "natural"
        target = content_idx

    set_page_break_before(doc.paragraphs[target], True)
    return "before_paragraph"


def process_sniot_document(
    doc: Document,
    profile: DocumentProfile,
    *,
    apply_page_breaks: bool = False,
    etalon_path: Path | None = None,
) -> str:
    """
    Порядок правок (правило 25):
    title (до загрузки Document) → numbering → font → chapters (center)
    → body (justify + 1,25 см) → spacing → fix_last_pages_and_signatories (шаг 1)
    → fix_last_pages_page_breaks при необходимости (шаг 2).
    Если в папке есть *_образец.docx — интервалы выравниваются по образцу.
    """
    etalon_doc: Document | None = None
    if etalon_path and etalon_path.is_file():
        etalon_doc = Document(etalon_path)
    use_etalon = etalon_doc is not None

    normalize_first_chapter_heading(doc)
    if profile.has_di_satp_numbering:
        restore_chapter_headers(doc)
    deduplicate_manual_and_list_numbering(doc)
    numbering_issues = validate_numbering_blocks(doc, profile)
    if numbering_issues:
        fix_numbering_selective(doc, profile)
    deduplicate_manual_and_list_numbering(doc)
    normalize_document_fonts(doc)
    center_chapter_headers(doc)
    apply_russian_phrase_rules(doc, profile)
    apply_body_paragraph_format(doc, profile)
    if not use_etalon:
        remove_extra_empty_lines_in_body(doc)
        ensure_chapter_header_spacing(doc)
    apply_body_paragraph_format(doc, profile)
    if use_etalon and etalon_doc is not None:
        align_spacing_to_etalon(doc, etalon_doc, profile)
        ensure_chapter_header_spacing(doc)
        remove_extra_empty_lines_in_body(doc)
        apply_body_paragraph_format(doc, profile)

    fix_last_pages_and_signatories(doc, profile, mode="natural", use_etalon_spacing=use_etalon)
    apply_body_paragraph_format(doc, profile)
    deduplicate_manual_and_list_numbering(doc)

    strategy = "natural"
    if apply_page_breaks or signatories_appear_orphaned(doc, profile):
        strategy = fix_last_pages_page_breaks(doc, profile, force=apply_page_breaks)
        try:
            razrab_idx = find_razrabotal_index(doc)
            set_page_break_before(doc.paragraphs[razrab_idx], False)
        except ValueError:
            pass
        apply_signatory_line_spacing(doc, profile)
        try:
            ensure_single_empty_line_before(doc, "Разработал:")
            ensure_single_empty_line_before(doc, "Согласовано:")
        except ValueError:
            pass

    return strategy


def process_document(
    doc: Document,
    *,
    apply_page_breaks: bool = False,
    path: Path | None = None,
    etalon_path: Path | None = None,
) -> str:
    """API для любого документа; path нужен для detect_profile."""
    profile = detect_profile(doc, path or Path("_unknown_.docx"))
    if etalon_path is None and path is not None:
        etalon_path = find_etalon_path(path)
    return process_sniot_document(
        doc, profile, apply_page_breaks=apply_page_breaks, etalon_path=etalon_path
    )


def export_debug(doc: Document, path: Path) -> None:
    lines = []
    for i, paragraph in enumerate(doc.paragraphs):
        fonts = {run.font.name for run in paragraph.runs if run.text.strip()}
        lines.append(f"{i+1:4d} font={fonts} | {paragraph.text}")
    path.write_text("\n".join(lines), encoding="utf-8")


def print_issues(title: str, issues: list[str]) -> None:
    if not issues:
        return
    print(title)
    for item in issues:
        print(f"  - {item}")


def apply_sniot_rules_to_file(
    target: Path,
    *,
    fix_page_breaks: bool = False,
    always_apply: bool = True,
) -> dict:
    """
    Применить все правила СНиОТ к docx и сохранить на месте.

    Для DocAgent «Оформить документ»: финальный проход после type-specific форматтеров.
    always_apply=True — выполнять process_sniot_document даже если validate OK до правки
    (structure_fix мог оставить шрифт/нумерацию/колонтитулы не по правилам).
    """
    result: dict = {
        "ok": False,
        "applied": False,
        "before_issues": [],
        "after_issues": [],
        "strategy": "natural",
        "actions": [],
    }
    if not target.is_file():
        result["actions"].append(f"СНиОТ: файл не найден — {target}")
        return result
    try:
        assert_path_writable(target)
    except PermissionError as exc:
        result["actions"].append(str(exc))
        return result
    if target.suffix.lower() != ".docx":
        result["ok"] = True
        result["actions"].append("СНиОТ: пропуск (не .docx)")
        return result

    preview_doc = Document(target)
    profile = detect_profile(preview_doc, target)
    before_nonempty = count_nonempty_body_paragraphs(preview_doc)
    before_numbered = count_numbered_paragraphs(preview_doc)
    cleaned_bytes = remove_duplicate_body_title(target.read_bytes(), profile.first_chapter)

    WORK_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^\w\-]+", "_", target.stem)[:48] or "doc"
    work_path = WORK_DIR / f"_docagent_sniot_{safe_stem}.docx"
    out_path = WORK_DIR / f"_docagent_sniot_fixed_{safe_stem}.docx"
    work_path.write_bytes(cleaned_bytes)

    restored_n, restored_msg = maybe_restore_senior_master_body(work_path)
    if restored_n:
        result["actions"].append(restored_msg)

    doc = Document(work_path)
    body_after_clean = count_nonempty_body_paragraphs(doc)
    if before_nonempty >= 5 and body_after_clean == 0:
        cleaned_bytes = target.read_bytes()
        work_path.write_bytes(cleaned_bytes)
        doc = Document(work_path)
        result["actions"].append(
            "СНиОТ: удаление дубля титула пропущено — иначе было бы стёрто всё тело документа"
        )
    profile = detect_profile(doc, target)
    before_issues = validate_sniot_document(
        doc, docx_bytes=cleaned_bytes, profile=profile, path=target
    )
    result["before_issues"] = before_issues

    if not always_apply and not before_issues:
        result["ok"] = True
        result["actions"].append("СНиОТ: уже соответствует правилам")
        return result

    etalon_path = find_etalon_path(target)
    if etalon_path:
        result["actions"].append(f"СНиОТ: образец — {etalon_path.name}")

    strategy = process_sniot_document(
        doc, profile, apply_page_breaks=fix_page_breaks, etalon_path=etalon_path
    )
    result["strategy"] = strategy

    doc.save(out_path)
    fixed_bytes = fix_page_numbering(out_path.read_bytes())
    out_path.write_bytes(fixed_bytes)

    doc = Document(out_path)
    profile = detect_profile(doc, target)
    integrity = validate_save_integrity(
        before_nonempty=before_nonempty,
        before_numbered=before_numbered,
        after_doc=doc,
        profile=profile,
    )
    if integrity:
        result["after_issues"] = integrity + result.get("after_issues", [])
        result["ok"] = False
        result["actions"].append(
            "СНиОТ: сохранение отменено — документ стал хуже (см. integrity)"
        )
        for item in integrity:
            result["actions"].append(f"СНиОТ ⛔ {item}")
        return result

    after_issues = validate_sniot_document(
        doc, docx_bytes=fixed_bytes, profile=profile, path=target
    )
    result["after_issues"] = after_issues

    try:
        assert_path_writable(target)
        shutil.copy2(out_path, target)
        result["applied"] = True
        result["ok"] = not after_issues
        if before_issues:
            result["actions"].append(
                f"СНиОТ (fix_sniot_document): исправлено замечаний — {len(before_issues)}"
            )
        else:
            result["actions"].append("СНиОТ (fix_sniot_document): финальная проверка и правка")
        result["actions"].append(
            "СНиОТ: дубль титула, TNR 14, номера стр., главы, тело 1,25 см, подписанты, нумерация 1.5.x"
        )
        result["actions"].append(f"СНиОТ: перенос страниц — {strategy}")
        result["actions"].append(
            f"СНиОТ: абзацев в теле — {count_nonempty_body_paragraphs(doc)}"
        )
        if after_issues:
            for item in after_issues[:6]:
                result["actions"].append(f"СНиОТ ! {item}")
        else:
            result["actions"].append("СНиОТ: Validation OK (0 issues)")
    except PermissionError:
        result["ok"] = False
        result["actions"].append(
            "СНиОТ: не удалось сохранить — закройте «_оформлен.docx» в Word и повторите"
        )

    return result


def autofix(
    target: Path,
    *,
    check_only: bool = False,
    dry_run: bool = False,
    fix_page_breaks: bool = False,
    always_apply: bool = False,
) -> int:
    """Полный автоматический цикл. Возвращает код выхода."""
    print(f"=== СНиОТ: {target.name} ({detect_document_kind(target)}) ===")

    preview_doc = Document(target)
    profile = detect_profile(preview_doc, target)
    cleaned_bytes = remove_duplicate_body_title(target.read_bytes(), profile.first_chapter)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    WORK_FILE.write_bytes(cleaned_bytes)

    doc = Document(WORK_FILE)
    profile = detect_profile(doc, target)
    before_issues = validate_sniot_document(doc, docx_bytes=cleaned_bytes, profile=profile, path=target)

    if check_only:
        print_issues("Проблемы:", before_issues)
        print("OK" if not before_issues else "Есть замечания")
        return EXIT_OK if not before_issues else EXIT_VALIDATION_FAIL

    if always_apply:
        rep = apply_sniot_rules_to_file(
            target, fix_page_breaks=fix_page_breaks, always_apply=True
        )
        print_issues("Было:", rep.get("before_issues") or [])
        print(f"Стратегия переноса: {rep.get('strategy', 'natural')}")
        print_issues("Осталось:", rep.get("after_issues") or [])
        if rep.get("applied"):
            print("Validation: OK" if not rep.get("after_issues") else "Есть замечания после правки")
        for act in rep.get("actions") or []:
            print(act)
        if not rep.get("ok"):
            return EXIT_VALIDATION_FAIL if rep.get("after_issues") else EXIT_FILE_LOCKED
        return EXIT_OK

    if not before_issues:
        print("Документ уже соответствует правилам. Правка не требуется.")
        return EXIT_OK

    print_issues("Исправляю:", before_issues)
    etalon_path = find_etalon_path(target)
    if etalon_path:
        print(f"Образец: {etalon_path.name}")
    strategy = process_sniot_document(
        doc, profile, apply_page_breaks=fix_page_breaks, etalon_path=etalon_path
    )

    doc.save(OUT_FILE)
    fixed_bytes = fix_page_numbering(OUT_FILE.read_bytes())
    OUT_FILE.write_bytes(fixed_bytes)
    doc = Document(OUT_FILE)
    profile = detect_profile(doc, target)
    after_issues = validate_sniot_document(doc, docx_bytes=fixed_bytes, profile=profile, path=target)
    export_debug(doc, OUT_FILE.with_suffix(".txt"))

    print(f"Стратегия переноса: {strategy}")
    print_issues("Осталось:", after_issues)

    if after_issues:
        print("ОШИБКА: после правки остались замечания.")
        return EXIT_VALIDATION_FAIL

    print("Validation: OK")

    if dry_run:
        print(f"Dry-run: результат в {OUT_FILE}")
        return EXIT_OK

    backup = target.with_name(
        target.stem + f"_backup_{datetime.now().strftime('%Y%m%d_%H%M')}" + target.suffix
    )
    try:
        assert_path_writable(target)
        assert_path_writable(backup)
        shutil.copy2(target, backup)
        shutil.copy2(OUT_FILE, target)
    except PermissionError:
        print("ОШИБКА: закройте файл в Word и запустите снова.")
        print(f"Готовый результат сохранён локально: {OUT_FILE}")
        return EXIT_FILE_LOCKED

    print(f"Сохранено: {target}")
    print(f"Бэкап: {backup.name}")
    return EXIT_OK


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Автоисправление документов СНиОТ — ДИ, РИ, Положения и др.",
        epilog="Правила: sniot-di-documents.mdc. Образец docx не важнее правил.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--target", type=Path, default=None, help="Необязательно: другой docx")
    parser.add_argument(
        "--handoff",
        action="store_true",
        help="Взять путь из DocAgent/handoff/request_latest.json",
    )
    parser.add_argument("--check", action="store_true", help="Только проверка (validate_sniot_document)")
    parser.add_argument("--dry-run", action="store_true", help="Без записи на N:\\")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Явная запись на N:\\ (по умолчанию без --dry-run запись выполняется)",
    )
    parser.add_argument(
        "--fix-page-breaks",
        action="store_true",
        help="Шаг 2: перенос страниц, если подписанты оторваны",
    )
    parser.add_argument(
        "--always-apply",
        action="store_true",
        help="Всегда выполнять process_sniot_document (для DocAgent «Оформить документ»)",
    )
    parser.add_argument(
        "--show-rules",
        action="store_true",
        help="Показать сводку правил и выйти",
    )
    parser.add_argument(
        "--restore-from-dump",
        type=Path,
        default=None,
        help="Восстановить тело из текстового дампа export_debug (_work_*.txt)",
    )
    args = parser.parse_args()

    if args.show_rules:
        print(RULES.strip())
        sys.exit(EXIT_OK)

    try:
        target = resolve_target(args.target, use_handoff=args.handoff)
    except FileNotFoundError as exc:
        print(f"ОШИБКА: {exc}")
        sys.exit(EXIT_NOT_FOUND)

    if args.restore_from_dump:
        dump = args.restore_from_dump.expanduser()
        if not dump.is_file():
            dump = Path(__file__).resolve().parent / dump.name
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        backup = target.with_name(f"{target.stem}_backup_{stamp}{target.suffix}")
        try:
            assert_path_writable(target)
            assert_path_writable(backup)
            shutil.copy2(target, backup)
            n = replace_body_from_debug_dump(target, dump)
            print(f"Восстановлено {n} абзацев из {dump.name}")
            print(f"Бэкап: {backup}")
        except Exception as exc:
            print(f"ОШИБКА восстановления: {exc}")
            sys.exit(EXIT_VALIDATION_FAIL)
        code = autofix(
            target,
            check_only=False,
            dry_run=False,
            fix_page_breaks=args.fix_page_breaks,
            always_apply=True,
        )
        sys.exit(code)

    # ДИ «Старший мастер»: авто-восстановление из дампа, если нумерация пропала
    if is_senior_master_di_path(target) and not args.check:
        restored_n, restored_msg = maybe_restore_senior_master_body(target)
        if restored_n:
            print(restored_msg)

    dry_run = args.dry_run and not args.apply

    code = autofix(
        target,
        check_only=args.check,
        dry_run=dry_run,
        fix_page_breaks=args.fix_page_breaks,
        always_apply=args.always_apply,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
