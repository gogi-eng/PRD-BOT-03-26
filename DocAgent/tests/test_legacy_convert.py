# -*- coding: utf-8 -*-
"""Конвертация .doc / .rtf → .docx без живого Word: расширения, копия, путь."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_core import convert_to_docx
from formatters.sniot_document import is_sniot_document, should_apply_sniot_pass
from formatters.word_com import (
    LEGACY_WORD_EXTENSIONS,
    converted_docx_path_for,
    copy_binary_unblocked,
    is_legacy_word_file,
)
from path_resolver import DOC_SUFFIXES


def test_legacy_extensions_include_doc_and_rtf():
    assert ".doc" in LEGACY_WORD_EXTENSIONS
    assert ".rtf" in LEGACY_WORD_EXTENSIONS
    assert ".rtf" in DOC_SUFFIXES
    assert is_legacy_word_file(r"N:\folder\инструкция.rtf")
    assert is_legacy_word_file(r"C:\a\b.RTF")
    assert is_legacy_word_file("file.doc")
    assert not is_legacy_word_file("file.docx")
    assert not is_legacy_word_file("file.pdf")


def test_converted_path_for_rtf_and_doc():
    rtf = Path(r"N:\Агент\ДИ старшего мастера.rtf")
    doc = Path(r"N:\Агент\ДИ старшего мастера.doc")
    assert converted_docx_path_for(rtf).name == "ДИ старшего мастера_converted.docx"
    assert converted_docx_path_for(doc).name == "ДИ старшего мастера_converted.docx"
    assert converted_docx_path_for(rtf).parent == rtf.parent


def test_copy_binary_unblocked_roundtrip(tmp_path: Path):
    src = tmp_path / "sample.rtf"
    src.write_bytes(b"{\\rtf1 test}")
    dst = tmp_path / "sub" / "source.rtf"
    copy_binary_unblocked(src, dst)
    assert dst.read_bytes() == src.read_bytes()


def test_convert_to_docx_passthrough_docx():
    path = r"C:\tmp\already.docx"
    assert convert_to_docx(path) == path


def test_convert_to_docx_rejects_unsupported():
    with pytest.raises(RuntimeError, match="Неподдерживаемый формат"):
        convert_to_docx(r"C:\tmp\file.pdf")


def test_rtf_in_agent_is_sniot_document():
    path = (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)"
        r"\Дубовик В.В\Агент\инструкция.rtf"
    )
    assert is_sniot_document(path) is True
    out = (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)"
        r"\Дубовик В.В\Агент\инструкция_оформлен.docx"
    )
    assert should_apply_sniot_pass(path, out, "dolzhnostnaya_instrukciya") is True
