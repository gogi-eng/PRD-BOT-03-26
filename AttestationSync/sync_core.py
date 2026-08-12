# -*- coding: utf-8 -*-
"""Общая логика рассылки файлов аттестации / ОПО в рабочие папки."""

from __future__ import annotations

import os
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
DESKTOP = Path.home() / "Desktop"

DEST_DIRS = [
    Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В"),
    Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Начальник"),
    Path(r"C:\Users\v.dubovik\Desktop\ПромБез\ПромБез"),
    Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность"),
    Path(r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Протько"),
]


@dataclass(frozen=True)
class SyncJob:
    """Описание одного файла для рассылки."""

    log_prefix: str
    title_ok: str
    title_err: str
    title_fail: str
    shortcut_name_part: str
    default_sources: tuple[Path, ...]
    source_not_found_hint: str


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LOG_DIR / "sync.log", "a", encoding="utf-8") as f:
        f.write(line)


def msgbox(title: str, text: str, icon: int = 0x40) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(0, text, title, icon)
    except Exception:
        print(title)
        print(text)


def find_desktop_shortcut(name_part: str) -> Path | None:
    if not DESKTOP.is_dir():
        return None
    part = name_part.lower().replace("ё", "е")
    for p in DESKTOP.iterdir():
        if p.suffix.lower() != ".lnk":
            continue
        if part in p.name.lower().replace("ё", "е"):
            return p
    return None


def resolve_shortcut(lnk: Path) -> Path | None:
    try:
        import win32com.client  # type: ignore

        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(lnk))
        target = (sc.TargetPath or "").strip()
        if target and os.path.isfile(target):
            return Path(target)
    except Exception as e:
        _log(f"shortcut resolve fail: {e}")
    return None


def find_source(job: SyncJob) -> Path:
    lnk = find_desktop_shortcut(job.shortcut_name_part)
    if lnk:
        tgt = resolve_shortcut(lnk)
        if tgt is not None:
            return tgt
    for candidate in job.default_sources:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(job.source_not_found_hint)


def same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))
    except OSError:
        return os.path.normcase(str(a)) == os.path.normcase(str(b))


def copy_one(src: Path, dest_dir: Path) -> tuple[str, str]:
    if not dest_dir.is_dir():
        return "error", f"нет папки: {dest_dir}"

    dest = dest_dir / src.name
    if same_file(src, dest):
        return "skip", "это и есть исходный файл (уже сохранён здесь)"

    tmp = dest_dir / f"~sync_{os.getpid()}_{src.name}.tmp"
    try:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        shutil.copy2(src, tmp)
        os.replace(tmp, dest)
        size = dest.stat().st_size
        return "ok", f"скопировано ({size} байт)"
    except PermissionError:
        return (
            "error",
            "файл занят (закройте его в Excel в этой папке и повторите)",
        )
    except OSError as e:
        return "error", str(e)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def sync_job(job: SyncJob) -> int:
    try:
        src = find_source(job)
    except Exception as e:
        msgbox(job.title_fail, str(e), icon=0x10)
        _log(f"{job.log_prefix} source error: {e}")
        return 1

    _log(f"{job.log_prefix} START source={src}")
    lines = [
        f"Исходник:\n{src}\n",
        f"Изменён: {datetime.fromtimestamp(src.stat().st_mtime):%d.%m.%Y %H:%M:%S}",
        f"Размер: {src.stat().st_size} байт\n",
        "Результаты:",
    ]
    ok_n = skip_n = err_n = 0
    for dest_dir in DEST_DIRS:
        status, detail = copy_one(src, dest_dir)
        folder = dest_dir.name
        if status == "ok":
            ok_n += 1
            mark = "[ОК]"
        elif status == "skip":
            skip_n += 1
            mark = "[—]"
        else:
            err_n += 1
            mark = "[Ошибка]"
        lines.append(f"{mark} {folder}: {detail}")
        _log(f"{job.log_prefix} {status}: {dest_dir} — {detail}")

    lines.append("")
    lines.append(f"Успешно: {ok_n}  |  без копирования: {skip_n}  |  ошибок: {err_n}")
    if err_n:
        lines.append(
            "\nЕсли ошибка «файл занят» — закройте копию в Excel "
            "в этой папке и нажмите ярлык ещё раз."
        )

    text = "\n".join(lines)
    title = job.title_ok if err_n == 0 else job.title_err
    msgbox(title, text, icon=0x40 if err_n == 0 else 0x30)
    _log(f"{job.log_prefix} DONE ok={ok_n} skip={skip_n} err={err_n}")
    return 0 if err_n == 0 else 2


def run_main(job: SyncJob) -> int:
    try:
        return sync_job(job)
    except Exception:
        tb = traceback.format_exc()
        _log(f"{job.log_prefix} crash:\n{tb}")
        msgbox(job.title_fail, tb[:1000], icon=0x10)
        return 1
