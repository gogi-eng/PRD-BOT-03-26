# -*- coding: utf-8 -*-
"""
Актуальные названия и номера инструкций предприятия.

ОСНОВНЫЕ источники (не папка файлов!):
1) N:\\…\\Перечень инструкций по ОТ.pdf
2) Desktop\\DocAgent_test_operator\\Приказ по инструкциям.docx
   (приказ перекрывает перечень по номеру)

Копии лежат в DocAgent\\etalons\\.
Папка N:\\…\\Инструкции — только запасной источник.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "learned_instruction_catalog.json"

DEFAULT_INSTRUCTIONS_DIR = Path(
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\Инструкции"
)

FILE_NAME_RE = re.compile(
    r"^(?P<num>\d{1,4})\s*(?P<title>.+)$",
    re.IGNORECASE,
)

# ссылки в тексте: «инструкция … № 47 …» / «ИОТ 47» (номер обязателен через №, для ИОТ — можно без)
REF_WITH_NUM_RE = re.compile(
    r"(?i)(?:"
    r"(?P<head>инструкци\w*(?:\s+по\s+охране\s+труда|\s+по\s+от)?)\s*"
    r"(?:№|N[oо]?\.?|No\.?)\s*(?P<num>\d{1,4})"
    r"|(?P<head2>иот)\s*(?:№|N[oо]?\.?|No\.?)?\s*(?P<num2>\d{1,4})"
    r")"
    r"(?:\s*[«\"„]\s*(?P<quoted>[^»\"“”]+)\s*[»\"“”])?"
)

# В перечнях: «ИОТ 116 «…» ОТ При транспортировке…;» → только полное название из папки
IOT_LIST_MESSY_RE = re.compile(
    r"(?i)\bиот\s*(?:№|N[oо]?\.?)?\s*(?P<num>\d{1,4})"
    r"(?:\s*[«\"„]\s*(?P<quoted>[^»\"“”]+)\s*[»\"“”])?"
    r"(?:\s*ОТ\b\s*(?P<ot>[^;.]*))?"
    r"(?P<punct>\s*[;.]?)"
)

# «ИОТ При работе на сверлильном станке;» — без номера
IOT_LIST_NO_NUM_RE = re.compile(
    r"(?i)^\s*иот\s+(?!\d)(?!№)"
    r"(?P<title>.+?)"
    r"(?P<punct>\s*[;.]?)\s*$"
)

# уже раскрытое, но урезанное: «Инструкция по охране труда «Слесаря_по_рем…»;»
IOT_ALREADY_FULL_RE = re.compile(
    r"(?i)^\s*инструкция\s+по\s+охране\s+труда\s*"
    r"[«\"„]\s*(?P<title>[^»\"“”]+)\s*[»\"“”]"
    r"(?P<punct>\s*[;.]?)\s*$"
)

# «инструкция по охране труда для оператора котельной» без номера
REF_BY_TITLE_RE = re.compile(
    r"(?i)(инструкци\w*\s+по\s+охране\s+труда\s+)"
    r"(?P<title>для\s+[А-ЯЁа-яёA-Za-z\- ]{3,80}?)"
    r"(?=;|,|\.|$|\s+и\s|\s+при\s)"
)

ARCHIVE_MARKERS = (
    "новая папка",
    "архив",
    "_2019",
    "2019",
    "old",
    "устарев",
)

SKIP_NAME_PARTS = (
    "титул",
    "перечен",
    "реестр",
    "thumbs",
    "lock",
)


def _clean_title(raw: str) -> str:
    """Короткое название из имени файла → первая заглавная, дальше строчные."""
    t = re.sub(r"\s+", " ", (raw or "").strip(" .-_"))
    t = t.replace("_", " ")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(
        r"^(от\s+)?(инструкция\s+)?(по\s+охране\s+труда\s+)?",
        "",
        t,
        flags=re.IGNORECASE,
    ).strip(" .-_")
    t = re.sub(r"^от\s+", "", t, flags=re.IGNORECASE).strip()
    # убрать хвосты файловых сокращений вида «ае&т»
    t = re.sub(r"\s*\([^)]*&\w+\)\s*", " ", t).strip()
    t = re.sub(r"\.{2,}", "…", t)
    return format_instruction_title_case(t)


# Файловые сокращения → полное наименование (по эталону СМАТ)
KNOWN_TITLE_FIXES = {
    "слесаря по рем автомоб": "Для слесаря по ремонту автомобилей",
    "слесаря по рем. автомоб": "Для слесаря по ремонту автомобилей",
    "слесаря по ремонту маш и тр": (
        "Для слесаря по ремонту дорожно-строительных машин и тракторов"
    ),
    "слесаря по ремонту маш. и тр": (
        "Для слесаря по ремонту дорожно-строительных машин и тракторов"
    ),
    "слесаря по топл аппаратуре": "Для слесаря по топливной аппаратуре",
    "слесаря по топл.аппаратуре": "Для слесаря по топливной аппаратуре",
    "при работе с ручным эл инструментом 2 класса": (
        "При работе с ручным электрифицированным инструментом 2-го класса"
    ),
    "при использовании грузоподъемных машин управляемых с пола": (
        "При использовании грузоподъёмных машин, управляемых с пола"
    ),
}


def _apply_known_title_fix(title: str) -> str:
    raw = title or ""
    raw_l = raw.lower().replace("ё", "е")
    if "для лиц" in raw_l and ("подъемник" in raw_l or "подъёмник" in raw_l or "ае&т" in raw_l):
        return (
            "Для лиц, занимающихся выполнением работ по техническому обслуживанию "
            "и ремонту автомобилей с использованием подъёмника"
        )
    t = _clean_title(raw)
    key = re.sub(r"[^а-яёa-z0-9]+", " ", t.lower().replace("ё", "е")).strip()
    key = re.sub(r"\s+", " ", key)
    for k, v in KNOWN_TITLE_FIXES.items():
        kk = re.sub(r"[^а-яёa-z0-9]+", " ", k.lower().replace("ё", "е")).strip()
        kk = re.sub(r"\s+", " ", kk)
        if key == kk or key.startswith(kk + " ") or kk == key:
            return v
    if key.startswith("для лиц") and ("…" in t or "..." in raw):
        return (
            "Для лиц, занимающихся выполнением работ по техническому обслуживанию "
            "и ремонту автомобилей с использованием подъёмника"
        )
    return t


def format_instruction_title_case(title: str) -> str:
    """
    Название инструкции: регистр при сравнении не важен;
    в тексте — первая буква заглавная, остальные строчные.
    Пример: «ДЛЯ ОПЕРАТОРА…» / «для Оператора…» → «Для оператора…»
    """
    t = re.sub(r"\s+", " ", (title or "").strip())
    if not t:
        return t
    low = t.lower()
    for i, ch in enumerate(low):
        if ch.isalpha():
            return low[:i] + ch.upper() + low[i + 1 :]
    return low


def _title_key(title: str) -> str:
    """Ключ сравнения названий — без учёта регистра и лишней пунктуации."""
    t = (title or "").lower().replace("ё", "е")
    t = re.sub(r"^(для\s+|от\s+)", "", t)
    t = re.sub(r"[^а-яa-z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def titles_match_ignore_case(a: str, b: str) -> bool:
    """Сравнение названий инструкций без учёта регистра."""
    return _title_key(a) == _title_key(b) and bool(_title_key(a))


def expand_instruction_abbreviations(text: str) -> str:
    """Убрать сокращения ИОТ/ОТ в названиях — только полные слова."""
    t = text or ""
    t = re.sub(r"\bИОТ\b", "инструкция по охране труда", t, flags=re.IGNORECASE)
    # «ОТ» как отдельное слово (не часть «ОТветственность»)
    t = re.sub(r"(?<![А-Яа-яЁёA-Za-z])ОТ(?![А-Яа-яЁёA-Za-z])", "охране труда", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def canonical_instruction_list_name(info: dict) -> str:
    """
    Наиболее полное наименование для перечня инструкций.
    БЕЗ номера и БЕЗ сокращений (ИОТ / ОТ).
    Источник истины: Перечень PDF + Приказ DOCX (не папка файлов).
    """
    if info.get("full_name"):
        fn = re.sub(r"\s*№\s*\d+\s*", " ", str(info["full_name"]), flags=re.I)
        fn = re.sub(r"\s+", " ", fn).strip()
        if fn.lower().startswith("инструкция по охране труда"):
            return fn
    title = _apply_known_title_fix(info.get("title") or "")
    title = format_instruction_title_case(title)
    low = title.lower()
    if low.startswith("инструкция по охране труда"):
        title = re.sub(r"\s*№\s*\d+\s*", " ", title, flags=re.I)
        title = re.sub(r"\s+", " ", title).strip()
        return title
    return f"Инструкция по охране труда «{title}»"


def _pick_best_list_title(*candidates: str) -> str:
    """Выбрать наиболее полное наименование (длиннее, без мусора файловых имён)."""
    best = ""
    best_score = -1
    for raw in candidates:
        t = _apply_known_title_fix(raw or "")
        if not t or len(t) < 3:
            continue
        score = len(t)
        if "_" in (raw or ""):
            score -= 20
        if re.search(r"[A-Za-z]&", raw or ""):
            score -= 30
        if "…" in t or "..." in (raw or ""):
            score -= 15
        if re.search(r"\b(рем|маш|топл|апп)\b", t, re.I) and len(t) < 40:
            score -= 10
        if score > best_score:
            best_score = score
            best = t
    return best


def apply_instruction_catalog_to_text(
    text: str,
    catalog: dict,
) -> tuple[str, list[str]]:
    """
    Сверить/поправить ссылки на инструкции в одном абзаце.
    В перечислениях: без «ИОТ», без номеров — только наиболее полное наименование.
    Если номера нет в каталоге — взять текст после «ОТ» / из кавычек.
    """
    if not text:
        return text, []
    notes: list[str] = []
    out = text
    catalog = catalog or {}

    def _with_punct(canon: str, punct: str) -> str:
        punct = punct or ""
        if punct.strip() == ";":
            return canon + ";"
        if punct.strip() == ".":
            return canon + "."
        return canon + (punct if punct else "")

    def _canon_from_title(title: str) -> str:
        return canonical_instruction_list_name({"title": title})

    def _repl_iot_messy(m: re.Match) -> str:
        num = m.group("num")
        quoted = (m.group("quoted") or "").strip()
        ot = (m.group("ot") or "").strip()
        info = lookup_by_number(catalog, num) if catalog.get("by_number") else None
        if info:
            # официальный перечень / приказ — главный источник
            canon = canonical_instruction_list_name(info)
            notes.append(f"ИОТ {num} → из Перечня/Приказа: {canon}")
            return _with_punct(canon, m.group("punct") or "")
        cat_title = ""
        best = _pick_best_list_title(ot, quoted, cat_title)
        if not best:
            notes.append(f"ИОТ {num}: нет в Перечне/Приказе и нет текста ОТ")
            return m.group(0)
        canon = _canon_from_title(best)
        notes.append(f"ИОТ {num} (нет в Перечне/Приказе) → из текста ОТ: {canon}")
        return _with_punct(canon, m.group("punct") or "")

    out = IOT_LIST_MESSY_RE.sub(_repl_iot_messy, out)

    def _repl_iot_no_num(m: re.Match) -> str:
        title = (m.group("title") or "").strip()
        title = re.sub(r"^(от\s+)", "", title, flags=re.I).strip()
        if not title:
            return m.group(0)
        info = lookup_by_title(catalog, title) if catalog.get("by_title_key") else None
        best = _pick_best_list_title(title, (info or {}).get("title") or "")
        canon = _canon_from_title(best)
        notes.append(f"ИОТ без номера → {canon}")
        return _with_punct(canon, m.group("punct") or "")

    stripped = out.strip()
    if IOT_LIST_NO_NUM_RE.match(stripped):
        out = IOT_LIST_NO_NUM_RE.sub(_repl_iot_no_num, stripped)

    def _repl_already_full(m: re.Match) -> str:
        title = (m.group("title") or "").strip()
        cleaned = _clean_title(title)
        info = lookup_by_title(catalog, cleaned) if catalog.get("by_title_key") else None
        best = _pick_best_list_title(cleaned, (info or {}).get("title") or "")
        canon = _canon_from_title(best or cleaned or title)
        orig = m.group(0).strip()
        with_p = _with_punct(canon, m.group("punct") or "")
        if with_p != orig:
            notes.append(f"нормализовано название → {canon}")
            return with_p
        return m.group(0)

    stripped = out.strip()
    if IOT_ALREADY_FULL_RE.match(stripped):
        out = IOT_ALREADY_FULL_RE.sub(_repl_already_full, stripped)

    if catalog.get("by_number"):

        def _repl_num(m: re.Match) -> str:
            num = m.group("num") or m.group("num2")
            info = lookup_by_number(catalog, num)
            if not info:
                notes.append(f"№ {num}: нет в каталоге Инструкции СНиОТ")
                return m.group(0)
            full = canonical_instruction_list_name(info)
            notes.append(f"ссылка №/ИОТ {num} → {full}")
            return full

        out = REF_WITH_NUM_RE.sub(_repl_num, out)

        def _repl_title(m: re.Match) -> str:
            title = m.group("title").strip().rstrip(" .;")
            start = m.start()
            window = out[max(0, start - 12) : start]
            if re.search(r"(?:№|N)\s*\d{1,4}\s*$", window, re.I):
                return m.group(0)
            info = lookup_by_title(catalog, title)
            if not info:
                return m.group(0)
            full = canonical_instruction_list_name(info)
            notes.append(f"по названию «{title}» → {full}")
            return full

        out = REF_BY_TITLE_RE.sub(_repl_title, out)

    return out, notes


def _path_score(rel: Path, unit_hint: str = "") -> int:
    """Чем выше — тем актуальнее экземпляр файла."""
    s = 0
    low = str(rel).lower().replace("\\", "/")
    for m in ARCHIVE_MARKERS:
        if m in low:
            s -= 100
            break
    parts0 = rel.parts[0].lower() if rel.parts else ""
    if parts0 == "инструкции по от":
        s += 25
    if unit_hint and unit_hint.lower() in low:
        s += 40
    suf = rel.suffix.lower()
    if suf == ".docx":
        s += 6
    elif suf == ".doc":
        s += 3
    elif suf == ".pdf":
        s += 1
    s -= max(0, len(rel.parts) - 2) * 2
    return s


def scan_instructions_dir(
    instructions_dir: Path | None = None,
    *,
    unit_hint: str = "",
) -> dict:
    """
    Сканировать папку Инструкции.
    Возвращает dict: by_number, by_title_key, source, scanned_at, files.
    """
    root = Path(instructions_dir or DEFAULT_INSTRUCTIONS_DIR)
    report = {
        "source": str(root),
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "exists": root.exists(),
        "by_number": {},
        "by_title_key": {},
        "files": 0,
        "unique_numbers": 0,
    }
    if not root.exists():
        return report

    candidates: dict[str, list[tuple[int, str, str]]] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        name_l = p.name.lower()
        if name_l.startswith("~$") or name_l.startswith(".~"):
            continue
        if p.suffix.lower() not in {".docx", ".doc", ".pdf", ".rtf"}:
            continue
        if any(x in name_l for x in SKIP_NAME_PARTS):
            continue
        m = FILE_NAME_RE.match(p.stem.strip())
        if not m:
            continue
        num = str(int(m.group("num")))
        title = _clean_title(m.group("title"))
        if not title or len(title) < 3:
            continue
        try:
            rel = p.relative_to(root)
        except ValueError:
            rel = Path(p.name)
        score = _path_score(rel, unit_hint=unit_hint)
        try:
            score += min(20, int(p.stat().st_mtime) // 10_000_000)
        except OSError:
            pass
        candidates.setdefault(num, []).append(
            (score, title, str(rel).replace("\\", "/"))
        )
        report["files"] += 1

    by_number: dict[str, dict] = {}
    by_title: dict[str, str] = {}
    for num, items in candidates.items():
        # приоритет: актуальность (score), затем самое полное (длинное) название
        items.sort(key=lambda x: (x[0], len(x[1])), reverse=True)
        best_score = items[0][0]
        top = [it for it in items if it[0] >= best_score - 15]
        best_score, best_title, best_path = max(top, key=lambda x: len(x[1]))
        title_fmt = format_instruction_title_case(best_title)
        by_number[num] = {
            "number": num,
            "title": title_fmt,
            "path": best_path,
            "score": best_score,
            # полное имя для перечня — БЕЗ номера и без сокращений ИОТ/ОТ
            "full_name": canonical_instruction_list_name(
                {"number": num, "title": title_fmt}
            ),
        }
        key = _title_key(best_title)
        if key and key not in by_title:
            by_title[key] = num

    report["by_number"] = by_number
    report["by_title_key"] = by_title
    report["unique_numbers"] = len(by_number)
    return report


def save_catalog(catalog: dict, path: Path | None = None) -> Path:
    out = path or CACHE_PATH
    slim = {
        "source": catalog.get("source"),
        "scanned_at": catalog.get("scanned_at"),
        "unique_numbers": catalog.get("unique_numbers", 0),
        "files": catalog.get("files", 0),
        "by_number": catalog.get("by_number", {}),
        "by_title_key": catalog.get("by_title_key", {}),
        "notes_ru": [
            "Каталог актуальных инструкций СНиОТ. Источник — папка Инструкции.",
            "Названия и номера в ДИ/РИ сверять только с этим каталогом.",
        ],
    }
    out.write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_catalog(
    *,
    instructions_dir: Path | None = None,
    force_rescan: bool = False,
    unit_hint: str = "",
    max_age_hours: float = 24.0,
) -> dict:
    """
    Загрузить каталог названий инструкций.

    ОСНОВНОЙ источник (обязательно):
      — Перечень инструкций по ОТ.pdf
      — Приказ по инструкциям.docx (перекрывает по номеру)
    Папка N:\\…\\Инструкции — только запасной источник, если официальных нет.
    """
    from formatters.official_instruction_lists import (
        CACHE_OFFICIAL,
        build_official_catalog,
    )

    if not force_rescan and CACHE_OFFICIAL.exists():
        try:
            data = json.loads(CACHE_OFFICIAL.read_text(encoding="utf-8"))
            scanned = data.get("scanned_at") or ""
            age_ok = True
            if scanned:
                try:
                    dt = datetime.fromisoformat(scanned)
                    age_ok = (datetime.now() - dt).total_seconds() < max_age_hours * 3600
                except ValueError:
                    age_ok = False
            if age_ok and data.get("source") == "official_lists" and data.get("by_number"):
                return data
        except Exception:
            pass

    try:
        official = build_official_catalog()
        if official.get("by_number"):
            # дополнительно сохранить в старый кэш для совместимости
            try:
                save_catalog(official)
            except Exception:
                pass
            return official
    except Exception:
        pass

    # запас: старая папка файлов (если официальные источники недоступны)
    root = Path(instructions_dir or DEFAULT_INSTRUCTIONS_DIR)
    if not force_rescan and CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            if data.get("by_number"):
                return data
        except Exception:
            pass
    catalog = scan_instructions_dir(root, unit_hint=unit_hint)
    if catalog.get("by_number"):
        try:
            save_catalog(catalog)
        except Exception:
            pass
    return catalog


def detect_unit_hint(path_or_text: str = "") -> str:
    """Подсказка подразделения из пути/текста (РТС-4, ТУ, АС…)."""
    s = (path_or_text or "").upper().replace(" ", "")
    m = re.search(r"РТС-?\s*(\d)", s, re.I)
    if m:
        return f"РТС-{m.group(1)}"
    for u in (
        "СМАТ",
        "ТУ",
        "АС",
        "АРС",
        "АХО",
        "ПЦ",
        "СУ",
        "СЭХ",
        "УОО",
        "ХЛ",
        "ЛСиМ",
    ):
        if u.upper().replace(" ", "") in s:
            return u
    return ""


def lookup_by_number(catalog: dict, num: str | int) -> dict | None:
    try:
        key = str(int(str(num).strip()))
    except ValueError:
        return None
    return (catalog.get("by_number") or {}).get(key)


def lookup_by_title(catalog: dict, title: str) -> dict | None:
    key = _title_key(title)
    if not key:
        return None
    by_t = catalog.get("by_title_key") or {}
    num = by_t.get(key)
    if not num:
        for k, n in by_t.items():
            if key in k or k in key:
                if abs(len(k) - len(key)) <= 12:
                    num = n
                    break
    if not num:
        return None
    return lookup_by_number(catalog, num)
