# -*- coding: utf-8 -*-
"""
Документы СНиОТ — делегирование в fix_sniot_document.py (AttestationSync).

Правила sniot-di-documents.mdc важнее любого docx-образца. Для всех типов: ДИ, РИ, Положения…
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from formatters.word_com import apply_word_grammar_check, try_close_open_document
from path_resolver import is_sniot_doc, normalize_sniot_path_text

FIX_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_sniot_document.py")
LEGACY_SCRIPT = Path(r"C:\Users\v.dubovik\AttestationSync\fix_senior_master_di.py")
# XML (--skip-word --always-apply): ждать до конца записи, не убивать на 90 с.
# GUI уже в отдельном потоке. 20 мин — только страховка от вечного зависания.
# Запасной номер: живой источник — SCRIPT_BUILD в fix_sniot_document.py на диске / stdout apply.
SNIOT_GUI_BUILD = "2026-08-24-agent-projects"
SNIOT_XML_TIMEOUT_SEC = 20 * 60
SNIOT_FIX_TIMEOUT_SEC = SNIOT_XML_TIMEOUT_SEC
_SCRIPT_BUILD_RE = re.compile(r'(?m)^SCRIPT_BUILD\s*=\s*"([^"]+)"')
_BUILD_LINE_PREFIX = "СНиОТ: сборка "


def read_live_script_build() -> str:
    """Номер из живого fix_sniot_document.py на диске, без кэша importlib."""
    script = FIX_SCRIPT if FIX_SCRIPT.is_file() else LEGACY_SCRIPT
    try:
        text = script.read_text(encoding="utf-8")
    except OSError:
        return SNIOT_GUI_BUILD
    match = _SCRIPT_BUILD_RE.search(text)
    return match.group(1) if match else SNIOT_GUI_BUILD


def _build_action() -> str:
    return f"{_BUILD_LINE_PREFIX}{read_live_script_build()}"


def _parse_script_build_line(output: str) -> str:
    for line in (output or "").splitlines():
        stripped = line.strip()
        if stripped.startswith(_BUILD_LINE_PREFIX):
            return stripped
    return _build_action()


def pick_sniot_build_line(result: dict | None = None) -> str:
    """Строка сборки для окна «Готово»: сначала stdout/return скрипта, не константа GUI."""
    if result:
        sniot = result.get("sniot_pass") or {}
        for candidate in (sniot.get("build"), result.get("sniot_build")):
            text = str(candidate or "").strip()
            if text.startswith(_BUILD_LINE_PREFIX):
                return text
        for action in list(sniot.get("actions") or []) + list(result.get("actions") or []):
            text = str(action).strip()
            if text.startswith(_BUILD_LINE_PREFIX):
                return text
    return _build_action()


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


def _fix_script_stamp() -> str:
    script = _resolve_script()
    if not script.is_file():
        return str(script)
    mtime = datetime.fromtimestamp(script.stat().st_mtime).strftime("%d.%m.%Y %H:%M")
    return f"{script} (изменён {mtime})"


def _clear_fix_module_cache() -> None:
    _load_fix_module.cache_clear()


@lru_cache(maxsize=1)
def _load_fix_module():
    """Загрузить fix_sniot_document.py как модуль (только для эвристик, не для финального прохода)."""
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
    Любая должностная инструкция — без learned text_edits и перестройки
    содержания (нумерацию исходника не сдвигать). Проверка русского —
    отдельно по галочке; аббревиатуры спеллер не трогает.
    """
    if doc_type == "dolzhnostnaya_instrukciya":
        return True
    name = Path(input_path or "").name.lower()
    if name.startswith("ди ") or "должностн" in name:
        return True
    try:
        mod = _load_fix_module()
        if hasattr(mod, "is_conservative_di_satp"):
            return bool(mod.is_conservative_di_satp(input_path))
    except Exception:
        pass
    path_low = (input_path or "").lower()
    if "мастер" in name and ("проект" in name or "оформлен" in name):
        return True
    return "сатп" in path_low and "мастер" in name


