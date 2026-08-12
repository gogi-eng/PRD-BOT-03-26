# -*- coding: utf-8 -*-
"""Поиск пути к документу: правка — только Агент; ОБМЕН — образцы read-only."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HANDOFF_JSON = ROOT / "handoff" / "request_latest.json"
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "last_document.json"

USER_AGENT_DIR = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНiОТ)\Дубовик В.В\Агент"
)
READONLY_SAMPLE_DIRS: tuple[Path, ...] = (
    Path(
        r"N:\9 - Служба надёжности и охраны труда (СНiОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ"
    ),
)
DEFAULT_TARGET_NAME = "ПРОЕКТ Старший мастер_оформлен.docx"
DEFAULT_TARGET_PLUS_NAME = "ПРОЕКТ Старший мастер_оформлен+.docx"

DOC_SUFFIXES = {".docx", ".doc", ".rtf"}
CLIPBOARD_PATH = re.compile(
    r'^[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]+\.(?:docx|doc|rtf)$',
    re.IGNORECASE,
)


def _normalize(path: str | Path) -> Path:
    return Path(str(path).strip().strip('"').replace("/", "\\"))


def _exists_doc(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        p = _normalize(path)
        if p.is_file() and p.suffix.lower() in DOC_SUFFIXES:
            return p
    except OSError:
        pass
    return None


def normalize_sniot_path_text(path: str) -> str:
    """Латинская «i» в «СНiОТ» → кириллическая «и»."""
    text = str(path).replace("/", "\\")
    text = re.sub(r"СН[iI]ОТ", "СНиОТ", text, flags=re.IGNORECASE)
    return text


def is_path_in_user_agent_dir(path: Path | str) -> bool:
    """Путь внутри папки Агент — единственная зона записи."""
    try:
        text = normalize_sniot_path_text(str(path))
        resolved = Path(text).resolve()
        base = Path(normalize_sniot_path_text(str(USER_AGENT_DIR))).resolve()
        return resolved.is_relative_to(base)
    except (OSError, ValueError, TypeError):
        return False


def is_path_readonly_sample(path: Path | str) -> bool:
    """Путь к образцу в ОБМЕН — только чтение."""
    try:
        text = normalize_sniot_path_text(str(path))
        resolved = Path(text).resolve()
        for root in READONLY_SAMPLE_DIRS:
            base = Path(normalize_sniot_path_text(str(root))).resolve()
            if resolved.is_relative_to(base):
                return True
    except (OSError, ValueError, TypeError):
        pass
    return False


def assert_path_writable(path: Path | str) -> Path:
    """Guard: запись только в USER_AGENT_DIR."""
    p = Path(normalize_sniot_path_text(str(path)))
    if not is_path_in_user_agent_dir(p):
        raise PermissionError(
            f"Запись запрещена вне папки Агент: {p}\n"
            f"Разрешено только: {USER_AGENT_DIR}\n"
            "Образцы из ОБМЕН — только для чтения."
        )
    return p.resolve()


def resolve_etalon_path(explicit: str | Path | None = None) -> tuple[Path | None, str]:
    """
    Путь к образцу (read-only). Явный путь → проверка READONLY_SAMPLE_DIRS.
    Без batch-scan.
    """
    if not explicit:
        return None, ""
    found = _exists_doc(explicit)
    if found and is_path_readonly_sample(found):
        return found, "образец ОБМЕН (read-only)"
    if found and is_path_in_user_agent_dir(found):
        return found, "файл в Агент (не эталон ОБМЕН)"
    return None, ""


def get_active_word_document() -> Path | None:
    """Word — только документ из папки Агент."""
    try:
        import win32com.client

        word = win32com.client.GetActiveObject("Word.Application")
        if int(word.Documents.Count) < 1:
            return None
        full_name = str(word.ActiveDocument.FullName or "").strip()
        if not full_name or full_name.lower().startswith("unsaved"):
            return None
        found = _exists_doc(full_name)
        if found and is_path_in_user_agent_dir(found):
            return found
    except Exception:
        pass
    return None


def get_handoff_path() -> Path | None:
    """Handoff — только если source_path в папке Агент."""
    if not HANDOFF_JSON.is_file():
        return None
    try:
        data = json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))
        found = _exists_doc(data.get("source_path", ""))
        if found and is_path_in_user_agent_dir(found):
            return found
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def get_clipboard_path() -> Path | None:
    """Буфер — только если файл в папке Агент."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get().strip().strip('"')
        root.destroy()
        if CLIPBOARD_PATH.match(text):
            found = _exists_doc(text)
            if found and is_path_in_user_agent_dir(found):
                return found
    except Exception:
        pass
    return None


def get_last_used_path() -> Path | None:
    if not STATE_FILE.is_file():
        return None
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        found = _exists_doc(data.get("path", ""))
        if found and is_path_in_user_agent_dir(found):
            return found
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def save_last_used_path(path: str | Path) -> None:
    p = _exists_doc(path)
    if p is None or not is_path_in_user_agent_dir(p):
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"path": str(p)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_user_agent_default_di() -> Path | None:
    """Файл по умолчанию в папке Агент."""
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
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def is_sniot_doc(path: Path) -> bool:
    """Документ из Агент, ОБМЕН или дерево СНиОТ на N:\\."""
    if is_path_in_user_agent_dir(path):
        return True
    if is_path_readonly_sample(path):
        return True
    text = normalize_sniot_path_text(str(path)).lower()
    if not text.endswith(".docx"):
        return False
    if "сниот" in text:
        return True
    name = Path(text).name.lower()
    return any(k in name for k in ("инструкц", "положен", "должност", "рабоч", "проект"))


is_satp_di = is_sniot_doc


def resolve_document_path(
    explicit: str | Path | None = None,
    *,
    allow_fallbacks: bool = False,
) -> tuple[Path | None, str]:
    """
    Документ для **правки** — только папка Агент.
    Образцы ОБМЕН — через resolve_etalon_path (read-only).
    """
    if explicit:
        found = _exists_doc(explicit)
        if found and is_path_in_user_agent_dir(found):
            return found, "поле «1. Документ»"

    found = get_handoff_path()
    if found:
        return found, "задание handoff (таблица агента)"

    found = get_last_used_path()
    if found:
        return found, "последний документ"

    if allow_fallbacks:
        for getter, label in (
            (get_active_word_document, "открытый документ Word"),
            (get_clipboard_path, "буфер обмена"),
        ):
            found = getter()
            if found:
                return found, label

    found = find_user_agent_default_di()
    if found:
        return found, "файл по умолчанию в папке Агент"

    return None, ""
