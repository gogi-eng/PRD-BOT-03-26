# -*- coding: utf-8 -*-
"""Запуск fix_sniot_document.py из DocAgent — без Cursor и без ручного ввода пути."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIX_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_sniot_document.py")
LEGACY_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_senior_master_di.py")
PYTHON = Path(r"C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe")


def _python_exe() -> str:
    if PYTHON.is_file():
        return str(PYTHON)
    return sys.executable


def _script_path() -> Path:
    return FIX_SCRIPT if FIX_SCRIPT.is_file() else LEGACY_SCRIPT


def run_sniot_fix(
    target: Path | None = None,
    *,
    check_only: bool = False,
    fix_page_breaks: bool = False,
    use_handoff: bool = False,
) -> tuple[int, str]:
    """Запустить скрипт правки документа СНиОТ. Код выхода + текст вывода."""
    script = _script_path()
    if not script.is_file():
        return 3, f"Не найден скрипт:\n{script}"

    cmd = [_python_exe(), str(script)]
    if use_handoff or target is None:
        cmd.append("--handoff")
    else:
        cmd.extend(["--target", str(target)])
    if check_only:
        cmd.append("--check")
    if fix_page_breaks:
        cmd.append("--fix-page-breaks")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(script.parent),
    )
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, output.strip()


run_satp_di_fix = run_sniot_fix


def format_result(code: int, output: str) -> str:
    if code == 0:
        if "Validation: OK" in output or output.endswith("OK"):
            return "Готово. Документ соответствует правилам.\n\n" + output
        if "уже соответствует" in output:
            return "Изменений не требовалось.\n\n" + output
        return "Готово.\n\n" + output
    if code == 2:
        return "Закройте файл в Word и нажмите снова.\n\n" + output
    if code == 3:
        return "Файл не найден.\n\n" + output
    return "Есть замечания после правки.\n\n" + output


def _safe_print(text: str) -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    print(text)


def main_cli() -> int:
    """Запуск из .bat без окна агента."""
    import argparse

    from path_resolver import resolve_document_path, save_last_used_path

    parser = argparse.ArgumentParser(description="Исправить документ СНиОТ по правилам")
    parser.add_argument("--target", type=Path, default=None)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--gui", action="store_true", help="Показать окно с результатом")
    parser.add_argument(
        "--fallback",
        action="store_true",
        help="Если нет handoff — искать Word/буфер (только папка Агент) или файл по умолчанию",
    )
    args = parser.parse_args()

    target = args.target
    source_label = ""
    if target is None:
        found, source = resolve_document_path(allow_fallbacks=args.fallback)
        if found:
            target = found
            source_label = source
            print(f"Файл: {found} ({source})")
        else:
            _safe_print(
                "Не найден документ в папке Агент. Укажите --target, положите путь в handoff "
                "(request_latest.json → source_path в папке Агент) или добавьте --fallback."
            )
            return 3

    code, output = run_sniot_fix(target, check_only=args.check)
    text = format_result(code, output)
    if source_label:
        text = f"Источник: {source_label}\n\n{text}"
    _safe_print(text)

    if args.gui:
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            if code == 0:
                messagebox.showinfo("СНиОТ", text)
            elif code == 2:
                messagebox.showwarning("Закройте Word", text)
            else:
                messagebox.showerror("СНиОТ", text)
            root.destroy()
        except Exception:
            pass

    if code == 0 and target:
        save_last_used_path(target)
    return code


if __name__ == "__main__":
    raise SystemExit(main_cli())