def is_sniot_document(source_path: str) -> bool:
    """True для .docx / .doc / .rtf в дереве СНиОТ на N:\\ или явных инструкций/положений."""
    low = source_path.lower()
    if not low.endswith((".docx", ".doc", ".rtf")):
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
    if doc_type in ("prikaz", "ezhenedelnyy_itog"):
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

    Всегда через subprocess к fix_sniot_document.py на диске — без кэша importlib,
    чтобы ярлык «АГЕНТ Дубовика (№ 007)» всегда брал свежие правила.
    """
    path = Path(output_path)
    stamp = [f"СНиОТ скрипт: {_fix_script_stamp()}"]
    if not path.is_file():
        build = _build_action()
        return {
            "ok": False,
            "applied": False,
            "build": build,
            "actions": [build] + stamp + [f"СНиОТ: файл не найден — {output_path}"],
            "after_issues": [],
        }
    try:
        closed = try_close_open_document(path)
        if closed.get("was_open") and not closed.get("closed"):
            build = _build_action()
            return {
                "ok": False,
                "applied": False,
                "build": build,
                "actions": [build]
                + stamp
                + [
                    "СНиОТ ⛔ Закройте «_оформлен.docx» в Word и нажмите «Оформить документ» снова",
                    f"СНиОТ: {closed.get('message') or 'Word не отдал файл'}",
                ],
                "after_issues": ["файл открыт в Word"],
            }
        if closed.get("closed"):
            stamp.append(f"СНиОТ: {closed.get('message')}")
    except Exception as exc:
        stamp.append(f"СНиОТ: проверка Word — {exc}")
    _clear_fix_module_cache()
    try:
        rep = run_sniot_document_fix(
            str(path), fix_page_breaks=True, always_apply=True
        )
        build = str(rep.get("build") or "").strip() or _build_action()
        rest = [a for a in list(rep.get("actions") or []) if a != build]
        actions = [build] + stamp + rest
        after_issues = [
            line[7:].strip()
            for line in actions
            if line.startswith("СНиОТ !") or line.startswith("СНиОТ ⛔")
        ]
        applied = bool(rep.get("applied"))
        # XML идёт с --skip-word, чтобы таймаут Word не убил запись.
        # Орфография (красные) и грамматика (зелёные) — отдельный вызов на уже записанный файл.
        if applied and path.is_file():
            try:
                gram = apply_word_grammar_check(str(path))
                gmsg = (gram or {}).get("message") or "Word: орфография/грамматика не ответила"
                if not str(gmsg).startswith("СНиОТ:"):
                    gmsg = f"СНиОТ: {gmsg}"
                actions.append(gmsg)
            except Exception as gram_exc:
                actions.append(f"СНиОТ: Word орфография/грамматика — {gram_exc}")
        return {
            "ok": bool(rep.get("ok")) and applied,
            "applied": applied,
            "build": build,
            "actions": actions,
            "after_issues": after_issues,
        }
    except Exception as exc:
        build = _build_action()
        return {
            "ok": False,
            "applied": False,
            "build": build,
            "actions": [build] + stamp + [f"СНиОТ: ошибка финального прохода — {exc}"],
            "after_issues": [str(exc)],
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
        build = _build_action()
        return {
            "ok": False,
            "exit_code": 3,
            "applied": False,
            "build": build,
            "message": f"Скрипт не найден: {script}",
            "actions": [build],
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
    cmd.append("--skip-word")

    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) | int(
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    xml_pass = always_apply or "--skip-word" in cmd
    wait_sec = SNIOT_XML_TIMEOUT_SEC if xml_pass else SNIOT_FIX_TIMEOUT_SEC
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=wait_sec,
            creationflags=creationflags,
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
        build = _build_action()
        return {
            "ok": False,
            "applied": False,
            "exit_code": 2,
            "build": build,
            "message": (
                "Оформление не закончилось за 20 минут. Закройте документ в Word "
                "и нажмите «Оформить документ» снова."
            ),
            "actions": [
                build,
                f"СНиОТ: таймаут {wait_sec} с — XML-проход не успел записаться",
            ],
            "after_issues": ["таймаут оформления"],
        }
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    applied = (
        "Сохранено:" in output
        and "Validation OK" in output
        and "запись в папку агент отменена" not in output.lower()
    )
    build = _parse_script_build_line(output)
    actions = [
        build,
        f"СНиОТ: {script.name}",
        f"Код выхода: {proc.returncode}",
    ]
    if proc.returncode == 2 or "закройте" in output.lower() and "word" in output.lower():
        ok = False
        applied = False
        actions.append(
            "СНиОТ ⛔ Закройте «_оформлен.docx» в Word и нажмите «Оформить документ» снова"
        )
        local_fixed = script.parent / "_work_sniot_document_fixed.docx"
        if local_fixed.is_file():
            actions.append(f"СНиОТ: правки готовы, но не записаны: {local_fixed}")
    for line in output.splitlines():
        line = line.strip()
        if line.startswith(
            (
                "Validation:",
                "Сохранено:",
                "Бэкап:",
                "Стратегия",
                "ОШИБКА:",
                "Исправляю:",
                "=== СНиОТ:",
                "СНиОТ:",
                "OK",
                "Есть замечания",
                "Скрипт сборка",
                "СНиОТ скрипт сборка",
                "СНиОТ: сборка",
                "Word:",
            )
        ):
            if line == build or line in actions:
                continue
            actions.append(line)
        elif line.startswith("  - "):
            actions.append(f"СНиОТ ! {line[4:]}")
        elif line.startswith("- "):
            actions.append(f"СНиОТ ! {line[2:]}")

    return {
        "ok": ok,
        "exit_code": proc.returncode,
        "applied": applied or "Сохранено:" in output,
        "build": build,
        "message": output.strip() or ("OK" if ok else "Ошибка"),
        "actions": actions,
        "output": source_path if ok and not dry_run and source_path else None,
    }


# обратная совместимость
run_satp_di_fix = run_sniot_document_fix
is_satp_senior_master_di = is_sniot_document
