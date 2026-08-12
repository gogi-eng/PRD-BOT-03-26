# -*- coding: utf-8 -*-
"""
Официальные источники названий инструкций по ОТ.

НЕ папка «Инструкции», а:
1) Перечень инструкций по ОТ.pdf (полный перечень)
2) Приказ по инструкциям.docx (утверждённые/обновлённые — приоритетнее)

PDF — скан; полный разбор кэшируется в JSON.
Приказ читается из таблицы Word напрямую.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ETALONS = ROOT / "etalons"
CACHE_OFFICIAL = ETALONS / "official_instruction_catalog.json"

PERECHEN_PDF_PRIMARY = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Положения, инструкции, графики, перечни"
    r"\Перечень инструкций по ОТ.pdf"
)
PERECHEN_PDF_LOCAL = ETALONS / "Перечень инструкций по ОТ.pdf"

PRIKAZ_DOCX_PRIMARY = Path(
    r"C:\Users\v.dubovik\Desktop\DocAgent_test_operator\Приказ по инструкциям.docx"
)
PRIKAZ_DOCX_LOCAL = ETALONS / "Приказ по инструкциям.docx"

NUM_RE = re.compile(r"(?i)(?P<num>\d{1,4})\s*ОТ\b")
TITLE_PREFIX_RE = re.compile(
    r"(?i)^\s*инструкция\s+по\s+охране\s+труда\s+"
)


def resolve_perechen_pdf() -> Path:
    if PERECHEN_PDF_PRIMARY.is_file():
        return PERECHEN_PDF_PRIMARY
    return PERECHEN_PDF_LOCAL


def resolve_prikaz_docx() -> Path:
    if PRIKAZ_DOCX_PRIMARY.is_file():
        return PRIKAZ_DOCX_PRIMARY
    return PRIKAZ_DOCX_LOCAL


def _short_title(full_name: str) -> str:
    """«Инструкция по охране труда для …» → «Для …» / «При …»."""
    t = re.sub(r"\s+", " ", (full_name or "").strip())
    t = TITLE_PREFIX_RE.sub("", t).strip(" .;")
    # убрать случайный дубль «для для»
    t = re.sub(r"(?i)^для\s+для\s+", "Для ", t)
    if t and t[0].islower():
        t = t[0].upper() + t[1:]
    return t


def _full_name_from_parts(title_or_full: str) -> str:
    t = re.sub(r"\s+", " ", (title_or_full or "").strip().rstrip(";."))
    if not t:
        return t
    low = t.lower()
    if low.startswith("инструкция по охране труда"):
        t = re.sub(r"(?i)\s*№\s*\d+\s*", " ", t)
        t = re.sub(r"\s+", " ", t).strip()
        return t
    return f"Инструкция по охране труда {t[0].lower() + t[1:]}" if t[0].isupper() and t.lower().startswith(("для ", "при ")) else (
        f"Инструкция по охране труда «{_short_title(t)}»"
        if not low.startswith(("для ", "при "))
        else f"Инструкция по охране труда {t[0].lower() + t[1:]}"
    )


def normalize_official_entry(num: str, name: str, *, source: str) -> dict:
    """Единый вид записи каталога."""
    raw = re.sub(r"\s+", " ", (name or "").strip())
    raw = re.sub(r"(?i)^для\s+для\s+", "для ", raw)
    if not raw.lower().startswith("инструкция по охране труда"):
        # «для …» / «при …» → полное
        if raw.lower().startswith(("для ", "при ")):
            full = f"Инструкция по охране труда {raw[0].lower() + raw[1:]}"
        else:
            full = f"Инструкция по охране труда «{raw}»"
    else:
        full = raw
    # канон для перечня в теле ДИ/РИ — без номера, в кавычках короткое имя
    short = _short_title(full)
    # предпочтительная форма в тексте: Инструкция по охране труда «Короткое»
    list_name = f"Инструкция по охране труда «{short}»"
    return {
        "number": str(int(str(num).strip())),
        "title": short,
        "full_name": list_name,
        "official_full": full,
        "source": source,
    }


def parse_prikaz_instructions_docx(path: Path | None = None) -> dict[str, dict]:
    """Таблица приказа: Наименование | № инструкции (104 ОТ)."""
    from docx import Document

    path = Path(path or resolve_prikaz_docx())
    out: dict[str, dict] = {}
    if not path.is_file():
        return out
    doc = Document(str(path))
    for table in doc.tables:
        if not table.rows:
            continue
        header = " ".join((c.text or "").lower() for c in table.rows[0].cells)
        if "наименован" not in header and "инструкц" not in header:
            # всё равно проверить строки на «N ОТ»
            pass
        for row in table.rows[1:]:
            cells = [(c.text or "").strip().replace("\n", " ") for c in row.cells]
            if len(cells) < 2:
                continue
            # найти ячейку с номером «N ОТ» и ячейку с названием
            num = None
            name = None
            for c in cells:
                m = NUM_RE.search(c)
                if m and len(c) < 20:
                    num = m.group("num")
                elif "инструкц" in c.lower() or c.lower().startswith(("для ", "при ")):
                    name = c
            if not num:
                for c in cells:
                    m = NUM_RE.search(c)
                    if m:
                        num = m.group("num")
                        break
            if not name:
                # обычно средняя колонка
                for c in cells:
                    if len(c) > 20 and not NUM_RE.fullmatch(c.replace(" ", "")):
                        name = c
                        break
            if num and name:
                out[str(int(num))] = normalize_official_entry(
                    num, name, source=f"prikaz:{path.name}"
                )
    return out


def load_perechen_seed() -> dict[str, dict]:
    """
    Полный перечень из утверждённого PDF (скан) — загружается из JSON-семени.
    Файл обновляется скриптом build_official_catalog / вручную при смене PDF.
    """
    seed = ETALONS / "perechen_ot_seed.json"
    if not seed.is_file():
        return {}
    data = json.loads(seed.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for num, name in (data.get("by_number") or data).items():
        if isinstance(name, dict):
            out[str(int(num))] = normalize_official_entry(
                num,
                name.get("official_full") or name.get("full_name") or name.get("title") or "",
                source="perechen_pdf",
            )
        else:
            out[str(int(num))] = normalize_official_entry(
                num, str(name), source="perechen_pdf"
            )
    return out


def build_official_catalog() -> dict:
    """
    Собрать каталог: Перечень PDF (семя) + Приказ (перекрывает по номеру).
    """
    by_number: dict[str, dict] = {}
    by_number.update(load_perechen_seed())
    prikaz = parse_prikaz_instructions_docx()
    by_number.update(prikaz)  # приказ важнее

    by_title: dict[str, str] = {}
    for num, info in by_number.items():
        key = re.sub(
            r"[^а-яa-z0-9]+",
            " ",
            (info.get("title") or "").lower().replace("ё", "е"),
        ).strip()
        key = re.sub(r"\s+", " ", key)
        if key and key not in by_title:
            by_title[key] = num

    report = {
        "source": "official_lists",
        "sources": {
            "perechen_pdf": str(resolve_perechen_pdf()),
            "prikaz_docx": str(resolve_prikaz_docx()),
            "seed": str(ETALONS / "perechen_ot_seed.json"),
        },
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "exists": True,
        "by_number": by_number,
        "by_title_key": by_title,
        "files": 2,
        "unique_numbers": len(by_number),
        "notes_ru": [
            "Названия инструкций брать ТОЛЬКО из Перечня инструкций по ОТ.pdf "
            "и Приказа по инструкциям.docx (приказ перекрывает перечень по номеру).",
            "Папка N:\\…\\Инструкции — НЕ источник названий.",
        ],
    }
    try:
        CACHE_OFFICIAL.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        pass
    return report
