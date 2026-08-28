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

import json
import os
import shutil
import subprocess
import sys
import tempfile
import winreg
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from path_resolver import copy_file_if_different

_MISSING = object()
MSO_AUTOMATION_SECURITY_FORCE_DISABLE = 3
WD_ALERTS_NONE = 0
WD_FORMAT_XML_DOCUMENT = 16
_WORD_FILEVALIDATION_VERSIONS = ("14.0", "15.0", "16.0")
_FILEBLOCK_VALUES = ("Word97Files", "Word95Files", "Word60Files", "OpenInProtectedView")
_PROTECTED_VIEW_VALUES = (
    "DisableInternetFilesInPV",
    "DisableUnsafeLocationsInPV",
    "DisableAttachmentsInPV",
)


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


def _docx_paths_match(full_name: str, target: str) -> bool:
    """UNC и N:\\ — один файл; сравнивать и по имени в папке Агент."""
    if not full_name or not target:
        return False
    try:
        from path_resolver import paths_are_same_file

        if paths_are_same_file(full_name, target):
            return True
    except Exception:
        pass
    a = os.path.normcase(os.path.normpath(full_name))
    b = os.path.normcase(os.path.normpath(target))
    if a == b:
        return True
    pa, pb = Path(full_name), Path(target)
    if pa.name.casefold() == pb.name.casefold():
        if "агент" in a and "агент" in b:
            return True
    return False


def try_close_open_document(path: str | Path) -> dict:
    """
    Если этот файл открыт в Word — сохранить и закрыть ТОЛЬКО его.
    Word.Application не закрывать, другие документы не трогать.
    """
    result = {"was_open": False, "closed": False, "message": ""}
    target = str(path)
    try:
        import pythoncom
        import win32com.client
    except ImportError:
        result["message"] = "Нет win32com — закройте документ в Word вручную"
        return result
    pythoncom.CoInitialize()
    try:
        try:
            word = win32com.client.GetActiveObject("Word.Application")
        except Exception:
            result["message"] = "Word не запущен"
            return result
        matches = []
        try:
            count = int(word.Documents.Count)
        except Exception:
            count = 0
        for i in range(1, count + 1):
            try:
                doc = word.Documents(i)
                full = str(doc.FullName or "")
            except Exception:
                continue
            if _docx_paths_match(full, target):
                matches.append(doc)
        if not matches:
            result["message"] = "Этот файл в Word не открыт"
            return result
        result["was_open"] = True
        for doc in matches:
            try:
                doc.Save()
            except Exception:
                pass
            try:
                doc.Close(False)
            except Exception as exc:
                result["closed"] = False
                result["message"] = (
                    f"Word не отдал файл: {exc}. Закройте «_оформлен.docx» вручную."
                )
                return result
        result["closed"] = True
        result["message"] = "Документ закрыт в Word (окно Word оставлено)"
        return result
    except Exception as exc:
        result["message"] = f"Не удалось закрыть в Word: {exc}"
        return result
    finally:
        try:
            pythoncom.CoUninitialize()
        except Exception:
            pass


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
    copy_file_if_different(src, dst)
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
                        copy_file_if_different(temp_copy, abs_path)
                except Exception:
                    pass
                cleanup_word_temp(temp_copy)


LEGACY_WORD_EXTENSIONS = (".doc", ".rtf")


def is_legacy_word_file(path: str | Path) -> bool:
    """Старый Word: .doc или .rtf — перед оформлением нужно превратить в .docx."""
    return Path(path).suffix.lower() in LEGACY_WORD_EXTENSIONS


def converted_docx_path_for(src: str | Path) -> Path:
    """Имя результата конвертации: тот же каталог, суффикс _converted.docx."""
    src_p = Path(src)
    return src_p.with_name(f"{src_p.stem}_converted.docx")


def unblock_windows_file(path: str | Path) -> None:
    """Снять метку «из интернета» (Zone.Identifier), из‑за неё Word блокирует файл."""
    ads = f"{os.fspath(path)}:Zone.Identifier"
    try:
        os.remove(ads)
    except OSError:
        pass


def copy_binary_unblocked(src: str | Path, dst: str | Path) -> Path:
    """Копия байтами — без ADS/Zone.Identifier (shutil.copy2 их часто переносит)."""
    src_p = Path(src)
    dst_p = Path(dst)
    dst_p.parent.mkdir(parents=True, exist_ok=True)
    dst_p.write_bytes(src_p.read_bytes())
    unblock_windows_file(dst_p)
    return dst_p


def _reg_set_dword(path: str, name: str, value: int, saved: list) -> None:
    try:
        key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        return
    try:
        try:
            old, typ = winreg.QueryValueEx(key, name)
        except OSError:
            old, typ = _MISSING, None
        winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
        saved.append((path, name, old, typ))
    finally:
        winreg.CloseKey(key)


def _reg_restore(saved: list) -> None:
    for path, name, old, typ in reversed(saved):
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_SET_VALUE
            )
        except OSError:
            continue
        try:
            if old is _MISSING:
                try:
                    winreg.DeleteValue(key, name)
                except OSError:
                    pass
            else:
                winreg.SetValueEx(
                    key, name, 0, int(typ or winreg.REG_DWORD), old
                )
        finally:
            winreg.CloseKey(key)


