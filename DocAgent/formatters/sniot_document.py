# -*- coding: utf-8 -*-
"""
Документы СНиОТ — делегирование в fix_sniot_document.py (AttestationSync).

Правила sniot-di-documents.mdc важнее любого docx-образца. Для всех типов: ДИ, РИ, Положения…
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from path_resolver import is_sniot_doc, normalize_sniot_path_text

FIX_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_sniot_document.py")
LEGACY_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_senior_master_di.py")

SNIIOT_TEXT_TYPES = frozenset(
    {
        "rabochaya_instrukciya",
        "dolzhnostnaya_instrukciya",
        "polozhenie",
        "instrukciya_ot",
    }
)


def _resolve_script() -> Path:
    if FIX_SCRIPT.is_file():
        return FIX_SCRIPT
    return LEGACY_SCRIPT


@lru_cache(maxsize=1)
def _load_fix_module():
    """Загрузить fix_sniot_document.py как модуль (in-process для DocAgent)."""
    script = _resolve_script()
    if not script.is_file():
        raise FileNotFoundError(f"Скрипт не найден: {script}")
    spec = importlib.util.spec_from_file_location("fix_sniot_document", script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Не удалось загрузить модуль: {script}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["fix_sniot_document"] = mod
    spec.loader.exec_module(mod)
    return mod


def is_conservative_di_satp(input_path: str, doc_type: str) -> bool:
    """
    ДИ САТП «Старший мастер» — без learned text_edits / structure_fix.
    Финал: только fix_sniot_document (нумерация 1.4.x / 1.5.x / 2.1.x).
    """
    if doc_type != "dolzhnostnaya_instrukciya":
        return False
    try:
        mod = _load_fix_module()
        if hasattr(mod, "is_senior_master_di_path"):
            return bool(mod.is_senior_master_di_path(input_path))
    except Exception:
        pass
    name = Path(input_path).name.lower()
    path_low = input_path.lower()
    if "мастер" in name and ("проект" in name or "оформлен" in name):
        return True
    return "сатп" in path_low and "мастер" in name


def is_sniot_document(source_path: str) -> bool:
    """True для docx в дереве СНиОТ на N:\\ или явных инструкций/положений."""
    if not source_path.lower().endswith(".docx"):
        return False
    return is_sniot_doc(Path(normalize_sniot_path_text(source_path)))


def should_apply_sniot_pass(
    source_path: str | None,
    output_path: str | None,
    doc_type: str = "",
) -> bool:
    """Нужен ли финальный проход fix_sniot_document после «Оформить документ»."""
    if not output_path or not output_path.lower().endswith(".docx"):
        return False
    if doc_type == "prikaz":
        return False
    if doc_type in SNIIOT_TEXT_TYPES:
        return True
    for path in (source_path, output_path):
        if path and is_sniot_document(path):
            return True
    return False


def apply_sniot_rules_to_output(
    output_path: str,
    *,
    fix_page_breaks: bool = False,
) -> dict:
    """
    Полный process_sniot_document + validate на готовом *_оформлен.docx.
    Сохраняет результат на месте (in-process, без subprocess).

    Если в той же папке есть *_образец.docx (напр. ПРОЕКТ …_образец.docx),
    интервалы и пустые строки выравниваются по образцу автоматически.
    Источник для «Оформить документ»: черновик или _образец.docx в папке Агент;
    результат всегда *_оформлен.docx рядом с образцом.
    """
    path = Path(output_path)
    if not path.is_file():
        return {
            "ok": False,
            "applied": False,
            "actions": [f"СНиОТ: файл не найден — {output_path}"],
        }
    try:
        mod = _load_fix_module()
        if hasattr(mod, "apply_sniot_rules_to_file"):
            return mod.apply_sniot_rules_to_file(
                path, fix_page_breaks=fix_page_breaks, always_apply=True
            )
        # запасной путь — subprocess на тот же файл
        rep = run_sniot_document_fix(
            str(path), fix_page_breaks=fix_page_breaks, always_apply=True
        )
        return {
            "ok": bool(rep.get("ok")),
            "applied": bool(rep.get("applied")),
            "actions": rep.get("actions") or [rep.get("message", "")],
            "after_issues": [],
        }
    except Exception as exc:
        return {
            "ok": False,
            "applied": False,
            "actions": [f"СНиОТ: ошибка финального прохода — {exc}"],
        }


def run_sniot_document_fix(
    source_path: str | None = None,
    *,
    check_only: bool = False,
    fix_page_breaks: bool = False,
    dry_run: bool = False,
    use_handoff: bool = False,
    always_apply: bool = False,
) -> dict:
    """Запуск fix_sniot_document.py. Возвращает dict для agent_core / GUI."""
    script = _resolve_script()
    if not script.is_file():
        return {
            "ok": False,
            "exit_code": 3,
            "message": f"Скрипт не найден: {script}",
            "actions": [],
        }

    cmd = [sys.executable, str(script)]
    if use_handoff or source_path is None:
        cmd.append("--handoff")
    elif source_path:
        cmd.extend(["--target", source_path])
    if check_only:
        cmd.append("--check")
    if dry_run:
        cmd.append("--dry-run")
    if fix_page_breaks:
        cmd.append("--fix-page-breaks")
    if always_apply:
        cmd.append("--always-apply")

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    actions = [
        "СНиОТ: правила sniot-di-documents.mdc (fix_sniot_document.py)",
        f"Код выхода: {proc.returncode}",
    ]
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(
            ("Validation:", "Сохранено:", "Бэкап:", "Стратегия", "ОШИБКА:", "Исправляю:", "=== СНиОТ:")
        ):
            actions.append(line)
        elif line.startswith("  - "):
            actions.append(line[4:])

    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "applied": "Сохранено:" in output or "финальная проверка" in output,
        "message": output.strip() or ("OK" if ok else "Ошибка"),
        "actions": actions,
        "output": source_path if ok and not dry_run and source_path else None,
    }


# обратная совместимость
run_satp_di_fix = run_sniot_document_fix
is_satp_senior_master_di = is_sniot_document
