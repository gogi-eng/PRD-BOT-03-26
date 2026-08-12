# -*- coding: utf-8 -*-
"""
Проверка всего текста документа на соответствие правилам русского языка.

Источники помощи (как просил пользователь):
  https://languagetool.org/ru
  https://text.ru/spelling
  https://textovod.com/spelling
  https://morpher.ru/demo.aspx
  https://www.translate.ru/… (спряжение и склонение)

Практически агент использует:
  1) локальную чистку (латиница вместо кириллицы, частые опечатки);
  2) Яндекс.Спеллер (орфография) — официальный JSON API;
  3) LanguageTool (грамматика/стиль) — https://api.languagetool.org/v2/check
     с паузами и короткими фрагментами (лимиты публичного сервиса).

Автоматически исправляются только безопасные орфографические замены
(одно предложение и уверенность). Спорные места пишутся в отчёт.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from docx import Document
from docx.text.paragraph import Paragraph

from .common import iter_all_paragraphs, save_docx_unprotected, set_run_font

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
HELP_URLS = [
    "https://languagetool.org/ru",
    "https://text.ru/spelling",
    "https://textovod.com/spelling",
    "https://morpher.ru/demo.aspx",
    "https://www.translate.ru/спряжение%20и%20склонение/русский/онлайн",
]

# Латинские буквы, которые часто путают с русскими в Word
LATIN_TO_CYR = str.maketrans(
    {
        "A": "А",
        "a": "а",
        "B": "В",
        "E": "Е",
        "e": "е",
        "K": "К",
        "k": "к",
        "M": "М",
        "H": "Н",
        "O": "О",
        "o": "о",
        "P": "Р",
        "p": "р",
        "C": "С",
        "c": "с",
        "T": "Т",
        "X": "Х",
        "x": "х",
        "y": "у",
    }
)

# Частые канцелярские опечатки / нормы (локальный словарь)
from .russian_phrase_rules import apply_phrase_replacements

LOCAL_FIXES = [
    (r"\bв\s+течении\b", "в течение"),
    (r"\bнормативными\s+правовыми\b", "нормативно-правовыми"),
    (r"\bнормативные\s+правовые\b", "нормативно-правовые"),
    (r"\bнормативных\s+правовых\b", "нормативно-правовых"),
    (r"\bтехническими\s+нормативными\s+правовыми\b", "техническими нормативно-правовыми"),
    (r"\bпо\s+окончанию\b", "по окончании"),
    (r"\bсогласно\s+приказа\b", "согласно приказу"),
    (r"\bсогласно\s+распоряжения\b", "согласно распоряжению"),
    (r"\bсогласно\s+положения\b", "согласно положению"),
    (r"\bсогласно\s+инструкции\b", "согласно инструкции"),
    (r"\bсогласно\s+договора\b", "согласно договору"),
    (r"\bcоблюдает\b", "соблюдает"),
    (r"\bCНиОТ\b", "СНиОТ"),
    (r"\bCниот\b", "СНиОТ"),
    # порча агентом / спеллером составных терминов
    (r"\bтепло\s+установок\b", "теплоустановок"),
    (r"\bтепло\s+установки\b", "теплоустановки"),
    (r"\bпоэксплуатации\b", "по эксплуатации"),
    (r"\bэлектро\s+установок\b", "электроустановок"),
    (r"\bдля\s+исполнении\b", "для исполнения"),
    # падеж «опрессовка» после ложного спеллера
    (r"\(опрессовка\)", "(опрессовкам)"),
    (r"\bпри\s+опрессовка\b", "при опрессовках"),
    # типичная канцелярская ошибка (страдательный залог)
    (r"\bпроизводиться\b", "производится"),
    # хвост после порчи гиперссылки/автономера
    (r"плотность:\s*ремонта системы отопления\s*$", "плотность:"),
    (r"^ремонта системы отопления\d+\.\d+\.\s*", ""),
]

# Слова/аббревиатуры, которые спеллер часто «ломает» в наших документах
IGNORE_WORDS = {
    "сниот",
    "мктс",
    "ткп",
    "нпа",
    "прб",
    "ртс",
    "смат",
    "дсм",
    "окси",
    "иот",
    "ри",
    "ди",
    "вирочкин",
    "литвинов",
    "дубовик",
    "docx",
    "pdf",
}


def _set_paragraph_text(paragraph: Paragraph, text: str) -> None:
    """Перезаписать абзац целиком (в т.ч. убрать текст внутри гиперссылок)."""
    from formatters.structure_fix import _set_runs

    bold = paragraph.runs[0].bold if paragraph.runs else None
    _set_runs(paragraph, text, bold=bold)


def _log(msg: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with open(LOG_DIR / "russian_check.log", "a", encoding="utf-8") as f:
        f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n")


def fix_latin_lookalikes_in_russian(text: str) -> tuple[str, int]:
    """В русских словах заменить латинские «двойники» на кириллицу."""
    if not text:
        return text, 0
    n = 0

    def repl_word(m: re.Match) -> str:
        nonlocal n
        w = m.group(0)
        # есть и латиница, и кириллица — или чисто латиница длиной>=2 среди кириллицы вокруг
        if re.search(r"[A-Za-z]", w) and re.search(r"[А-Яа-яЁё]", w):
            fixed = w.translate(LATIN_TO_CYR)
            if fixed != w:
                n += 1
                return fixed
        # слово из латинских букв, которые все имеют кириллические двойники и похоже на русское
        if re.fullmatch(r"[A-Za-z]+", w) and 2 <= len(w) <= 20:
            mapped = w.translate(LATIN_TO_CYR)
            # если после замены осталась латиница — не трогаем (английское слово)
            if re.search(r"[A-Za-z]", mapped):
                return w
            # эвристика: часто cоблюдает, CНиОТ уже покрыты; общие короткие eng — пропуск
            if w.lower() in {"ok", "usb", "pdf", "word", "excel", "api", "http", "https"}:
                return w
            if mapped != w:
                n += 1
                return mapped
        return w

    new = re.sub(r"[A-Za-zА-Яа-яЁё]+", repl_word, text)
    return new, n


def apply_local_russian_fixes(text: str) -> tuple[str, list[str]]:
    details: list[str] = []
    text2, n_lat = fix_latin_lookalikes_in_russian(text)
    if n_lat:
        details.append(f"латиница→кириллица: {n_lat}")
        text = text2
    for pat, repl in LOCAL_FIXES:
        if repl is None:
            continue
        new, cnt = re.subn(pat, repl, text, flags=re.IGNORECASE)
        if cnt:
            details.append(f"локально: {pat} → {repl} (x{cnt})")
            text = new
    text2, phrase_details = apply_phrase_replacements(text)
    if phrase_details:
        details.extend(phrase_details)
        text = text2
    return text, details


def _http_json(url: str, data: dict | None = None, timeout: int = 25) -> dict | list:
    if data is None:
        req = urllib.request.Request(url, method="GET")
    else:
        body = urllib.parse.urlencode(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def yandex_speller_check(texts: list[str]) -> list[list[dict]]:
    """Орфография через Яндекс.Спеллер (удобный API для автопроверки)."""
    if not texts:
        return []
    # API принимает texts как повтор параметра
    params = [("lang", "ru"), ("options", "6")]  # IGNORE_URLS | IGNORE_DIGITS
    for t in texts:
        params.append(("text", t[:10000]))
    body = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(
        "https://speller.yandex.net/services/spellservice.json/checkTexts",
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def languagetool_check(text: str) -> list[dict]:
    """Грамматика/стиль через LanguageTool (публичный API, короткие куски)."""
    if not text.strip():
        return []
    data = {
        "language": "ru-RU",
        "text": text[:15000],
        "enabledOnly": "false",
    }
    raw = _http_json("https://api.languagetool.org/v2/check", data=data, timeout=40)
    return list(raw.get("matches") or [])


def _safe_spelling_replace(text: str, offset: int, length: int, suggestion: str) -> str | None:
    if offset < 0 or length <= 0 or offset + length > len(text):
        return None
    old = text[offset : offset + length]
    if not suggestion or suggestion == old:
        return None
    # не трогать аббревиатуры в ВЕРХНЕМ РЕГИСТРЕ длиннее 1
    if old.isupper() and len(old) <= 6:
        return None
    if old.lower() in IGNORE_WORDS or suggestion.lower() in IGNORE_WORDS:
        return None
    # не разрезать составные технические слова пробелом («теплоустановок» ≠ «тепло установок»)
    if " " not in old and " " in suggestion and re.fullmatch(r"[A-Za-zА-Яа-яЁё\-]+", old or ""):
        if any(
            k in old.lower()
            for k in (
                "теплоустанов",
                "электроустанов",
                "газопровод",
                "трубопровод",
                "теплообмен",
                "водоснабж",
            )
        ):
            return None
        # любое разрезание одного слова на два — подозрительно для спеллера
        if len(suggestion.split()) == 2 and abs(len(suggestion.replace(" ", "")) - len(old)) <= 1:
            return None
    # не менять падеж технических терминов (опрессовкам→опрессовка — ломает фразу)
    if " " not in old and " " not in suggestion:
        ol, sl = old.lower(), suggestion.lower()
        tech_stems = (
            "опрессовк",
            "теплоустанов",
            "электроустанов",
            "теплообмен",
            "газопровод",
            "трубопровод",
        )
        if any(ol.startswith(s) for s in tech_stems) and any(
            sl.startswith(s) for s in tech_stems
        ):
            if ol != sl:
                return None
    # только «похожее» исправление (не переписывать полпредложение)
    if abs(len(suggestion) - len(old)) > max(6, len(old)):
        return None
    return text[:offset] + suggestion + text[offset + length :]


def chunk_indices(n: int, size: int = 40) -> list[tuple[int, int]]:
    out = []
    i = 0
    while i < n:
        out.append((i, min(i + size, n)))
        i += size
    return out


def check_and_fix_document(
    docx_path: str,
    *,
    use_yandex: bool = True,
    use_languagetool: bool = True,
    auto_fix: bool = True,
    lt_max_paragraphs: int = 25,
) -> dict:
    """
    Перечитать весь документ, проверить русский язык, по возможности исправить.
    Возвращает отчёт.
    """
    doc = Document(docx_path)
    paras = [p for p in iter_all_paragraphs(doc) if p.text and p.text.strip()]
    report = {
        "paragraphs": len(paras),
        "local_fixes": 0,
        "speller_fixes": 0,
        "grammar_notes": 0,
        "details": [],
        "suggestions": [],
        "help_urls": HELP_URLS,
        "output": docx_path,
    }

    # 1) локальный проход по каждому абзацу
    for p in paras:
        original = p.text
        text, details = apply_local_russian_fixes(original)
        if text != original and auto_fix:
            _set_paragraph_text(p, text)
            report["local_fixes"] += len(details)
            report["details"].extend(details[:3])

    # обновить список после локальных правок
    paras = [p for p in iter_all_paragraphs(doc) if p.text and p.text.strip()]
    texts = [p.text for p in paras]

    # 2) Яндекс.Спеллер пакетами
    if use_yandex and texts:
        try:
            for a, b in chunk_indices(len(texts), 35):
                batch = texts[a:b]
                try:
                    results = yandex_speller_check(batch)
                except Exception as e:
                    _log(f"yandex fail: {e}")
                    report["details"].append(f"Яндекс.Спеллер недоступен: {e}")
                    break
                for i, errors in enumerate(results):
                    if not errors:
                        continue
                    idx = a + i
                    text = texts[idx]
                    # правки с конца, чтобы оффсеты не плыли
                    for err in sorted(errors, key=lambda x: -int(x.get("pos", 0))):
                        word = err.get("word") or ""
                        pos = int(err.get("pos", -1))
                        length = int(err.get("len", len(word)))
                        suggs = err.get("s") or []
                        if not suggs:
                            report["suggestions"].append(
                                f"орфография?: «{word}» (абз. {idx + 1}) — проверьте на text.ru / LanguageTool"
                            )
                            continue
                        best = suggs[0]
                        if word.lower() in IGNORE_WORDS:
                            continue
                        note = f"орфография: «{word}» → «{best}»"
                        if auto_fix and len(suggs) == 1:
                            fixed = _safe_spelling_replace(text, pos, length, best)
                            if fixed is not None:
                                text = fixed
                                report["speller_fixes"] += 1
                                report["details"].append(note)
                                continue
                        report["suggestions"].append(note + f" (абз. {idx + 1})")
                    texts[idx] = text
                    if auto_fix and texts[idx] != paras[idx].text:
                        _set_paragraph_text(paras[idx], texts[idx])
                time.sleep(0.35)
        except Exception as e:
            _log(f"yandex block: {e}")
            report["details"].append(f"Ошибка орфографии: {e}")

    # 3) LanguageTool — выборочно по абзацам с кириллицей (с паузами)
    if use_languagetool:
        checked = 0
        for i, p in enumerate(paras):
            t = p.text.strip()
            if len(t) < 25 or not re.search(r"[А-Яа-яЁё]{4,}", t):
                continue
            # не гонять весь роман: лимит из настроек (публичный API LanguageTool)
            if checked >= lt_max_paragraphs:
                report["details"].append(
                    f"LanguageTool: проверены {lt_max_paragraphs} абзацев; "
                    "полная допроверка — на https://languagetool.org/ru и https://text.ru/spelling"
                )
                break
            try:
                matches = languagetool_check(t)
                time.sleep(3.1)  # уважать лимит публичного API (~20 req/min)
            except Exception as e:
                _log(f"LT fail: {e}")
                report["details"].append(
                    "LanguageTool временно недоступен. Проверьте текст на https://languagetool.org/ru"
                )
                break
            checked += 1
            # применяем только явные орфографические с 1 вариантом
            text = p.text
            for m in sorted(matches, key=lambda x: -int(x.get("offset", 0))):
                offset = int(m.get("offset", -1))
                length = int(m.get("length", 0))
                reps = [r.get("value") for r in (m.get("replacements") or []) if r.get("value")]
                msg = (m.get("message") or "").strip()
                rule = ((m.get("rule") or {}).get("issueType") or "").lower()
                cat = ((m.get("rule") or {}).get("category") or {}).get("id", "")
                snippet = text[offset : offset + length] if offset >= 0 else ""
                if not reps:
                    report["suggestions"].append(f"LT: {msg} «{snippet}» (абз. {i + 1})")
                    report["grammar_notes"] += 1
                    continue
                is_spell = rule in ("misspelling",) or "TYPOS" in str(cat).upper() or "орфограф" in msg.lower()
                if auto_fix and is_spell and len(reps) == 1:
                    fixed = _safe_spelling_replace(text, offset, length, reps[0])
                    if fixed is not None:
                        text = fixed
                        report["speller_fixes"] += 1
                        report["details"].append(f"LT орфография: «{snippet}» → «{reps[0]}»")
                        continue
                report["suggestions"].append(
                    f"LT: {msg} «{snippet}» → {reps[0]} (абз. {i + 1})"
                )
                report["grammar_notes"] += 1
            if text != p.text:
                _set_paragraph_text(p, text)

    if auto_fix:
        save_docx_unprotected(doc, docx_path)

    # отчёт на диск
    LOG_DIR.mkdir(exist_ok=True)
    rep_path = LOG_DIR / f"russian_check_{datetime.now():%Y%m%d_%H%M%S}.txt"
    lines = [
        "Проверка русского языка",
        f"Файл: {docx_path}",
        f"Абзацев: {report['paragraphs']}",
        f"Локальных правок: {report['local_fixes']}",
        f"Орфография исправлена: {report['speller_fixes']}",
        f"Замечаний (грамматика/спорные): {report['grammar_notes']}",
        "",
        "Сайты для ручной допроверки:",
        *[f"  - {u}" for u in HELP_URLS],
        "",
        "Исправления:",
        *report["details"][:80],
        "",
        "На проверку / сомнительные:",
        *report["suggestions"][:120],
    ]
    rep_path.write_text("\n".join(lines), encoding="utf-8")
    report["report_path"] = str(rep_path)
    _log(
        f"done file={docx_path} local={report['local_fixes']} "
        f"spell={report['speller_fixes']} notes={report['grammar_notes']}"
    )
    return report
