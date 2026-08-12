# -*- coding: utf-8 -*-
"""
Безопасная работа с Word через COM.

Важно для пользователя: во время работы агента Word с открытыми
документами НЕ должен закрываться.

Правила:
1) Если Word уже запущен — подключаемся к нему и НИКОГДА не делаем Quit().
2) Если Word не был запущен — создаём отдельный скрытый экземпляр (DispatchEx)
   и закрываем только его.
3) Документ, который уже открыт у пользователя, лучше читать/править через
   копию во временной папке (см. copy_for_word_com).
"""

from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def word_has_document_open(word, path: str) -> bool:
    """Проверить, открыт ли уже этот файл в данном Word."""
    target = _norm(path)
    try:
        for i in range(1, int(word.Documents.Count) + 1):
            try:
                full = _norm(word.Documents(i).FullName)
            except Exception:
                continue
            if full == target:
                return True
    except Exception:
        pass
    return False


@contextmanager
def word_app(*, visible: bool = False):
    """
    Контекст Word.Application без «выбрасывания» пользователя.

    Yields: (word, created_by_us: bool)
    """
    import win32com.client  # type: ignore

    word = None
    created = False
    try:
        try:
            word = win32com.client.GetActiveObject("Word.Application")
            created = False
        except Exception:
            # Новый отдельный процесс Word — Quit закроет только его
            word = win32com.client.DispatchEx("Word.Application")
            created = True
            try:
                word.Visible = visible
            except Exception:
                pass
            try:
                word.DisplayAlerts = 0
            except Exception:
                pass
        yield word, created
    finally:
        if created and word is not None:
            try:
                word.Quit()
            except Exception:
                pass


def copy_for_word_com(docx_path: str) -> str:
    """
    Копия файла во временную папку для безопасного открытия в Word,
    чтобы не трогать документ, который уже открыт у пользователя.
    """
    src = Path(docx_path)
    td = Path(tempfile.mkdtemp(prefix="docagent_word_"))
    dst = td / src.name
    shutil.copy2(src, dst)
    return str(dst)


def cleanup_word_temp(temp_docx_path: str) -> None:
    """Удалить временную папку копии (если создавали)."""
    try:
        parent = Path(temp_docx_path).parent
        if parent.name.startswith("docagent_word_"):
            shutil.rmtree(parent, ignore_errors=True)
    except Exception:
        pass


@contextmanager
def open_docx_readonly(docx_path: str) -> Iterator[object]:
    """
    Открыть документ только для чтения.
    Если файл уже открыт у пользователя — работаем с копией.
    Не закрывает Word пользователя.
    """
    abs_path = os.path.abspath(docx_path)
    temp_copy = None
    with word_app(visible=False) as (word, _created):
        path_to_open = abs_path
        if word_has_document_open(word, abs_path):
            temp_copy = copy_for_word_com(abs_path)
            path_to_open = temp_copy
        doc = word.Documents.Open(path_to_open, ReadOnly=True)
        try:
            yield doc
        finally:
            try:
                doc.Close(False)
            except Exception:
                pass
            if temp_copy:
                cleanup_word_temp(temp_copy)


@contextmanager
def open_docx_readwrite(docx_path: str) -> Iterator[tuple[object, str]]:
    """
    Открыть документ для правки через Word.
    Если оригинал уже открыт у пользователя — правим копию и возвращаем
    (doc, path_actually_opened). Вызывающий должен сохранить и при необходимости
    скопировать результат обратно, когда файл освободится.

    Yields: (doc, opened_path)
    """
    abs_path = os.path.abspath(docx_path)
    temp_copy = None
    with word_app(visible=False) as (word, _created):
        path_to_open = abs_path
        used_copy = False
        if word_has_document_open(word, abs_path):
            temp_copy = copy_for_word_com(abs_path)
            path_to_open = temp_copy
            used_copy = True
        doc = word.Documents.Open(path_to_open, ReadOnly=False)
        try:
            yield doc, path_to_open
            try:
                doc.Save()
            except Exception:
                pass
        finally:
            try:
                doc.Close(False)
            except Exception:
                pass
            if used_copy and temp_copy and path_to_open == temp_copy:
                # если правили копию — перенести обратно, если оригинал свободен
                try:
                    if not word_has_document_open(word, abs_path):
                        shutil.copy2(temp_copy, abs_path)
                except Exception:
                    pass
                cleanup_word_temp(temp_copy)
