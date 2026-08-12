# -*- coding: utf-8 -*-
"""Тесты словаря канцелярских фраз."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from formatters.russian_phrase_rules import apply_phrase_replacements


def test_forbidden_local_legal_acts_phrase():
    text = "2.2.17. Выполняет локальные правовые акты предприятия."
    new, details = apply_phrase_replacements(text)
    assert "требования локальных правовых актов" in new
    assert "локальные правовые акты" not in new
    assert new.startswith("2.2.17.")
    assert details


def test_phrase_replace_preserves_numbering():
    text = "1.5.1. выполняет локальные правовые акты и инструкции."
    new, _ = apply_phrase_replacements(text)
    assert new.startswith("1.5.1.")
    assert "требования локальных правовых актов" in new
