# -*- coding: utf-8 -*-
"""
Еженедельный итог работы Дубовика В.В. (СНиОТ).

Что делает скрипт:
1) Определяет период недели (пн–вс, либо пн–сегодня, если неделя ещё идёт).
2) Собирает файлы, созданные/изменённые в этот период (Документы, Загрузки,
   рабочий стол, папка «Фоты проверок», сетевая папка N:\\…\\Дубовик В.В).
3) По именам фото IMG_ГГГГММДД_… группирует проверки по датам.
4) Считает количество: мероприятий, документов рассмотренных, документов разработанных
   (включая задачи СЭД с скриншотов: Ознакомиться/Согласовать → рассмотрено,
   Исполнить → мероприятия).
5) Пишет отчёт Word на рабочий стол и в N:\\…\\Отчеты (если доступен).

Запуск: двойной щелчок по «Сделать еженедельный итог.bat»
или: python weekly_report.py
      python weekly_report.py --from 2026-07-27 --to 2026-07-30
      python weekly_report.py --events 5 --reviewed 12 --developed 4
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import Cm, Pt
except ImportError:
    print("Нужна библиотека python-docx. Установите: pip install python-docx")
    sys.exit(1)

DESKTOP = Path.home() / "Desktop"
DOWNLOADS = Path.home() / "Downloads"
DOCUMENTS = Path.home() / "Documents"
PHOTOS = DESKTOP / "Фоты проверок"
N_DUBOVIK = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Дубовик В.В"
)
N_REPORTS = N_DUBOVIK / "Отчеты"

IMG_DATE_RE = re.compile(r"(?:IMG_|PHOTO_)?(\d{4})(\d{2})(\d{2})[_-]?", re.I)
FOLDER_DATE_RE = re.compile(
    r"(\d{1,2})[.\-_](\d{1,2})[.\-_](\d{2,4})|(\d{1,2})[.\-_](\d{1,2})[.\-_]?(\d{2})",
    re.I,
)

DOC_EXTS = {".docx", ".doc", ".pdf", ".xlsx", ".xls", ".rtf", ".txt"}
IMG_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
SKIP_DIR_PARTS = {
    "node_modules",
    ".git",
    "__pycache__",
    "venv",
    ".venv",
    "AppData",
    "ViberDownloads",
}


def week_bounds(today: dt.date | None = None) -> tuple[dt.datetime, dt.datetime]:
    """Понедельник 07:30 — воскресенье 23:59:59 (или сегодня, если неделя не закончена)."""
    today = today or dt.date.today()
    monday = today - dt.timedelta(days=today.weekday())
    start = dt.datetime.combine(monday, dt.time(7, 30))
    sunday = monday + dt.timedelta(days=6)
    end_day = min(today, sunday)
    end = dt.datetime.combine(end_day, dt.time(23, 59, 59))
    return start, end


def parse_args():
    p = argparse.ArgumentParser(description="Еженедельный отчёт СНиОТ")
    p.add_argument("--from", dest="date_from", help="Начало ГГГГ-ММ-ДД")
    p.add_argument("--to", dest="date_to", help="Конец ГГГГ-ММ-ДД")
    p.add_argument(
        "--out",
        dest="out",
        help="Куда сохранить .docx (по умолчанию — рабочий стол)",
    )
    p.add_argument(
        "--events",
        type=int,
        default=None,
        help="Число мероприятий вручную (иначе считает скрипт)",
    )
    p.add_argument(
        "--reviewed",
        type=int,
        default=None,
        help="Число рассмотренных документов вручную",
    )
    p.add_argument(
        "--developed",
        type=int,
        default=None,
        help="Число разработанных документов вручную",
    )
    return p.parse_args()


def in_range(ts: dt.datetime, start: dt.datetime, end: dt.datetime) -> bool:
    return start <= ts <= end


def file_times(path: Path) -> tuple[dt.datetime, dt.datetime]:
    """
    (время_создания/появления, время_последнего_сохранения).
    На Windows стараемся взять birth time; иначе st_ctime.
    Учёт работы — только по mtime (файл реально сохраняли).
    """
    st = path.stat()
    m = dt.datetime.fromtimestamp(st.st_mtime)
    birth = getattr(st, "st_birthtime", None)
    if birth:
        c = dt.datetime.fromtimestamp(birth)
    else:
        c = dt.datetime.fromtimestamp(st.st_ctime)
    return c, m


def should_skip(path: Path) -> bool:
    parts = {x.lower() for x in path.parts}
    return bool(parts & {x.lower() for x in SKIP_DIR_PARTS})


def is_likely_unedited_download(path: Path, ctime: dt.datetime, mtime: dt.datetime) -> bool:
    """Скачали в «Загрузки» и сразу не правили — в отчёт не берём."""
    if "downloads" not in {p.lower() for p in path.parts}:
        return False
    return abs((mtime - ctime).total_seconds()) < 180


def format_work_span(item: dict, start: dt.datetime, end: dt.datetime) -> str:
    """
    Текст про время правки/сохранения.
    Точного «сколько минут смотрели» ОС не хранит — только создание и сохранение.
    """
    c: dt.datetime = item["ctime"]
    m: dt.datetime = item["mtime"]
    saved = m.strftime("%d.%m.%Y %H:%M")
    # оба события в периоде и сохранение позже создания — оценка окна работы
    if in_range(c, start, end) and m > c:
        delta = m - c
        hours = delta.total_seconds() / 3600
        if 0.05 <= hours <= 10:  # от ~3 мин до 10 ч — похоже на одну сессию
            return (
                f"правка примерно с {c.strftime('%d.%m.%Y %H:%M')} "
                f"по {m.strftime('%H:%M')} "
                f"(~{int(delta.total_seconds() // 60)} мин), сохранение {saved}"
            )
        if c.date() == m.date():
            return (
                f"в работе {c.strftime('%d.%m.%Y')} "
                f"с {c.strftime('%H:%M')} до {m.strftime('%H:%M')}, "
                f"сохранение {saved}"
            )
    return f"сохранён {saved}"


def estimate_session_minutes(item: dict, start: dt.datetime, end: dt.datetime) -> int:
    """Оценка минут по разнице создание→сохранение в периоде (0, если нельзя оценить)."""
    c, m = item["ctime"], item["mtime"]
    if not in_range(m, start, end):
        return 0
    if not in_range(c, start, end) or m <= c:
        return 0
    mins = int((m - c).total_seconds() // 60)
    if mins < 3 or mins > 10 * 60:
        return 0
    return mins


def scan_docs(roots: list[Path], start: dt.datetime, end: dt.datetime) -> list[dict]:
    """Только файлы, которые СОХРАНЯЛИ в периоде (mtime), на ПК или N:."""
    found = []
    for root in roots:
        if not root.exists():
            continue
        try:
            iterator = root.rglob("*")
        except OSError:
            continue
        for path in iterator:
            try:
                if not path.is_file():
                    continue
                if should_skip(path):
                    continue
                if path.suffix.lower() not in DOC_EXTS:
                    continue
                c, m = file_times(path)
                # главное правило: документ правили и сохранили в периоде
                if not in_range(m, start, end):
                    continue
                if is_likely_unedited_download(path, c, m):
                    continue
                found.append(
                    {
                        "path": path,
                        "name": path.name,
                        "ctime": c,
                        "mtime": m,
                        "touched": m,  # для отчёта — момент сохранения
                        "saved_in_period": True,
                        "created_in_period": in_range(c, start, end),
                        "rel": str(path),
                    }
                )
            except OSError:
                continue
    found.sort(key=lambda x: x["touched"])
    return found


def date_from_img_name(name: str) -> dt.date | None:
    m = IMG_DATE_RE.search(name)
    if not m:
        return None
    try:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def scan_photos(photo_root: Path, start: dt.datetime, end: dt.datetime) -> dict[dt.date, list[Path]]:
    by_day: dict[dt.date, list[Path]] = defaultdict(list)
    if not photo_root.exists():
        return by_day
    start_d, end_d = start.date(), end.date()
    for path in photo_root.rglob("*"):
        try:
            if not path.is_file() or path.suffix.lower() not in IMG_EXTS:
                continue
            d = date_from_img_name(path.name)
            if d is None:
                # папка вида «14_07_26 …»
                for part in path.parts:
                    fm = FOLDER_DATE_RE.search(part)
                    if not fm:
                        continue
                    g = fm.groups()
                    if g[0] and g[1] and g[2]:
                        day, month, year = int(g[0]), int(g[1]), int(g[2])
                    elif g[3] and g[4] and g[5]:
                        day, month, year = int(g[3]), int(g[4]), int(g[5])
                    else:
                        continue
                    if year < 100:
                        year += 2000
                    try:
                        d = dt.date(year, month, day)
                    except ValueError:
                        d = None
                    break
            if d is None or d < start_d or d > end_d:
                continue
            by_day[d].append(path)
        except OSError:
            continue
    for d in by_day:
        by_day[d].sort(key=lambda p: p.name)
    return dict(sorted(by_day.items()))


def find_sed_screens(downloads: Path, start: dt.datetime, end: dt.datetime) -> list[Path]:
    """Скриншоты СЭД в Загрузках за период (по имени и/или дате файла)."""
    out: list[Path] = []
    if not downloads.exists():
        return out
    name_hints = ("экран", "screen", "снимок", "сед", "задач", "ознакомит", "согласов")
    for path in downloads.iterdir():
        try:
            if not path.is_file():
                continue
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".bmp"}:
                continue
            c, m = file_times(path)
            if not (in_range(c, start, end) or in_range(m, start, end)):
                continue
            low = path.name.lower()
            if any(h in low for h in name_hints):
                out.append(path)
        except OSError:
            continue
    out.sort(key=lambda p: p.stat().st_mtime)
    return out


def ocr_image_windows(path: Path) -> str:
    """Распознать текст скриншота через встроенный Windows OCR."""
    script = Path(__file__).resolve().parent / "win_ocr.ps1"
    if not script.is_file():
        return ""
    out_txt = Path(__file__).resolve().parent / f"_ocr_tmp_{os.getpid()}.txt"
    try:
        import subprocess

        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-ImagePath",
                str(path),
                "-OutFile",
                str(out_txt),
            ],
            capture_output=True,
            timeout=90,
            check=False,
        )
        if out_txt.is_file() and out_txt.stat().st_size > 0:
            text = out_txt.read_text(encoding="utf-8").strip()
            return text
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        out = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        if err or out:
            print(f"OCR предупреждение ({path.name}): {(err or out)[:240]}")
        return ""
    except Exception as e:
        print(f"OCR не удался для {path.name}: {e}")
        return ""
    finally:
        if out_txt.exists():
            try:
                out_txt.unlink()
            except OSError:
                pass


_SED_ACTION_RE = re.compile(
    r"(?i)\b(ознакомиться|согласовать|исполнить)\b\s*[«\"'«\"]?\s*(.+?)(?="
    r"\b(?:ознакомиться|согласовать|исполнить|отсутствие|приказ|внутренние|"
    r"файлы|подписанный|акт\b|\(\s*(?:понедельник|вторник|среда|четверг|пятница|"
    r"суббота|воскресенье)\s*\))"
    r"|$)",
)


def _normalize_sed_title(title: str) -> str:
    t = (title or "").strip(" \t\r\n«»\"'„“.…-_")
    t = re.sub(r"\s+", " ", t)
    # обрезать хвосты OCR: «__. (среда)», даты-дни недели
    t = re.sub(
        r"\s*[_.,]{0,4}\s*\(\s*(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)\s*\)\s*$",
        "",
        t,
        flags=re.I,
    )
    t = re.sub(r"[_…]{2,}\s*$", "", t)
    # частые ошибки OCR
    repl = (
        ("инструщия", "инструкция"),
        ("инструщии", "инструкции"),
        ("нупедого", "нулевого"),
        ("нупевого", "нулевого"),
        ("еженедепьное", "еженедельное"),
        ("допжностная", "должностная"),
        ("територий", "территорий"),
        ("мд ", "№ "),
        ("ng3", "№3"),
        ("ftc-", "РТС-"),
    )
    for a, b in repl:
        t = re.sub(a, b, t, flags=re.I)
    return t[:160].strip(" .,_-")


def parse_sed_tasks_from_text(text: str) -> list[dict]:
    """
    Из текста OCR вытащить задачи СЭД:
    Ознакомиться / Согласовать / Исполнить + название.
    """
    if not text or not text.strip():
        return []
    # склеить переносы, даты-заголовки оставить как разделители
    blob = re.sub(r"[\r\n]+", " ", text)
    blob = re.sub(r"\s+", " ", blob).strip()

    tasks: list[dict] = []
    for m in _SED_ACTION_RE.finditer(blob):
        action = m.group(1).capitalize()
        # normalize action spelling
        al = action.lower()
        if al.startswith("ознаком"):
            action = "Ознакомиться"
            kind = "reviewed"
        elif al.startswith("соглас"):
            action = "Согласовать"
            kind = "reviewed"
        else:
            action = "Исполнить"
            kind = "execute"
        title = _normalize_sed_title(m.group(2))
        if len(title) < 3:
            continue
        # отбросить мусор вроде одной даты
        if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{2,4}", title):
            continue
        tasks.append(
            {
                "action": action,
                "title": title,
                "kind": kind,
                "raw": m.group(0)[:200],
            }
        )

    # дедуп: одинаковое действие + похожее начало названия
    seen: set[str] = set()
    uniq: list[dict] = []
    for t in tasks:
        title_key = re.sub(r"[^a-zа-яё0-9]+", "", t["title"].lower())[:36]
        key = t["action"].lower() + "|" + title_key
        if not title_key or key in seen:
            continue
        # если уже есть более длинный похожий — пропуск короткого
        skip = False
        for prev in list(seen):
            if prev.startswith(t["action"].lower() + "|") and (
                title_key in prev.split("|", 1)[-1] or prev.split("|", 1)[-1] in title_key
            ):
                skip = True
                break
        if skip:
            continue
        seen.add(key)
        uniq.append(t)
    return uniq


def extract_sed_tasks(screens: list[Path]) -> dict:
    """OCR всех скриншотов СЭД → задачи и счётчики."""
    all_tasks: list[dict] = []
    per_file: list[tuple[Path, list[dict], str]] = []
    for path in screens:
        text = ocr_image_windows(path)
        # если имя похоже на экран, но OCR пустой — всё равно учтём файл
        tasks = parse_sed_tasks_from_text(text)
        # эвристика: если в имени нет «экран/сед», а задач нет — пропуск
        low = path.name.lower()
        looks_sed = any(
            h in low for h in ("экран", "screen", "снимок", "сед", "задач")
        )
        if not tasks and not looks_sed and text:
            # текст есть, но без глаголов задач — не СЭД
            if not re.search(r"(?i)ознакомиться|согласовать|исполнить", text):
                continue
        per_file.append((path, tasks, text[:500]))
        all_tasks.extend(tasks)

    # общий дедуп между скриншотами
    seen: set[str] = set()
    uniq: list[dict] = []
    for t in all_tasks:
        key = (t["action"] + "|" + t["title"][:50]).lower()
        key = re.sub(r"[^a-zа-яё0-9|]+", "", key)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(t)

    n_acq = sum(1 for t in uniq if t["action"] == "Ознакомиться")
    n_agr = sum(1 for t in uniq if t["action"] == "Согласовать")
    n_exe = sum(1 for t in uniq if t["action"] == "Исполнить")
    return {
        "tasks": uniq,
        "per_file": per_file,
        "n_acquaint": n_acq,
        "n_agree": n_agr,
        "n_execute": n_exe,
        "n_reviewed": n_acq + n_agr,
        "n_total": len(uniq),
    }


def set_run_font(run, bold=False, size=14):
    run.font.name = "Times New Roman"
    run.font.size = Pt(size)
    run.bold = bold
    rPr = run._element.get_or_add_rPr()
    rFonts = rPr.find(qn("w:rFonts"))
    if rFonts is None:
        rFonts = OxmlElement("w:rFonts")
        rPr.insert(0, rFonts)
    for a in ("w:ascii", "w:hAnsi", "w:eastAsia", "w:cs"):
        rFonts.set(qn(a), "Times New Roman")


def add_p(doc, text, bold=False, center=False, first=True, size=14):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(6)
    pf.line_spacing = 1.15
    pf.first_line_indent = Cm(1.25) if first and not center else Cm(0)
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY
    set_run_font(p.add_run(text), bold=bold, size=size)


def add_h(doc, text):
    add_p(doc, text, bold=True, first=False)


def add_bul(doc, text):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    pf.space_before = Pt(0)
    pf.space_after = Pt(3)
    pf.line_spacing = 1.15
    pf.left_indent = Cm(0.75)
    pf.first_line_indent = Cm(0)
    p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
    set_run_font(p.add_run("– " + text), size=14)


def group_docs_by_day(docs: list[dict]) -> dict[dt.date, list[dict]]:
    g: dict[dt.date, list[dict]] = defaultdict(list)
    for item in docs:
        g[item["touched"].date()].append(item)
    return dict(sorted(g.items()))


def normalize_edition_key(name: str) -> str:
    """Свести разные редакции одного документа к одному ключу."""
    s = name.lower()
    s = re.sub(r"\.(docx?|pdf|xlsx?|rtf|txt)$", "", s)
    for pat in (
        r"_оформлен\+?",
        r"_formatted",
        r"_проект",
        r"_эталон",
        r"_backup",
        r"_тест\w*",
        r"_с_проверками",
        r"_с_количествами",
        r"\s*\(\d+\)",
        r"_копия",
        r"— копия",
        r"- копия",
        r"_out\d*",
        r"_in\b",
        r"_final",
        r"\.{2,}",
    ):
        s = re.sub(pat, "", s, flags=re.I)
    s = re.sub(r"[\s_\-.–—]+", " ", s).strip()
    s = re.sub(r"\s*\d{1,2}[./]\d{1,2}[./]\d{2,4}.*$", "", s)
    return s[:100]


def dedupe_editions(docs: list[dict]) -> list[dict]:
    """Оставить по одной записи на логический документ (последняя редакция)."""
    best: dict[str, dict] = {}
    for item in docs:
        key = normalize_edition_key(item["name"])
        if not key or key.startswith("~$"):
            continue
        prev = best.get(key)
        if prev is None or item["touched"] >= prev["touched"]:
            best[key] = item
    return sorted(best.values(), key=lambda x: x["touched"])


def interesting_name(name: str) -> bool:
    low = name.lower()
    keys = (
        "акт",
        "приказ",
        "инструкц",
        "отчет",
        "отчёт",
        "комисс",
        "опо",
        "jsa",
        "риск",
        "протокол",
        "сед",
        "ноль",
        "травмат",
        "персонал",
        "провер",
        "ди ",
        "ри ",
        "докладн",
        "распоряж",
        "памятк",
        "карт",
    )
    return any(k in low for k in keys)


# Документы, которые обычно «разрабатываем / готовим сами»
DEVELOPED_KEYS = (
    "приказ",
    "акт",
    "докладн",
    "jsa",
    "карт",
    "инструкц",
    "проект",
    "оформлен",
    "черновик",
    "положен",
    "распоряж",
    "памятк",
    "отчет",
    "отчёт",
    "справк",
    "информац по смен",
    "персональн",
)

# Документы, которые чаще «рассматриваем / согласовываем / изучаем»
REVIEWED_KEYS = (
    "согласов",
    "подписанн",
    "ознакомит",
    "протокол",
    "re_",
    "закон",
    "ткп",
    "правил",
    "норм",
    "комментар",
    "zero",
    "нулев",
    "сед",
)

# По именам — признаки мероприятий (проверки, совещания, комиссии)
EVENT_DOC_KEYS = (
    "проверк",
    "акт",
    "совещани",
    "комисс",
    "обследован",
    "освидетельств",
    "ноль",
    "травмат",
    "нот",
)


def classify_document(item: dict, start: dt.datetime, end: dt.datetime) -> str:
    """
    Вернуть: developed | reviewed | other.

    Считаем ТОЛЬКО документы, сохранённые в периоде (mtime уже отфильтрован в scan_docs).
    developed — создан и сохранён на этой неделе (подготовка нового документа);
    reviewed  — файл был раньше, на этой неделе правили/сохраняли
                (рассмотрение, изучение, сверка с нормами, правка по замечаниям).
    """
    name = item["name"]
    low = name.lower()
    created_in = bool(item.get("created_in_period")) or in_range(item["ctime"], start, end)
    saved_in = in_range(item["mtime"], start, end)

    if not saved_in:
        return "other"

    # явные признаки рассмотрения / согласования / изучения норм
    if any(k in low for k in REVIEWED_KEYS) and not created_in:
        return "reviewed"
    if any(k in low for k in REVIEWED_KEYS) and any(
        k in low for k in ("подписанн", "протокол", "закон", "ткп", "re_", "норм")
    ):
        return "reviewed"

    if created_in and any(k in low for k in DEVELOPED_KEYS):
        return "developed"
    if created_in and interesting_name(name):
        return "developed"

    # существовал раньше — на этой неделе сохранили после правки/сверки
    if (not created_in) and interesting_name(name):
        return "reviewed"

    if created_in:
        return "developed"
    return "reviewed"


def count_stats(
    docs: list[dict],
    photos: dict[dt.date, list[Path]],
    start: dt.datetime,
    end: dt.datetime,
    sed_info: dict | None = None,
) -> dict:
    """
    Мероприятия = уникальные выезды/проверки по фото (день+папка)
                 + отдельные «событийные» документы (акты проверок и т.п.),
                   если по этому дню ещё не было фото-мероприятия
                 + задачи СЭД «Исполнить».
    Рассмотренные = файлы + задачи СЭД «Ознакомиться»/«Согласовать».
    """
    developed = []
    reviewed = []
    event_docs = []

    for item in docs:
        kind = classify_document(item, start, end)
        if kind == "developed":
            developed.append(item)
        elif kind == "reviewed":
            reviewed.append(item)
        low = item["name"].lower()
        if any(k in low for k in EVENT_DOC_KEYS) and interesting_name(item["name"]):
            event_docs.append(item)

    # Уникальные мероприятия по фото: (дата, папка объекта)
    photo_events = set()
    for day, files in photos.items():
        for f in files:
            photo_events.add((day, f.parent.name))

    # Документы-мероприятия, не перекрытые фото того же дня
    photo_days = set(photos.keys())
    extra_event_docs = []
    seen_names = set()
    for item in event_docs:
        key = item["name"].lower()
        if key in seen_names:
            continue
        seen_names.add(key)
        day = item["touched"].date()
        if day in photo_days and any(k in key for k in ("акт", "проверк", "нот", "травмат")):
            continue
        extra_event_docs.append(item)

    sed_info = sed_info or {}
    sed_reviewed = int(sed_info.get("n_reviewed") or 0)
    sed_execute = int(sed_info.get("n_execute") or 0)
    sed_total = int(sed_info.get("n_total") or 0)

    events_count = len(photo_events) + len(extra_event_docs) + sed_execute
    reviewed_n = len(reviewed) + sed_reviewed

    work_files = developed + reviewed
    session_mins = sum(estimate_session_minutes(x, start, end) for x in work_files)
    saves = sorted(x["mtime"] for x in work_files) if work_files else []

    return {
        "events": events_count,
        "events_photo": len(photo_events),
        "events_docs": len(extra_event_docs),
        "events_sed_execute": sed_execute,
        "photo_event_list": sorted(photo_events, key=lambda x: (x[0], x[1])),
        "event_docs": extra_event_docs,
        "developed": developed,
        "reviewed": reviewed,
        "developed_n": len(developed),
        "reviewed_n": reviewed_n,
        "reviewed_files_n": len(reviewed),
        "sed_reviewed_n": sed_reviewed,
        "sed_execute_n": sed_execute,
        "sed_total_n": sed_total,
        "sed_tasks": list(sed_info.get("tasks") or []),
        "work_session_minutes": session_mins,
        "work_first_save": saves[0] if saves else None,
        "work_last_save": saves[-1] if saves else None,
        "work_files_n": len(work_files),
    }


def build_report(
    start: dt.datetime,
    end: dt.datetime,
    docs: list[dict],
    photos: dict[dt.date, list[Path]],
    sed: list[Path],
    out_path: Path,
    stats: dict,
) -> Path:
    doc = Document()
    for s in doc.sections:
        s.top_margin = Cm(2)
        s.bottom_margin = Cm(2)
        s.left_margin = Cm(3)
        s.right_margin = Cm(1.5)

    d1 = start.strftime("%d.%m.%Y")
    d2 = end.strftime("%d.%m.%Y")
    today_s = dt.date.today().strftime("%d.%m.%Y")

    add_p(doc, "ГОСУДАРСТВЕННОЕ ПРЕДПРИЯТИЕ «МИНСККОММУНТЕПЛОСЕТЬ»", True, True, False, 12)
    add_p(doc, "Служба надёжности и охраны труда (СНиОТ)", False, True, False, 12)
    doc.add_paragraph()
    add_p(doc, "ОТЧЁТ", True, True, False, 16)
    add_p(doc, "о выполненной работе за период", False, True, False)
    add_p(doc, f"с {d1} по {d2}", True, True, False)
    doc.add_paragraph()
    add_p(
        doc,
        "Исполнитель: ведущий инженер по промышленной безопасности СНиОТ Дубовик В.В.",
        first=False,
    )
    add_p(
        doc,
        "Документ сформирован автоматически скриптом «Еженедельный итог». "
        "В количественные итоги по файлам входят только документы, "
        "которые были отредактированы и СОХРАНЕНЫ на компьютере или на диске N: "
        "в указанный период (по времени последнего сохранения). "
        "Время правки — оценка по созданию/сохранению файла (ОС не хранит "
        "точную длительность «изучения» без сохранения).",
        first=True,
    )

    add_h(doc, "1. Количественные итоги")
    n_docs = len(docs)
    n_photo_days = len(photos)
    n_photos = sum(len(v) for v in photos.values())
    ev = stats["events"]
    rev = stats["reviewed_n"]
    dev = stats["developed_n"]

    add_p(
        doc,
        f"За период с {d1} по {d2} проведено мероприятий: {ev}; "
        f"документов рассмотрено: {rev}; документов разработано: {dev}.",
        first=True,
    )
    add_bul(doc, f"Мероприятий (всего): {ev}")
    add_bul(
        doc,
        f"  из них проверок объектов по фотофиксации: {stats['events_photo']}; "
        f"иных мероприятий по документам: {stats['events_docs']}; "
        f"задач СЭД «Исполнить»: {stats.get('events_sed_execute', 0)}",
    )
    add_bul(
        doc,
        "Документов рассмотрено (правка / изучение / сверка с нормами / "
        f"ознакомление / согласование): {rev}",
    )
    add_bul(
        doc,
        f"  из них сохранено на ПК/N:: {stats.get('reviewed_files_n', rev)}; "
        f"задач СЭД «Ознакомиться»/«Согласовать»: {stats.get('sed_reviewed_n', 0)}",
    )
    add_bul(doc, f"Документов разработано (созданы и сохранены в периоде): {dev}")
    if stats.get("work_first_save") and stats.get("work_last_save"):
        add_bul(
            doc,
            "Работа с файлами (по времени сохранений): с "
            f"{stats['work_first_save'].strftime('%d.%m.%Y %H:%M')} "
            f"по {stats['work_last_save'].strftime('%d.%m.%Y %H:%M')} "
            f"(файлов с сохранением: {stats.get('work_files_n', 0)}).",
        )
    sess = int(stats.get("work_session_minutes") or 0)
    if sess > 0:
        add_bul(
            doc,
            f"Оценка суммарного времени правок по файлам "
            f"(где есть создание и сохранение в периоде, сессия до 10 ч): "
            f"примерно {sess // 60} ч {sess % 60} мин. "
            f"Это не хронометраж «изучения без сохранения».",
        )
    add_bul(
        doc,
        f"Справочно: файлов с сохранением за период — {n_docs}; "
        f"дней с фото — {n_photo_days}; снимков — {n_photos}; "
        f"скриншотов СЭД — {len(sed)}; задач СЭД распознано — {stats.get('sed_total_n', 0)}.",
    )
    if stats.get("manual"):
        add_p(
            doc,
            "Часть показателей задана вручную параметрами запуска (--events / --reviewed / --developed).",
            first=True,
        )
    else:
        add_p(
            doc,
            "Цифры посчитаны автоматически по файлам и фото. "
            "При необходимости поправьте вручную в этом абзаце перед сдачей.",
            first=True,
        )

    add_h(doc, "1.1. Мероприятия (детализация)")
    if stats["photo_event_list"] or stats["event_docs"]:
        for day, folder in stats["photo_event_list"]:
            add_bul(
                doc,
                f"{day.strftime('%d.%m.%Y')} — проверка объекта "
                f"(папка «{folder}»). Адрес: _______________",
            )
        for item in stats["event_docs"]:
            add_bul(
                doc,
                f"{item['touched'].strftime('%d.%m.%Y')} — {item['name']}",
            )
    else:
        add_p(doc, "Автоматически выделенных мероприятий не найдено.")

    add_h(doc, "1.2. Разработанные документы")
    if stats["developed"]:
        for item in stats["developed"][:40]:
            span = format_work_span(item, start, end)
            add_bul(
                doc,
                f"{item['name']} — {span}",
            )
        if len(stats["developed"]) > 40:
            add_bul(doc, f"… и ещё: {len(stats['developed']) - 40}")
    else:
        add_p(doc, "Разработанных документов за период не выявлено.")

    add_h(doc, "1.3. Рассмотренные / правленные документы")
    add_p(
        doc,
        "Файлы, которые уже существовали ранее и были сохранены в периоде "
        "(правка замечаний, изучение, сверка с нормативными требованиями и т.п.).",
        first=True,
    )
    if stats["reviewed"]:
        for item in stats["reviewed"][:40]:
            span = format_work_span(item, start, end)
            add_bul(
                doc,
                f"{item['name']} — {span}",
            )
        if len(stats["reviewed"]) > 40:
            add_bul(doc, f"… и ещё: {len(stats['reviewed']) - 40}")
    else:
        add_p(doc, "Сохранённых «рассмотренных» документов за период не выявлено.")

    add_h(doc, "2. Проверки объектов (по дате в имени фото)")
    if not photos:
        add_p(
            doc,
            "В папке «Фоты проверок» за этот период не найдено фото "
            "с датой в имени файла (IMG_ГГГГММДД_…) или в имени папки.",
        )
    else:
        add_p(
            doc,
            "Адреса с табличек на снимках скрипт сам не читает. "
            "Ниже — даты и папки/файлы; при сдаче отчёта допишите адрес объекта.",
            first=True,
        )
        for day, files in photos.items():
            add_h(doc, f"2.{day.strftime('%d.%m.%Y')}")
            folders = sorted({f.parent.name for f in files})
            for folder in folders:
                cnt = sum(1 for f in files if f.parent.name == folder)
                add_bul(doc, f"Папка «{folder}» — снимков: {cnt}. Адрес объекта: _______________")
            examples = ", ".join(f.name for f in files[:5])
            if len(files) > 5:
                examples += f" … (+ещё {len(files) - 5})"
            add_bul(doc, f"Примеры файлов: {examples}")

    add_h(doc, "3. Документы по дням")
    by_day = group_docs_by_day(docs)
    if not by_day:
        add_p(doc, "Документов за период не найдено.")
    else:
        for day, items in by_day.items():
            add_h(doc, f"3.{day.strftime('%d.%m.%Y')}")
            # сначала «важные», потом остальные (короткий список)
            important = [x for x in items if interesting_name(x["name"])]
            other = [x for x in items if x not in important]
            show = important[:25] + other[:10]
            for x in show:
                where = "N:" if str(x["path"]).upper().startswith("N:") else (
                    "Загрузки" if "Downloads" in str(x["path"]) else (
                        "Стол" if "Desktop" in str(x["path"]) else "ПК"
                    )
                )
                add_bul(
                    doc,
                    f"[{where}] {x['name']} — {format_work_span(x, start, end)}",
                )
            rest = len(items) - len(show)
            if rest > 0:
                add_bul(doc, f"… и ещё файлов: {rest}")

    add_h(doc, "4. СЭД")
    sed_tasks = stats.get("sed_tasks") or []
    if sed_tasks:
        n_acq = sum(1 for t in sed_tasks if t["action"] == "Ознакомиться")
        n_agr = sum(1 for t in sed_tasks if t["action"] == "Согласовать")
        n_exe = sum(1 for t in sed_tasks if t["action"] == "Исполнить")
        add_p(
            doc,
            f"Распознано задач СЭД: {len(sed_tasks)} "
            f"(Ознакомиться — {n_acq}, Согласовать — {n_agr}, Исполнить — {n_exe}). "
            f"Ознакомиться и Согласовать входят в «документов рассмотрено»; "
            f"Исполнить — в «мероприятия».",
            first=True,
        )
        for t in sed_tasks[:60]:
            add_bul(doc, f"{t['action']} — {t['title']}")
        if len(sed_tasks) > 60:
            add_bul(doc, f"… и ещё задач: {len(sed_tasks) - 60}")
        if sed:
            add_p(doc, "Исходные скриншоты:", first=False)
            for pth in sed:
                add_bul(doc, pth.name)
    elif sed:
        add_p(
            doc,
            "Найдены скриншоты, но задачи не распознаны (проверьте качество снимка). "
            "Перенесите формулировки вручную:",
        )
        for pth in sed:
            add_bul(doc, pth.name)
    else:
        add_p(
            doc,
            "Скриншот СЭД за период не найден. Сделайте снимок страницы задач "
            "(Ознакомиться / Согласовать / Исполнить) и положите PNG в папку «Загрузки» "
            "— при следующем запуске задачи попадут в итоги автоматически.",
        )

    add_h(doc, "5. Что дописать вручную перед сдачей")
    add_bul(doc, "Адреса объектов по фото (с табличек на снимках).")
    add_bul(
        doc,
        "Если скриншот СЭД был неполный — допишите задачи, которых не хватает.",
    )
    add_bul(doc, "Устные поручения руководства / совещания.")
    add_bul(
        doc,
        "При необходимости исправить числа: мероприятий / рассмотрено / разработано.",
    )

    add_h(doc, "6. Примечание")
    add_p(
        doc,
        f"Скрипт: Desktop\\Еженедельный_итог\\weekly_report.py. "
        f"Дата формирования: {today_s}.",
        first=True,
    )
    doc.add_paragraph()
    add_p(doc, "Ведущий инженер по промышленной безопасности СНиОТ", first=False)
    add_p(doc, "_________________ / В.В. Дубовик /", first=False)
    add_p(doc, today_s, first=False)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    return out_path


def main():
    args = parse_args()
    if args.date_from and args.date_to:
        start = dt.datetime.strptime(args.date_from, "%Y-%m-%d").replace(
            hour=7, minute=30
        )
        end = dt.datetime.strptime(args.date_to, "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        )
    else:
        start, end = week_bounds()

    print(f"Период: {start} — {end}")
    roots = [DESKTOP, DOWNLOADS, N_DUBOVIK]
    # не сканируем весь Documents (много Viber) — только если явно нужно
    docs = scan_docs(roots, start, end)
    # убрать сам отчёт и служебные файлы скрипта
    docs = [
        d
        for d in docs
        if "Еженедельный_итог" not in str(d["path"])
        and not d["name"].startswith("Отчёт_о_работе_Дубовик")
        and not d["name"].startswith("~$")
    ]
    docs = dedupe_editions(docs)
    photos = scan_photos(PHOTOS, start, end)
    sed = find_sed_screens(DOWNLOADS, start, end)
    print(f"Скриншотов СЭД: {len(sed)}")
    sed_info = extract_sed_tasks(sed)
    print(
        f"Задач СЭД распознано: {sed_info['n_total']} "
        f"(ознакомиться/согласовать={sed_info['n_reviewed']}, "
        f"исполнить={sed_info['n_execute']})"
    )

    stats = count_stats(docs, photos, start, end, sed_info=sed_info)
    stats["manual"] = False
    if args.events is not None:
        stats["events"] = args.events
        stats["manual"] = True
    if args.reviewed is not None:
        stats["reviewed_n"] = args.reviewed
        stats["manual"] = True
    if args.developed is not None:
        stats["developed_n"] = args.developed
        stats["manual"] = True

    print(
        f"Итоги: мероприятий={stats['events']}, "
        f"рассмотрено={stats['reviewed_n']}, "
        f"разработано={stats['developed_n']}"
    )

    name = (
        f"Отчёт_о_работе_Дубовик_ВВ_"
        f"{start.strftime('%d.%m.%Y')}-{end.strftime('%d.%m.%Y')}.docx"
    )
    out = Path(args.out) if args.out else DESKTOP / name
    path = build_report(start, end, docs, photos, sed, out, stats)
    print(f"Готово: {path}")

    if N_REPORTS.exists():
        try:
            dest = N_REPORTS / path.name
            shutil.copy2(path, dest)
            print(f"Копия: {dest}")
        except OSError as e:
            print(f"На N: не скопировано ({e})")

    # открыть папку / файл
    try:
        os.startfile(path)  # noqa: S606
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
