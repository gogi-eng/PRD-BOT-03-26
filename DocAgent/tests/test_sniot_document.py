# -*- coding: utf-8 -*-
"""Тесты определения СНиОТ-пути и финального прохода fix_sniot_document."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formatters.sniot_document import is_sniot_document, should_apply_sniot_pass
from path_resolver import is_sniot_doc, normalize_sniot_path_text
from rules import detect_type_from_text


def test_normalize_latin_i_in_sniot_path():
    bad = r"N:\9 - Служба (СНiОТ)\!!!ОБМЕН\САТП\test.docx"
    fixed = normalize_sniot_path_text(bad)
    assert "СНiОТ" not in fixed
    assert "СНиОТ" in fixed


def test_is_sniot_with_latin_i():
    bad = r"N:\9 - Служба надёжности (СНiОТ)\!!!ОБМЕН\САТП\ПРОЕКТ.docx"
    assert is_sniot_document(bad) is True
    assert is_sniot_doc(Path(bad)) is True


def test_should_apply_for_latin_i_path():
    p = r"N:\9 - Служба (СНiОТ)\САТП\doc.docx"
    assert should_apply_sniot_pass(p, p, "unsupported") is True


def test_detect_proekt_starshiy_master_as_di():
    name = "ПРОЕКТ Старший мастер.docx"
    text = "УТВЕРЖДАЮ\nстарший мастер участка"
    assert detect_type_from_text(name, text) == "dolzhnostnaya_instrukciya"


def test_detect_di_from_table_title_text():
    name = "ПРОЕКТ Старший мастер.docx"
    text = "ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ\nстаршего мастера"
    assert detect_type_from_text(name, text) == "dolzhnostnaya_instrukciya"
