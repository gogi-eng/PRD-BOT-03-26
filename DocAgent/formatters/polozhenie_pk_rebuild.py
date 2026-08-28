# -*- coding: utf-8 -*-
"""
Локальная перестройка Положения о производственном контроле МКТС
по структуре образца П.ЦЭМ 10-02-2023 (без вызова LLM).

Если рядом уже есть готовый файл *_по_образцу_ЦЭМ.docx — копирует его
в папку исходника как результат. Иначе запускает сборщик из Нормативки.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from path_resolver import copy_file_if_different


def _log(msg: str) -> None:
    try:
        from agent_core import log

        log(msg)
    except Exception:
        pass


KNOWN_READY = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность"
    r"\Положение о производственном контроле_по_образцу_ЦЭМ.docx"
)

BUILDER = Path(
    r"C:\Users\v.dubovik\Desktop\Нормативка\_build_polozhenie_mkts_cem.py"
)


def is_pk_polozhenie(path: str) -> bool:
    n = os.path.basename(path).lower()
    return ("положен" in n or "производствен" in n) and (
        "контрол" in n or "промбез" in n or "прб" in n or "пк" in n
    )


def is_cem_sample(path: str | None) -> bool:
    if not path:
        return False
    n = os.path.basename(path).lower()
    return "цэм" in n or "10-02-2023" in n or "центроэнерго" in n


def rebuild_pk_by_cem_structure(
    source_path: str,
    *,
    sample_path: str | None = None,
    python_exe: str | None = None,
) -> dict:
    """
    Перестроить положение о ПК по структуре ЦЭМ.
    Возвращает dict с output / actions / mode.
    """
    actions: list[str] = []
    src = Path(source_path)
    out_dir = src.parent if src.suffix.lower() in {".docx", ".doc", ".pdf", ".rtf"} else Path(
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Промышленная безопасность"
    )
    out = out_dir / "Положение о производственном контроле_по_образцу_ЦЭМ.docx"

    if KNOWN_READY.is_file():
        if out.resolve() != KNOWN_READY.resolve():
            copy_file_if_different(KNOWN_READY, out)
            actions.append(f"Скопирован готовый вариант по образцу ЦЭМ → {out.name}")
        else:
            actions.append(f"Использован готовый файл: {out.name}")
        _log(f"PK rebuild: used ready {KNOWN_READY}")
        return {
            "ok": True,
            "mode": "polozhenie_structure_rebuild_cem",
            "output": str(out),
            "actions": actions,
            "type": "polozhenie",
            "example_used": sample_path,
        }

    if BUILDER.is_file():
        py = python_exe or os.environ.get("PYTHON_EXE") or (
            r"C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe"
        )
        _log(f"PK rebuild: run builder {BUILDER}")
        proc = subprocess.run(
            [py, str(BUILDER)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            raise RuntimeError(
                "Не удалось собрать положение по образцу ЦЭМ.\n"
                f"{proc.stderr or proc.stdout}"
            )
        if KNOWN_READY.is_file():
            if out.resolve() != KNOWN_READY.resolve():
                copy_file_if_different(KNOWN_READY, out)
            actions.append("Собран документ сборщиком по структуре П.ЦЭМ 10-02-2023")
            return {
                "ok": True,
                "mode": "polozhenie_structure_rebuild_cem",
                "output": str(out if out.is_file() else KNOWN_READY),
                "actions": actions,
                "type": "polozhenie",
                "example_used": sample_path,
            }

    raise RuntimeError(
        "Нет готового файла и нет сборщика для перестройки по ЦЭМ.\n"
        "Нажмите «Помощь Cursor» — задание будет передано в чат Cursor."
    )
