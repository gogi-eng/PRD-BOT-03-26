# -*- coding: utf-8 -*-
"""
Запрещённые / канцелярские фразы → правильные (правила русского для СНиОТ).

Используются в fix_sniot_document (финальный проход) и russian_check.
Замена подстроки в тексте абзаца — нумерация «2.2.17.» не затрагивается.
Аббревиатуры (ЛСиМ, СНиОТ, ТКП…) этим словарём не трогаются — см. russian_check.is_abbreviation_token.
"""

from __future__ import annotations

import re
from typing import Callable

# wrong → correct (подстрока; регистр вариантов — отдельные строки при необходимости)
PHRASE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "Выполняет локальные правовые акты",
        "Выполняет требования локальных правовых актов",
    ),
    (
        "выполняет локальные правовые акты",
        "выполняет требования локальных правовых актов",
    ),
)

# Для отчётов / RULES в fix_sniot_document
PHRASE_RULES_DOC: str = """
Правила русского языка (канцелярские фразы СНиОТ)
-------------------------------------------------
Запрещено → правильно:
  • «Выполняет локальные правовые акты» → «Выполняет требования локальных правовых актов»
Применение: только текст абзацев тела (не заголовки глав, не подписанты).
"""


def apply_phrase_replacements(text: str) -> tuple[str, list[str]]:
    """Подстроковые замены по PHRASE_REPLACEMENTS."""
    if not text:
        return text, []
    details: list[str] = []
    out = text
    for wrong, correct in PHRASE_REPLACEMENTS:
        if wrong in out:
            count = out.count(wrong)
            out = out.replace(wrong, correct)
            details.append(f"фраза: «{wrong}» → «{correct}» (x{count})")
    return out, details


def apply_phrase_replacements_regex(text: str) -> tuple[str, list[str]]:
    """Расширенный вариант с regex (на будущее); сейчас = apply_phrase_replacements."""
    return apply_phrase_replacements(text)


def should_apply_to_paragraph(
    text: str,
    idx: int,
    *,
    is_chapter_header: Callable[[str], bool],
    is_section_header: Callable[[str], bool],
    signatory_start: int | None,
) -> bool:
    """Тело документа: нумерованные пункты и обычный текст; не главы и не подписанты."""
    t = text.strip()
    if not t:
        return False
    if is_chapter_header(t) or is_section_header(t):
        return False
    if signatory_start is not None and idx >= signatory_start:
        return False
    upper = t.upper()
    if upper.startswith(("РАЗРАБОТАЛ", "СОГЛАСОВАН")):
        return False
    return True
