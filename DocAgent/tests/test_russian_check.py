# -*- coding: utf-8 -*-
"""Проверка русского: аббревиатуры не правятся; фразы можно править отдельно."""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formatters.russian_check import (
    apply_local_russian_fixes,
    is_abbreviation_token,
    _safe_spelling_replace,
)
from formatters.russian_phrase_rules import apply_phrase_replacements


def test_abbreviation_tokens_detected():
    for word in (
        "ЛСиМ",
        "СНиОТ",
        "САТП",
        "ТКП",
        "НПА",
        "ТНПА",
        "ЛПА",
        "ФИО",
        "РТС",
        "ЛЭС",
        "АТП",
        "ОК",
        "ЮО",
        "ООТиЗ",
    ):
        assert is_abbreviation_token(word), word
    assert not is_abbreviation_token("от")
    assert is_abbreviation_token("ОК")


def test_engineer_lsim_not_changed():
    text = "1.6. Инженер ЛСиМ руководствуется ТКП, НПА и СНиОТ."
    new, _ = apply_local_russian_fixes(text)
    assert "ЛСиМ" in new
    assert "ТКП" in new
    assert "НПА" in new
    assert "СНиОТ" in new
    assert "Осим" not in new
    assert "Сим" not in new
    assert new.startswith("1.6.")


def test_speller_must_not_replace_lsim_with_osim():
    text = "Инженер ЛСиМ работает."
    pos = text.index("ЛСиМ")
    assert _safe_spelling_replace(text, pos, 4, "Осим") is None
    assert _safe_spelling_replace(text, pos, 4, "Сим") is None


def test_speller_must_not_replace_sniot_tkp():
    text = "Служба СНиОТ применяет ТКП."
    p_sniot = text.index("СНиОТ")
    p_tkp = text.index("ТКП")
    assert _safe_spelling_replace(text, p_sniot, 5, "Сниот") is None
    assert _safe_spelling_replace(text, p_tkp, 3, "ткп") is None


def test_latin_c_in_sniot_still_fixed():
    text = "Инженер службы CНиОТ."
    new, details = apply_local_russian_fixes(text)
    assert "СНиОТ" in new
    assert "CНиОТ" not in new
    assert details


def test_local_typo_fixed_near_abbreviation():
    text = "Инженер ЛСиМ в течении года руководствуется ТКП."
    new, _ = apply_local_russian_fixes(text)
    assert "ЛСиМ" in new
    assert "ТКП" in new
    assert "в течение" in new
    assert "в течении" not in new


def test_phrase_rules_can_fix_local_acts():
    text = "2.2.17. Выполняет локальные правовые акты предприятия."
    new, details = apply_phrase_replacements(text)
    assert "требования локальных правовых актов" in new
    assert new.startswith("2.2.17.")
    assert details
    via_check, _ = apply_local_russian_fixes(text)
    assert "требования локальных правовых актов" in via_check


def test_phrase_with_abbreviation_keeps_lsim():
    text = "Инженер ЛСиМ выполняет локальные правовые акты предприятия."
    new, _ = apply_local_russian_fixes(text)
    assert "ЛСиМ" in new
    assert "требования локальных правовых актов" in new


def test_process_document_does_not_force_off_russian_for_di():
    from agent_core import process_document

    src = inspect.getsource(process_document)
    assert "apply_russian_check_flag = False" not in src
