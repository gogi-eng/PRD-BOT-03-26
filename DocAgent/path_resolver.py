# -*- coding: utf-8 -*-
"""Поиск пути: правка — Агент или Проекты; образец — только Агент + «образец» в имени."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HANDOFF_JSON = ROOT / "handoff" / "request_latest.json"
STATE_DIR = ROOT / "state"
STATE_FILE = STATE_DIR / "last_document.json"

_USER_BASE_N = r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В"
_USER_BASE_UNC = r"\\srv-data\doc\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В"

USER_AGENT_DIR = Path(_USER_BASE_N + r"\Агент")
USER_AGENT_DIR_UNC = Path(_USER_BASE_UNC + r"\Агент")
# На диске папка «Проекты» (мн.ч.); «Проект» тоже принимаем по имени.
USER_PROJECT_DIR = Path(_USER_BASE_N + r"\Проекты")
USER_PROJECT_DIR_UNC = Path(_USER_BASE_UNC + r"\Проекты")
WRITABLE_USER_DIRS: tuple[Path, ...] = (
    USER_AGENT_DIR,
    USER_AGENT_DIR_UNC,
    USER_PROJECT_DIR,
    USER_PROJECT_DIR_UNC,
)
# ОБМЕН / САТП больше не источник образца.
READONLY_SAMPLE_DIRS: tuple[Path, ...] = ()
SAMPLE_NAME_MARK = "образец"
SAMPLE_EXTENSIONS = {".docx"}
DEFAULT_TARGET_NAME = "ПРОЕКТ Старший мастер_оформлен.docx"
DEFAULT_TARGET_PLUS_NAME = "ПРОЕКТ Старший мастер_оформлен+.docx"
_WRITABLE_FOLDER_NAMES = frozenset({"агент", "проекты", "проект"})

DOC_SUFFIXES = {".docx", ".doc", ".rtf"}
CLIPBOARD_PATH = re.compile(
    r'^[A-Za-z]:\\(?:[^\\/:*?"<>|\r\n]+\\)*[^\\/:*?"<>|\r\n]+\.(?:docx|doc|rtf)$',
    re.IGNORECASE,
)


def _normalize(path: str | Path) -> Path:
    return Path(str(path).strip().strip('"').replace("/", "\\"))


def canonical_fs_path(path: str | Path) -> Path:
    """Единый вид пути: «/» → «\\», при возможности resolve()."""
    text = normalize_sniot_path_text(str(path).strip().strip('"'))
    p = Path(text)
    try:
        return p.resolve()
    except OSError:
        return Path(os.path.normpath(str(p)))


def paths_are_same_file(src: str | Path, dst: str | Path) -> bool:
    """True, если «N:/a.docx» и «N:\\a.docx» — один файл (иначе shutil.SameFileError)."""
    if not src or not dst:
        return False
    a = Path(str(src).strip().strip('"').replace("/", "\\"))
    b = Path(str(dst).strip().strip('"').replace("/", "\\"))
    try:
        if a.exists() and b.exists():
            return os.path.samefile(a, b)
    except OSError:
        pass
    try:
        na = os.path.normcase(str(a.resolve()))
        nb = os.path.normcase(str(b.resolve()))
        return na == nb
    except OSError:
        return os.path.normcase(os.path.abspath(str(a))) == os.path.normcase(
            os.path.abspath(str(b))
        )


def copy_file_if_different(src: str | Path, dst: str | Path) -> bool:
    """
    copy2, но если источник == приёмник — не копировать и не падать.
    True — скопировали; False — тот же файл, пропуск.
    """
    if paths_are_same_file(src, dst):
        return False
    try:
        shutil.copy2(src, dst)
    except shutil.SameFileError:
        return False
    return True


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


def _canonical_agent_n_path() -> Path:
    return Path(
        normalize_sniot_path_text(
            r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В\Агент"
        )
    )


def _is_canonical_user_agent_dir() -> bool:
    """True, если USER_AGENT_DIR — настоящая папка Агент (не tmp в тестах)."""
    current = Path(normalize_sniot_path_text(str(USER_AGENT_DIR)))
    try:
        return os.path.normcase(str(current)) == os.path.normcase(
            str(_canonical_agent_n_path())
        )
    except OSError:
        return False


def _agent_dir_try_list() -> list[Path]:
    folders = [
        Path(normalize_sniot_path_text(str(USER_AGENT_DIR))),
        USER_AGENT_DIR,
    ]
    if _is_canonical_user_agent_dir():
        folders.append(Path(normalize_sniot_path_text(str(USER_AGENT_DIR_UNC))))
        folders.append(USER_AGENT_DIR_UNC)
    unique: list[Path] = []
    seen: set[str] = set()
    for folder in folders:
        key = os.path.normcase(str(folder))
        if key in seen:
            continue
        seen.add(key)
        unique.append(folder)
    return unique


def live_user_agent_dir() -> Path | None:
    """Существующая папка Агент: N:\\ (кириллица «и») или UNC. Не ОБМЕН, не обход N:\\."""
    for folder in _agent_dir_try_list():
        try:
            if folder.is_dir():
                return folder
        except OSError:
            continue
    return None


def user_agent_dir_unavailable_hint() -> str:
    return (
        "Папка Агент недоступна (N:\\…\\Агент или \\\\srv-data\\doc\\…\\Агент). "
        "Список образцов пуст; файлы из ОБМЕН не подставляются."
    )


def _membership_bases(dirs: tuple[Path, ...] | list[Path]) -> list[Path]:
    bases: list[Path] = []
    seen: set[str] = set()
    for raw in dirs:
        try:
            resolved = Path(normalize_sniot_path_text(str(raw))).resolve()
        except OSError:
            resolved = Path(os.path.normpath(normalize_sniot_path_text(str(raw))))
        key = os.path.normcase(str(resolved))
        if key in seen:
            continue
        seen.add(key)
        bases.append(resolved)
    return bases


def _path_parts_folded(path: Path | str) -> list[str]:
    return [p.replace("ё", "е").casefold() for p in Path(str(path)).parts]


def _looks_like_writable_user_dir(path: Path | str) -> bool:
    """N: и UNC: Агент или Проекты под «Дубовик …». ОБМЕН / САТП — нет."""
    parts = _path_parts_folded(path)
    if any("обмен" in p for p in parts):
        return False
    for i, part in enumerate(parts):
        if part in _WRITABLE_FOLDER_NAMES and i >= 1 and "дубовик" in parts[i - 1]:
            return True
    return False


def _is_under_bases(path: Path | str, bases: list[Path]) -> bool:
    try:
        text = normalize_sniot_path_text(str(path))
        resolved = Path(text).resolve()
        for base in bases:
            try:
                if resolved == base or resolved.is_relative_to(base):
                    return True
            except (OSError, ValueError):
                continue
            a = os.path.normcase(str(resolved))
            b = os.path.normcase(str(base))
            if a == b or a.startswith(b.rstrip("\\") + "\\"):
                return True
        return False
    except (OSError, ValueError, TypeError):
        return False


def _agent_membership_bases() -> list[Path]:
    return _membership_bases(_agent_dir_try_list())


def _writable_membership_bases() -> list[Path]:
    dirs: list[Path] = list(_agent_dir_try_list())
    if _is_canonical_user_agent_dir():
        dirs.extend(
            [
                Path(normalize_sniot_path_text(str(USER_PROJECT_DIR))),
                USER_PROJECT_DIR,
                Path(normalize_sniot_path_text(str(USER_PROJECT_DIR_UNC))),
                USER_PROJECT_DIR_UNC,
            ]
        )
    return _membership_bases(dirs)


def is_path_in_user_agent_dir(path: Path | str) -> bool:
    """Путь внутри папки Агент (образцы, умолчания). N:\\ и UNC — один каталог."""
    try:
        text = normalize_sniot_path_text(str(path))
        parts = _path_parts_folded(text)
        if any("обмен" in p for p in parts):
            return False
        for i, part in enumerate(parts):
            if part == "агент" and i >= 1 and "дубовик" in parts[i - 1]:
                return True
        return _is_under_bases(text, _agent_membership_bases())
    except (OSError, ValueError, TypeError):
        return False


def is_path_in_writable_user_dir(path: Path | str) -> bool:
    """Путь в папке Агент или Проекты — зона записи делопроизводителя."""
    try:
        text = normalize_sniot_path_text(str(path))
        if _looks_like_writable_user_dir(text):
            return True
        return _is_under_bases(text, _writable_membership_bases())
    except (OSError, ValueError, TypeError):
        return False


def writable_dirs_hint() -> str:
    return f"{USER_AGENT_DIR}\nили\n{USER_PROJECT_DIR}"


def is_path_readonly_sample(path: Path | str) -> bool:
    """ОБМЕН больше не эталон. Оставлено для совместимости: всегда False."""
    return False


def filename_has_sample_mark(name: str) -> bool:
    """В имени файла есть подстрока «образец» (регистр не важен)."""
    return SAMPLE_NAME_MARK in (name or "").casefold()


def is_allowed_sample_path(path: Path | str) -> bool:
    """
    Эталон оформления: только папка Агент, в имени «образец», расширение .docx.
    *_оформлен.docx без «образец» — False. ОБМЕН / САТП — False.
    """
    try:
        text = normalize_sniot_path_text(str(path))
        p = Path(text)
        name = p.name
        if name.startswith("~$"):
            return False
        if p.suffix.lower() not in SAMPLE_EXTENSIONS:
            return False
        if not filename_has_sample_mark(name):
            return False
        stem_l = p.stem.casefold()
        if stem_l.endswith("_оформлен") or stem_l.endswith("_оформлен+"):
            return False
        return is_path_in_user_agent_dir(p)
    except (OSError, ValueError, TypeError):
        return False


def list_agent_sample_paths() -> list[Path]:
    """Все *образец*.docx только в папке Агент (N:\\ или UNC). Без обхода N:\\ и ОБМЕН."""
    folder = live_user_agent_dir()
    if folder is None:
        return []
    found: list[Path] = []
    try:
        for item in folder.iterdir():
            if item.is_file() and is_allowed_sample_path(item):
                found.append(item)
    except OSError:
        return []
    return sorted(found, key=lambda item: item.name.casefold())


def _sample_rank(target: Path, sample: Path) -> tuple[int, int]:
    target_base = re.sub(r"_оформлен\+?$", "", target.stem, flags=re.IGNORECASE)
    target_base = re.sub(re.escape(SAMPLE_NAME_MARK), "", target_base, flags=re.IGNORECASE)
    target_base = target_base.strip(" _").casefold()
    sample_core = re.sub(re.escape(SAMPLE_NAME_MARK), "", sample.stem, flags=re.IGNORECASE)
    sample_core = sample_core.strip(" _").casefold()
    exact = 1 if sample_core == target_base else 0
    t_tokens = set(re.findall(r"[а-яёa-z0-9]+", target_base, flags=re.IGNORECASE))
    s_tokens = set(re.findall(r"[а-яёa-z0-9]+", sample_core, flags=re.IGNORECASE))
    return (exact, len(t_tokens & s_tokens))


def pick_best_agent_sample(target: Path | str | None = None) -> Path | None:
    """Близкое по имени к целевому; иначе любой *образец*.docx из папки Агент."""
    samples = list_agent_sample_paths()
    if not samples:
        return None
    if target is None:
        return samples[0]
    target_path = Path(normalize_sniot_path_text(str(target)))
    ranked = sorted(
        samples,
        key=lambda sample: (_sample_rank(target_path, sample), sample.name.casefold()),
        reverse=True,
    )
    return ranked[0]


def assert_path_writable(path: Path | str) -> Path:
    """Guard: запись только в папку Агент или Проекты."""
    p = Path(normalize_sniot_path_text(str(path)))
    if not is_path_in_writable_user_dir(p):
        raise PermissionError(
            f"Запись запрещена вне папок Агент/Проекты: {p}\n"
            f"Разрешено:\n{writable_dirs_hint()}\n"
            "Образец — только файл со словом «образец» в папке Агент."
        )
    return p.resolve()


def resolve_etalon_path(
    explicit: str | Path | None = None,
    target: str | Path | None = None,
) -> tuple[Path | None, str]:
    """
    Образец: только папка Агент и имя со словом «образец».
    Явный путь вне Агент или без «образец» — игнорировать и искать в Агент.
    Нет файла — (None, пояснение), без ОБМЕН.
    """
    if explicit:
        found = _exists_doc(explicit)
        if found and is_allowed_sample_path(found):
            return found, "образец из папки Агент"
    found = pick_best_agent_sample(target)
    if found:
        return found, "образец из папки Агент"
    return None, "нет файла «образец» в папке Агент — оформление по правилам mdc"


def get_active_word_document() -> Path | None:
    """Word — документ из папки Агент или Проекты."""
    try:
        import win32com.client

        word = win32com.client.GetActiveObject("Word.Application")
        if int(word.Documents.Count) < 1:
            return None
        full_name = str(word.ActiveDocument.FullName or "").strip()
        if not full_name or full_name.lower().startswith("unsaved"):
            return None
        found = _exists_doc(full_name)
        if found and is_path_in_writable_user_dir(found):
            return found
    except Exception:
        pass
    return None


def get_handoff_path() -> Path | None:
    """Handoff — только если source_path в папке Агент или Проекты."""
    if not HANDOFF_JSON.is_file():
        return None
    try:
        data = json.loads(HANDOFF_JSON.read_text(encoding="utf-8"))
        found = _exists_doc(data.get("source_path", ""))
        if found and is_path_in_writable_user_dir(found):
            return found
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def get_clipboard_path() -> Path | None:
    """Буфер — только если файл в папке Агент или Проекты."""
    try:
        import tkinter as tk

        root = tk.Tk()
        root.withdraw()
        text = root.clipboard_get().strip().strip('"')
        root.destroy()
        if CLIPBOARD_PATH.match(text):
            found = _exists_doc(text)
            if found and is_path_in_writable_user_dir(found):
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
        if found and is_path_in_writable_user_dir(found):
            return found
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return None


def save_last_used_path(path: str | Path) -> None:
    p = _exists_doc(path)
    if p is None or not is_path_in_writable_user_dir(p):
        return
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps({"path": str(p)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def find_user_agent_default_di() -> Path | None:
    """Файл по умолчанию в папке Агент."""
    folder = live_user_agent_dir()
    if folder is None:
        return None
    preferred = folder / DEFAULT_TARGET_NAME
    if preferred.is_file():
        return preferred
    plus = folder / DEFAULT_TARGET_PLUS_NAME
    if plus.is_file():
        return plus
    matches = sorted(
        (
            f
            for f in folder.glob("*оформлен*.docx")
            if "_backup_" not in f.name.lower()
        ),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    return matches[0] if matches else None


def is_sniot_doc(path: Path) -> bool:
    """Документ СНиОТ по пути/имени (тип файла). Образец для оформления — отдельно, is_allowed_sample_path."""
    if is_path_in_writable_user_dir(path):
        return True
    text = normalize_sniot_path_text(str(path)).lower()
    if not text.endswith((".docx", ".doc", ".rtf")):
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
    Документ для **правки** — папка Агент или Проекты.
    Образец — через resolve_etalon_path (только Агент + «образец» в имени).
    """
    if explicit:
        found = _exists_doc(explicit)
        if found and is_path_in_writable_user_dir(found):
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

    return None, "нет документа в папке Агент или Проекты"
