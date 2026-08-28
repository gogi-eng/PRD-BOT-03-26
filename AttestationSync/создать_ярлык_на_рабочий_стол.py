# -*- coding: utf-8 -*-
"""Создать / обновить ярлыки рассылки на Рабочем столе."""

from pathlib import Path

import win32com.client

ROOT = Path(__file__).resolve().parent
DESKTOP = Path.home() / "Desktop"
ICO = ROOT / "attestaciya_icon.ico"

SHORTCUTS = [
    (
        DESKTOP / "Разослать аттестацию ПрБ.lnk",
        ROOT / "Разослать_аттестацию.bat",
        "Скопировать аттестацию ПрБ МКТС в 5 папок после сохранения",
    ),
    (
        DESKTOP / "Разослать ОПО и ПОО.lnk",
        ROOT / "Разослать_ОПО_ПОО.bat",
        "Скопировать файл ОПО и ПОО в 5 папок после сохранения",
    ),
]


def main() -> None:
    shell = win32com.client.Dispatch("WScript.Shell")
    for lnk_path, bat_path, description in SHORTCUTS:
        sc = shell.CreateShortcut(str(lnk_path))
        sc.TargetPath = str(bat_path)
        sc.WorkingDirectory = str(ROOT)
        sc.WindowStyle = 7
        sc.Description = description
        if ICO.is_file():
            sc.IconLocation = f"{ICO},0"
        sc.Save()
        print(f"Готово: {lnk_path}")


if __name__ == "__main__":
    main()