@contextmanager
def word_file_validation_disabled() -> Iterator[None]:
    """
    На время конвертации ослабить блокировку старых .doc в Word:
    File Validation, File Block (Word 97) и Protected View.
    Значения реестра после конвертации возвращаются как были.
    """
    saved: list = []
    try:
        for ver in _WORD_FILEVALIDATION_VERSIONS:
            sec = rf"Software\Microsoft\Office\{ver}\Word\Security"
            _reg_set_dword(f"{sec}\\FileValidation", "EnableOnLoad", 0, saved)
            for name in _FILEBLOCK_VALUES:
                _reg_set_dword(f"{sec}\\FileBlock", name, 0, saved)
            for name in _PROTECTED_VIEW_VALUES:
                _reg_set_dword(f"{sec}\\ProtectedView", name, 1, saved)
        yield
    finally:
        _reg_restore(saved)


def convert_legacy_word_to_docx(src: str | Path, dst: str | Path) -> Path:
    """
    .doc / .rtf → .docx через отдельный скрытый Word (DispatchEx).
    Копия на локальный диск без Zone.Identifier, OpenAndRepair, без File Validation.
    """
    import win32com.client  # type: ignore

    src_p = Path(src)
    dst_p = Path(dst)
    ext = src_p.suffix.lower()
    if ext not in LEGACY_WORD_EXTENSIONS:
        raise RuntimeError(f"Ожидался файл .doc или .rtf, получен: {src_p}")
    if not src_p.is_file():
        raise RuntimeError(f"Файл не найден:\n{src_p}")

    td = Path(tempfile.mkdtemp(prefix="docagent_conv_"))
    local_src = td / f"source{ext}"
    local_out = td / "converted.docx"
    word = None
    doc = None
    try:
        copy_binary_unblocked(src_p, local_src)
        with word_file_validation_disabled():
            word = win32com.client.DispatchEx("Word.Application")
            word.Visible = False
            try:
                word.DisplayAlerts = WD_ALERTS_NONE
            except Exception:
                pass
            try:
                word.AutomationSecurity = MSO_AUTOMATION_SECURITY_FORCE_DISABLE
            except Exception:
                pass
            local_name = str(local_src.resolve())
            # Сначала простой Open: лишние именованные аргументы (Visible/Revert)
            # у Documents.Open дают «Ошибка в Word» 24577.
            try:
                doc = word.Documents.Open(local_name, False, False, False)
            except Exception:
                fmt = 6 if ext == ".rtf" else 1
                doc = word.Documents.Open(
                    local_name,
                    False,
                    False,
                    False,
                    "",
                    "",
                    False,
                    "",
                    "",
                    fmt,
                    0,
                    False,
                    True,
                )
            if doc is None:
                raise RuntimeError(
                    "Word не смог открыть файл (Documents.Open вернул пусто)."
                )
            doc.SaveAs(str(local_out), FileFormat=WD_FORMAT_XML_DOCUMENT)
            doc.Close(False)
            doc = None
        if not local_out.is_file():
            raise RuntimeError("Word не создал временный .docx")
        dst_p.parent.mkdir(parents=True, exist_ok=True)
        dst_p.write_bytes(local_out.read_bytes())
        unblock_windows_file(dst_p)
        return dst_p
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        shutil.rmtree(td, ignore_errors=True)


FIX_SNIOT_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_sniot_document.py")


def apply_word_grammar_check(docx_path: str) -> dict:
    """
    Красная волнистая черта Word = орфография: SpellingErrors + GetSpellingSuggestions.
    Аббревиатуры не менять. Грамматика — CheckGrammar. Файл на диске сохраняется.
    Если Word занят — предупреждение в dict, без исключения наружу.
    """
    script = FIX_SNIOT_SCRIPT
    if not script.is_file():
        return {
            "ok": False,
            "available": False,
            "applied": False,
            "message": f"Word: скрипт не найден — {script}",
            "error": "no_script",
        }
    try:
        proc = subprocess.run(
            [
                sys.executable,
                str(script),
                "--word-grammar-check",
                str(Path(docx_path).resolve()),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
        )
    except subprocess.TimeoutExpired as expired:
        pid = getattr(expired, "pid", None) or getattr(getattr(expired, "process", None), "pid", None)
        if pid:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True,
                    timeout=8,
                    check=False,
                )
            except Exception:
                pass
        return {
            "ok": False,
            "available": False,
            "applied": False,
            "message": "Word: проверка орфографии прервана (таймаут 25 с)",
            "error": "timeout",
        }
    except Exception as exc:
        err = str(exc).strip() or type(exc).__name__
        low = err.lower()
        if "busy" in low or "заблокир" in low or "rpc" in low:
            return {
                "ok": False,
                "available": False,
                "applied": False,
                "message": "Word: не удалось проверить орфографию — закройте документ в Word и повторите",
                "error": err,
            }
        return {
            "ok": False,
            "available": False,
            "applied": False,
            "message": f"Word: ошибка орфографии — {err[:180]}",
            "error": err,
        }
    raw = (proc.stdout or "").strip()
    if not raw:
        err = (proc.stderr or "").strip()[:180]
        return {
            "ok": False,
            "available": False,
            "applied": False,
            "message": f"Word: нет ответа орфографии{(' — ' + err) if err else ''}",
            "error": "empty",
        }
    try:
        data = json.loads(raw.splitlines()[-1])
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    return {
        "ok": False,
        "available": False,
        "applied": False,
        "message": f"Word: неразобранный ответ орфографии — {raw[:160]}",
        "error": "bad_json",
    }
