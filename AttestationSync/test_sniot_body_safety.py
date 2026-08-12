# -*- coding: utf-8 -*-
"""Тест: remove_duplicate_body_title не стирает тело без маркера главы."""
from __future__ import annotations

import sys
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import fix_sniot_document as fix


def _make_doc_with_body() -> bytes:
    doc = Document()
    doc.add_paragraph("ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("Настоящая должностная инструкция определяет функции.")
    doc.add_paragraph("Старший мастер должен знать:")
    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_remove_duplicate_does_not_wipe_body_without_chapter_marker():
    raw = _make_doc_with_body()
    before = len(Document(BytesIO(raw)).paragraphs)
    cleaned = fix.remove_duplicate_body_title(raw, first_chapter=None)
    after = len(Document(BytesIO(cleaned)).paragraphs)
    assert before >= 3
    assert after == before


def test_validate_empty_body_reports_issue():
    doc = Document()
    profile = fix.DocumentProfile("di", None, False, False, None)
    issues = fix.validate_body_not_empty(doc, profile)
    assert any("пуст" in i.lower() for i in issues)


def test_center_chapter_headers_sets_jc_in_xml():
    doc = Document()
    paragraph = doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    assert fix.center_chapter_headers(doc) == 1
    assert fix.is_paragraph_centered(paragraph)
    assert fix.paragraph_jc(paragraph) == "center"


def _di_satp_profile() -> fix.DocumentProfile:
    return fix.DocumentProfile(
        kind="di",
        first_chapter="1 ОБЩИЕ ПОЛОЖЕНИЯ",
        has_signatories=True,
        has_di_satp_numbering=True,
        tail_chapter_idx=None,
    )


def _build_correct_numbering_doc() -> Document:
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4. в своей деятельности руководствуется законодательством.")
    doc.add_paragraph("1.4.1. Первый пункт раздела 1.4.")
    doc.add_paragraph("1.5. Старший мастер должен знать:")
    doc.add_paragraph("1.5.1. ТКП первый.")
    doc.add_paragraph("1.5.2. ТКП второй.")
    doc.add_paragraph("2 ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ")
    doc.add_paragraph("2.1. выполняет следующие функции:")
    doc.add_paragraph("2.1.1. Функция одна.")
    doc.add_paragraph("2.2. Для выполнения возложенных на него функций старший мастер:")
    doc.add_paragraph("2.2.1. Обязанность одна.")
    doc.add_paragraph("3 ПРАВА")
    doc.add_paragraph("3.1. имеет право:")
    doc.add_paragraph("3.1.1. Право одно.")
    doc.add_paragraph("4 ВЗАИМООТНОШЕНИЯ")
    doc.add_paragraph("5 ОТВЕТСТВЕННОСТЬ")
    doc.add_paragraph("5.1. несет ответственность:")
    doc.add_paragraph("5.1.1. Ответственность одна.")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    doc.add_paragraph("Согласовано:")
    doc.add_paragraph("Начальник")
    return doc


def test_correct_numbering_not_modified():
    doc = _build_correct_numbering_doc()
    profile = _di_satp_profile()
    before = [p.text for p in doc.paragraphs]
    assert not fix.validate_numbering_blocks(doc, profile)
    changed = fix.fix_numbering_selective(doc, profile)
    after = [p.text for p in doc.paragraphs]
    assert changed == 0
    assert before == after


def test_wrong_prefix_19_fixed_only_in_block():
    doc = _build_correct_numbering_doc()
    profile = _di_satp_profile()
    doc.paragraphs[4].text = "1.9.1. ТКП с ошибкой."
    doc.paragraphs[5].text = "1.9.2. ТКП второй с ошибкой."
    assert fix.validate_numbering_blocks(doc, profile)
    fix.fix_numbering_selective(doc, profile)
    assert doc.paragraphs[4].text.startswith("1.5.1.")
    assert doc.paragraphs[5].text.startswith("1.5.2.")
    assert doc.paragraphs[2].text.startswith("1.4.1.")


def test_apply_body_paragraph_format_justify_and_indent():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    body = doc.add_paragraph("1.4.1. Текст абзаца тела документа.")
    profile = fix.DocumentProfile("di", "1 ОБЩИЕ ПОЛОЖЕНИЯ", False, False, None)
    fix.center_chapter_headers(doc)
    fix.apply_body_paragraph_format(doc, profile)
    assert fix.is_paragraph_justified(body)
    assert abs(fix.first_line_indent_cm(body) - 1.25) < 0.1
    chapter = doc.paragraphs[0]
    assert fix.is_paragraph_centered(chapter)
    assert fix.first_line_indent_cm(chapter) < 0.1


def test_assert_path_writable_rejects_obmen():
    obmen = (
        r"N:\9 - Служба надёжности и охраны труда (СНiОТ)\!!!ОБМЕН"
        r"\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\САТП\test.docx"
    )
    with pytest.raises(PermissionError):
        fix.assert_path_writable(obmen)


def test_assert_path_writable_accepts_agent():
    agent = (
        r"N:\9 - Служба надёжности и охраны труда (СНiОТ)\Дубовик В.В\Агент"
        r"\ПРОЕКТ Старший мастер_оформлен.docx"
    )
    if fix.USER_AGENT_DIR.is_dir():
        resolved = fix.assert_path_writable(agent)
        assert fix.is_path_in_user_agent_dir(resolved)


def test_no_empty_line_after_soglasovano():
    doc = Document()
    doc.add_paragraph("5.1.1. Последний пункт.")
    doc.add_paragraph("")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    doc.add_paragraph("")
    doc.add_paragraph("Согласовано:")
    doc.add_paragraph("")
    doc.add_paragraph("Начальник службы")
    profile = fix.DocumentProfile("di", None, True, False, None)
    fix.fix_signatory_block_format(doc, profile)
    soglas_idx = fix.find_soglasovano_index(doc)
    assert doc.paragraphs[soglas_idx + 1].text.strip()
    issues = fix.validate_signatory_block(doc, profile)
    assert not any("после «Согласовано»" in i for i in issues)


def test_russian_phrase_local_legal_acts_replaced():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    p = doc.add_paragraph(
        "2.2.17. Выполняет локальные правовые акты предприятия (приказы, указания)."
    )
    profile = fix.DocumentProfile("di", "1 ОБЩИЕ ПОЛОЖЕНИЯ", False, False, None)
    changed = fix.apply_russian_phrase_rules(doc, profile)
    assert changed >= 1
    assert "требования локальных правовых актов" in p.text
    assert "Выполняет локальные правовые акты" not in p.text
    assert p.text.startswith("2.2.17.")


def test_remove_triple_empty_lines_in_body():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4.1. Первый пункт.")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("1.4.2. Второй пункт.")
    removed = fix.remove_extra_empty_lines_in_body(doc)
    assert removed >= 2
    texts = [p.text.strip() for p in doc.paragraphs]
    assert "" not in texts[2:5]


def test_remove_empty_after_chapter_header_even_before_signatory():
    doc = Document()
    doc.add_paragraph("5 ОТВЕТСТВЕННОСТЬ")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    removed = fix.remove_empty_lines_after_chapter_headers(doc)
    assert removed >= 2
    assert doc.paragraphs[0].text.strip().startswith("5 ОТВЕТСТВЕННОСТЬ")
    assert doc.paragraphs[1].text.strip() == "Разработал:"


def test_signatory_tail_detected_without_razrabotal_marker():
    doc = Document()
    doc.add_paragraph("5.1.1. Последний пункт.")
    doc.add_paragraph("Начальник службы АТП\tА.И.Торопчин")
    doc.add_paragraph("СОГЛАСОВАНО")
    doc.add_paragraph("Заместитель")
    tail = fix.find_signatory_tail_start(doc)
    assert tail == 1
    profile = fix.DocumentProfile("di", None, True, False, None)
    fix.ensure_razrabotal_marker(doc)
    razrab_idx = fix.find_paragraph_index(doc, "Разработал:")
    assert doc.paragraphs[razrab_idx].text.strip() == "Разработал:"
    assert doc.paragraphs[razrab_idx + 1].text.strip().startswith("Начальник")
    fix.fix_signatory_block_format(doc, profile)
    razrab_idx = fix.find_paragraph_index(doc, "Разработал:")
    assert fix.count_empty_lines_before(doc, razrab_idx) == 1


def test_maybe_restore_skips_when_chapters_present():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4.1. Пункт.")
    doc.add_paragraph("2 ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ")
    doc.add_paragraph("2.1.1. Функция.")
    doc.add_paragraph("3 ПРАВА")
    doc.add_paragraph("3.1.1. Право.")
    doc.add_paragraph("4 ВЗАИМООТНОШЕНИЯ")
    doc.add_paragraph("5 ОТВЕТСТВЕННОСТЬ")
    doc.add_paragraph("5.1.1. Ответственность.")
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        path = Path(tmp.name)
    doc.save(path)
    try:
        path = path.rename(
            path.with_name("ПРОЕКТ Старший мастер_test_restore.docx")
        )
    except OSError:
        pass
    n, msg = fix.maybe_restore_senior_master_body(path)
    assert n == 0
    assert "Восстановлено" not in msg
    path.unlink(missing_ok=True)


def test_empty_line_before_razrabotal_preserved_after_body_cleanup():
    doc = Document()
    doc.add_paragraph("5.1.1. Последний пункт.")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    doc.add_paragraph("Согласовано:")
    doc.add_paragraph("Начальник")
    profile = fix.DocumentProfile("di", None, True, False, None)
    fix.ensure_single_empty_line_before(doc, "Разработал:")
    razrab_idx = fix.find_paragraph_index(doc, "Разработал:")
    assert fix.count_empty_lines_before(doc, razrab_idx) == 1
    fix.remove_extra_empty_lines_in_body(doc)
    razrab_idx = fix.find_paragraph_index(doc, "Разработал:")
    assert fix.count_empty_lines_before(doc, razrab_idx) == 1


def test_validate_empty_lines_in_body_reports_double():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4.1. Текст.")
    doc.add_paragraph("")
    doc.add_paragraph("")
    doc.add_paragraph("1.4.2. Ещё текст.")
    issues = fix.validate_empty_lines_in_body(doc)
    assert any("пустые строки" in i.lower() for i in issues)


def test_apply_signatory_line_spacing_sets_one_point_five():
    from docx.enum.text import WD_LINE_SPACING

    doc = Document()
    doc.add_paragraph("5.1.1. Последний пункт.")
    doc.add_paragraph("")
    p_raz = doc.add_paragraph("Разработал:")
    p_dev = doc.add_paragraph("Инженер\tИ.И. Иванов")
    doc.add_paragraph("")
    p_sog = doc.add_paragraph("Согласовано:")
    p_boss = doc.add_paragraph("Начальник\tП.П. Петров")
    for paragraph in (p_raz, p_dev, p_sog, p_boss):
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
        paragraph.paragraph_format.line_spacing = 1.0
    profile = fix.DocumentProfile("di", None, True, False, None)
    changed = fix.apply_signatory_line_spacing(doc, profile)
    assert changed >= 4
    for paragraph in (p_raz, p_dev, p_sog, p_boss):
        assert fix.paragraph_has_one_point_five_spacing(paragraph)
    assert not fix.validate_signatory_line_spacing(doc, profile)


def test_apply_signatory_line_spacing_does_not_touch_body():
    from docx.enum.text import WD_LINE_SPACING

    doc = Document()
    body = doc.add_paragraph("1.4.1. Текст тела документа.")
    body.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    body.paragraph_format.line_spacing = 1.0
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    profile = fix.DocumentProfile("di", None, True, False, None)
    fix.apply_signatory_line_spacing(doc, profile)
    assert body.paragraph_format.line_spacing_rule == WD_LINE_SPACING.SINGLE
    assert body.paragraph_format.line_spacing == 1.0


def test_fix_signatory_block_format_enforces_line_spacing():
    from docx.enum.text import WD_LINE_SPACING

    doc = Document()
    doc.add_paragraph("5.1.1. Последний пункт.")
    doc.add_paragraph("Разработал:")
    p_dev = doc.add_paragraph("Инженер")
    doc.add_paragraph("Согласовано:")
    p_boss = doc.add_paragraph("Начальник")
    for paragraph in (p_dev, p_boss):
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.SINGLE
    profile = fix.DocumentProfile("di", None, True, False, None)
    fix.fix_signatory_block_format(doc, profile)
    assert not fix.validate_signatory_line_spacing(doc, profile)
    assert not fix.validate_signatory_block(doc, profile)


def test_body_indent_after_hanging_style():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    p = doc.add_paragraph("1.4.1. Абзац с отступом списка.")
    p_pr = p._p.get_or_add_pPr()
    ind = OxmlElement("w:ind")
    ind.set(qn("w:hanging"), "360")
    p_pr.append(ind)
    profile = fix.DocumentProfile("di", "1 ОБЩИЕ ПОЛОЖЕНИЯ", False, False, None)
    fix.apply_body_paragraph_format(doc, profile)
    assert abs(fix.first_line_indent_cm(p) - 1.25) < 0.1


def _count_empty_paragraphs(doc: Document) -> int:
    return sum(1 for p in doc.paragraphs if not p.text.strip())


def test_ensure_chapter_header_spacing_does_not_add_empty_after_header():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4.1. Первый пункт.")
    doc.add_paragraph("")
    doc.add_paragraph("2 ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ")
    doc.add_paragraph("2.1.1. Функция.")
    before = _count_empty_paragraphs(doc)
    fix.ensure_chapter_header_spacing(doc)
    after = _count_empty_paragraphs(doc)
    assert after <= before
    ch2_idx = fix.find_paragraph_index(doc, "2 ФУНКЦИИ")
    assert doc.paragraphs[ch2_idx + 1].text.strip().startswith("2.1")


def test_prevent_chapter_header_orphan_moves_page_break_to_content():
    doc = Document()
    hdr = doc.add_paragraph("2 ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ")
    body = doc.add_paragraph("2.1.1. Текст функции.")
    fix.set_page_break_before(hdr, True)
    assert fix.paragraph_has_page_break_before(hdr)
    changed = fix.prevent_chapter_header_orphan(doc)
    assert changed >= 1
    assert not fix.paragraph_has_page_break_before(hdr)
    assert fix.paragraph_has_page_break_before(body)
    assert not fix.validate_chapter_header_orphan(doc)


def test_header_not_separated_from_content_by_page_break_only():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4.1. Пункт.")
    hdr = doc.add_paragraph("2 ФУНКЦИИ И ДОЛЖНОСТНЫЕ ОБЯЗАННОСТИ")
    doc.add_paragraph("2.1.1. Следующий текст.")
    fix.set_page_break_before(hdr, True)
    fix.prevent_chapter_header_orphan(doc)
    hdr_idx = fix.find_paragraph_index(doc, "2 ФУНКЦИИ")
    content_idx = fix.find_first_nonempty_paragraph_after(doc, hdr_idx)
    assert content_idx is not None
    assert not fix.paragraph_has_page_break_before(hdr)
    assert fix.paragraph_has_page_break_before(doc.paragraphs[content_idx])


def test_process_sniot_document_reduces_empty_after_headers():
    doc = _build_correct_numbering_doc()
    added = 0
    for idx in fix.find_chapter_header_indices(doc):
        if idx + 1 < len(doc.paragraphs) and doc.paragraphs[idx + 1].text.strip():
            fix.insert_empty_paragraph_before(doc.paragraphs[idx + 1])
            added += 1
    assert added >= 2

    def empties_after_headers(d: Document) -> int:
        total = 0
        for hdr_idx in fix.find_chapter_header_indices(d):
            if hdr_idx + 1 < len(d.paragraphs) and not d.paragraphs[hdr_idx + 1].text.strip():
                total += 1
        return total

    before = empties_after_headers(doc)
    assert before >= 2
    profile = fix.detect_profile(doc, Path("ПРОЕКТ Старший мастер_оформлен.docx"))
    fix.process_sniot_document(doc, profile)
    after = empties_after_headers(doc)
    assert after < before
    assert after == 0
    assert not fix.validate_chapter_header_orphan(doc)


def _signatory_tail_doc() -> Document:
    doc = Document()
    doc.add_paragraph("5 ОТВЕТСТВЕННОСТЬ")
    doc.add_paragraph("5.1. несет ответственность:")
    doc.add_paragraph("5.1.1. Последний пункт ответственности.")
    doc.add_paragraph("")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер\tИ.И. Иванов")
    doc.add_paragraph("")
    doc.add_paragraph("Согласовано:")
    doc.add_paragraph("Начальник\tП.П. Петров")
    return doc


def test_validate_last_two_pages_rejects_page_break_only_on_razrabotal():
    doc = _signatory_tail_doc()
    profile = fix.DocumentProfile("di", None, True, False, 0)
    razrab_idx = fix.find_razrabotal_index(doc)
    fix.set_page_break_before(doc.paragraphs[razrab_idx], True)
    issues = fix.validate_last_two_pages_layout(doc, profile)
    assert any("только перед «Разработал:»" in i for i in issues)
    assert fix.signatories_appear_orphaned(doc, profile)


def test_fix_last_pages_and_signatories_clears_keep_and_formats_block():
    doc = _signatory_tail_doc()
    profile = fix.DocumentProfile("di", None, True, False, 0)
    razrab_idx = fix.find_razrabotal_index(doc)
    doc.paragraphs[razrab_idx].paragraph_format.keep_with_next = True
    doc.paragraphs[razrab_idx + 1].paragraph_format.keep_with_next = True
    fix.fix_last_pages_and_signatories(doc, profile)
    assert not doc.paragraphs[razrab_idx].paragraph_format.keep_with_next
    assert fix.count_empty_lines_before(doc, razrab_idx) == 1
    assert not fix.validate_signatory_block(doc, profile)
    assert not fix.validate_last_two_pages_layout(doc, profile)


def test_fix_last_pages_page_breaks_never_on_razrabotal():
    doc = _signatory_tail_doc()
    profile = fix.detect_profile(doc, Path("ПРОЕКТ Старший мастер_оформлен.docx"))
    razrab_idx = fix.find_razrabotal_index(doc)
    fix.set_page_break_before(doc.paragraphs[razrab_idx], True)
    strategy = fix.fix_last_pages_page_breaks(doc, profile, force=True)
    assert strategy in ("before_paragraph", "natural")
    assert not fix.paragraph_has_page_break_before(doc.paragraphs[razrab_idx])
    issues = fix.validate_last_two_pages_layout(doc, profile)
    assert not any("только перед «Разработал:»" in i for i in issues)


def test_signatories_not_orphaned_when_body_text_present():
    doc = _signatory_tail_doc()
    profile = fix.detect_profile(doc, Path("ПРОЕКТ Старший мастер_оформлен.docx"))
    fix.fix_last_pages_and_signatories(doc, profile)
    last_body = fix.find_last_body_paragraph_before_signatories(doc)
    assert last_body is not None
    assert not fix.is_chapter_header(doc.paragraphs[last_body].text)
    assert not fix.signatories_appear_orphaned(doc, profile)


def test_orphaned_signatories_detected_when_only_chapter_header_before():
    doc = Document()
    doc.add_paragraph("5 ОТВЕТСТВЕННОСТЬ")
    doc.add_paragraph("")
    doc.add_paragraph("Разработал:")
    doc.add_paragraph("Инженер")
    doc.add_paragraph("")
    doc.add_paragraph("Согласовано:")
    doc.add_paragraph("Начальник")
    profile = fix.DocumentProfile("di", None, True, False, 0)
    issues = fix.validate_last_two_pages_layout(doc, profile)
    assert any("заголовок главы" in i.lower() for i in issues)
    assert fix.signatories_appear_orphaned(doc, profile)


def test_deduplicate_list_and_manual_numbering():
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    doc = Document()
    p = doc.add_paragraph("1.5.1. 1.5.1. ТКП 608-2025 текст.")
    p_pr = p._p.get_or_add_pPr()
    num_pr = OxmlElement("w:numPr")
    ilvl = OxmlElement("w:ilvl")
    ilvl.set(qn("w:val"), "2")
    num_id = OxmlElement("w:numId")
    num_id.set(qn("w:val"), "1")
    num_pr.append(ilvl)
    num_pr.append(num_id)
    p_pr.append(num_pr)
    changed = fix.deduplicate_manual_and_list_numbering(doc)
    assert changed >= 2
    assert doc.paragraphs[0].text.startswith("1.5.1. ТКП")
    assert "1.5.1. 1.5.1." not in doc.paragraphs[0].text
    assert not fix.has_word_list_numbering(doc.paragraphs[0])
    assert not fix.validate_duplicate_list_numbering(doc)


def test_max_one_empty_before_chapter_after_process():
    doc = _build_correct_numbering_doc()
    # лишние пустые перед главой 5
    ch5_idx = fix.find_paragraph_index(doc, "5 ОТВЕТСТВЕННОСТЬ")
    for _ in range(3):
        fix.insert_empty_paragraph_before(doc.paragraphs[ch5_idx])
    profile = fix.detect_profile(doc, Path("ПРОЕКТ Старший мастер_оформлен.docx"))
    fix.process_sniot_document(doc, profile)
    ch5_idx = fix.find_paragraph_index(doc, "5 ОТВЕТСТВЕННОСТЬ")
    assert fix.count_empty_lines_before(doc, ch5_idx) <= 1
    assert not fix.validate_empty_lines_in_body(doc)


def test_indent_preserved_after_etalon_align():
    doc = Document()
    doc.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    doc.add_paragraph("1.4. в своей деятельности руководствуется:")
    body = doc.add_paragraph("1.4.1. Текст абзаца тела документа.")
    etalon = Document()
    etalon.add_paragraph("1 ОБЩИЕ ПОЛОЖЕНИЯ")
    etalon.add_paragraph("1.4. в своей деятельности руководствуется:")
    et_body = etalon.add_paragraph("1.4.1. Текст абзаца тела документа.")
    et_body.paragraph_format.first_line_indent = None
    p_pr = et_body._p.get_or_add_pPr()
    ind = p_pr.find(qn("w:ind"))
    if ind is not None:
        p_pr.remove(ind)
    profile = fix.DocumentProfile("di", "1 ОБЩИЕ ПОЛОЖЕНИЯ", False, True, None)
    fix.copy_paragraph_format_from_etalon(
        et_body,
        body,
        profile=profile,
        target_idx=2,
        doc=doc,
    )
    assert abs(fix.first_line_indent_cm(body) - 1.25) < 0.1


def test_single_paragraph_break_for_signatories():
    doc = _signatory_tail_doc()
    profile = fix.detect_profile(doc, Path("ПРОЕКТ Старший мастер_оформлен.docx"))
    breaks_before = [
        i
        for i, p in enumerate(doc.paragraphs)
        if fix.paragraph_has_page_break_before(p)
    ]
    assert not breaks_before
    strategy = fix.fix_last_pages_page_breaks(doc, profile, force=True)
    assert strategy == "before_paragraph"
    breaks_after = [
        i
        for i, p in enumerate(doc.paragraphs)
        if fix.paragraph_has_page_break_before(p)
    ]
    assert len(breaks_after) == 1
    razrab_idx = fix.find_razrabotal_index(doc)
    assert not fix.paragraph_has_page_break_before(doc.paragraphs[razrab_idx])
    last_body = fix.find_last_body_paragraph_before_signatories(doc)
    assert last_body is not None
    assert breaks_after[0] == last_body
