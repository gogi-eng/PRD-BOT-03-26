# -*- coding: utf-8 -*-
"""
Мост «Делопроизводитель → Cursor»: задачи, которые агент не может решить
правилами оформления (перестройка содержания по образцу, OCR→смысл и т.п.).
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HANDOFF_DIR = ROOT / "handoff"


def _log(msg: str) -> None:
    try:
        from agent_core import log

        log(msg)
    except Exception:
        pass


def handoff_dir() -> Path:
    HANDOFF_DIR.mkdir(parents=True, exist_ok=True)
    return HANDOFF_DIR


def write_cursor_task(
    *,
    source_path: str,
    sample_path: str | None,
    doc_type: str,
    goal: str,
    extra: dict | None = None,
) -> Path:
    """Создать задание для Cursor и краткую инструкцию пользователю."""
    d = handoff_dir()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    task = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "source_path": source_path,
        "sample_path": sample_path or "",
        "doc_type": doc_type,
        "goal": goal,
        "extra": extra or {},
        "instructions_for_cursor": (
            "Выполни задание делопроизводителя: перестрой/исправь документ "
            "по образцу структуры, сохрани новым .docx рядом с исходником. "
            "Не меняй смысл без необходимости; адаптируй под предприятие "
            "«Минсккоммунтеплосеть», если исходник от МКТС. Отвечай пользователю по-русски."
        ),
    }
    req = d / f"request_{stamp}.json"
    latest = d / "request_latest.json"
    prompt = d / "PROMPT_FOR_CURSOR.txt"
    req.write_text(json.dumps(task, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(req.read_text(encoding="utf-8"), encoding="utf-8")

    prompt_text = (
        "Задание от агента-делопроизводителя (нужна помощь Cursor)\n"
        "============================================================\n\n"
        f"Цель:\n{goal}\n\n"
        f"Исходный документ:\n{source_path}\n\n"
        f"Образец структуры/содержания:\n{sample_path or '(не указан)'}\n\n"
        f"Тип документа: {doc_type}\n\n"
        "Сделай:\n"
        "1) Изучи исходник и образец (PDF — через OCR/рендер страниц при необходимости).\n"
        "2) Перестрой документ по структуре образца, сохранив факты/объекты МКТС.\n"
        "3) Сохрани НОВЫМ файлом .docx (не затирай исходник без копии).\n"
        "4) Кратко напиши в чат, куда сохранён результат.\n\n"
        f"Подробности JSON: {latest}\n"
    )
    prompt.write_text(prompt_text, encoding="utf-8")
    _log(f"CURSOR HANDOFF written: {req}")
    return prompt


def open_cursor_with_task(prompt_path: Path | None = None) -> tuple[bool, str]:
    """Открыть Cursor в папке DocAgent; вернуть (ok, сообщение)."""
    root = str(ROOT)
    prompt_path = prompt_path or (HANDOFF_DIR / "PROMPT_FOR_CURSOR.txt")
    try:
        # Открыть проект DocAgent в Cursor
        subprocess.Popen(
            ["cursor", root],
            cwd=root,
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # Попробовать открыть текст задания
        if prompt_path.is_file():
            subprocess.Popen(
                ["cursor", str(prompt_path)],
                cwd=root,
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        return True, (
            "Cursor открыт. В чате Cursor вставьте содержимое файла:\n"
            f"{prompt_path}\n"
            "или напишите: «Выполни задание из handoff/PROMPT_FOR_CURSOR.txt»"
        )
    except Exception as e:
        return False, (
            f"Не удалось запустить Cursor автоматически ({e}).\n"
            f"Откройте Cursor вручную и файл:\n{prompt_path}"
        )


def needs_cursor_assist(
    *,
    source_path: str,
    sample_path: str | None,
    doc_type: str,
) -> tuple[bool, str]:
    """
    Эвристика: когда одного «оформления» мало и нужна смысловая перестройка.
    """
    name = os.path.basename(source_path).lower()
    sample = (sample_path or "").lower()
    reasons: list[str] = []

    if source_path.lower().endswith(".pdf") and ("скан" in name or "scan" in name):
        reasons.append("скан PDF — нужна смысловая расшифровка OCR")

    if doc_type == "polozhenie":
        if "производствен" in name and "контрол" in name:
            reasons.append("положение о производственном контроле")
        if "цэм" in sample or "центроэнергомонтаж" in sample or "10-02-2023" in sample:
            reasons.append("образец ЦЭМ — нужна перестройка структуры, не только шрифты")

    if "ocr" in name or "скан" in name:
        reasons.append("признаки OCR-исходника")

    if reasons:
        return True, "; ".join(reasons)
    return False, ""
