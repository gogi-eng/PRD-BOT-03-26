# -*- coding: utf-8 -*-
"""Сравнение документов «до / после правок Дубовика» и извлечение паттернов."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

OUT_DIR = Path(r"C:\Users\v.dubovik\DocAgent")
LOG = OUT_DIR / "logs" / "diff_patterns.txt"
JSON_OUT = OUT_DIR / "learned_edit_patterns.json"

try:
    import win32com.client  # type: ignore
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pywin32", "-q"])
    import win32com.client  # type: ignore

from docx import Document

PAIRS = [
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ДС\Должн. инженера 1 категории ДС 04.08.2025.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ДС\Должн. инженера 1 категории ДС 04.08.2025-Дубовик.doc",
    ),
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ЛСиМ\ДИ ведущий инженер  2024.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ЛСиМ\ДИ ведущий инженер  2024 - СНиОТ 29.05.2024.doc",
    ),
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\Ведущий специалист по моб. подготовке\2.1.Обязанности Серов-2025 основной — 1.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\Ведущий специалист по моб. подготовке\2.1.Обязанности Серов-2025 основной (СНиОТ 17.10.2025).doc",
    ),
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ОИиОР\Борисов.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\ОИиОР\Борисов  исправленно.doc",
    ),
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\Положение о СНиОТ.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\Положение о СНиОТ-ред Дубовика.doc",
    ),
    (
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\АС\РАБОЧАЯ ИНСТРУКЦИЯ водителю автомобиля 4 разряда абонентской службы - CНиОТ 20.02.2025.doc",
        r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ\АС\РАБОЧАЯ ИНСТРУКЦИЯ водителю автомобиля 4-го разряда абонентской службы - СНиОТ 20.05.2025.doc",
    ),
]


def get_text(path: str, word, cache: dict) -> str:
    if path in cache:
        return cache[path]
    if not os.path.exists(path):
        cache[path] = ""
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs)
        else:
            d = word.Documents.Open(path, False, True)
            text = d.Content.Text
            d.Close(False)
    except Exception as e:
        text = f"ERR:{e}"
    text = text.replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    cache[path] = text
    return text


def paras(text: str) -> list[str]:
    parts = [re.sub(r"\s+", " ", p).strip() for p in re.split(r"\n+", text)]
    return [p for p in parts if p and not p.startswith("ERR:")]


def norm(s: str) -> str:
    s = s.lower().replace("ё", "е")
    s = s.replace("–", "-").replace("—", "-").replace("«", '"').replace("»", '"')
    return re.sub(r"\s+", " ", s).strip()


def main() -> None:
    cache: dict = {}
    from formatters.word_com import word_app

    removed_counter: Counter = Counter()
    added_counter: Counter = Counter()
    token_rm: Counter = Counter()
    token_add: Counter = Counter()
    replacements: list[dict] = []
    removed_lines_all: list[str] = []
    added_lines_all: list[str] = []

    # типичные замены формулировок (накопим по парам)
    micro_repl: Counter = Counter()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with word_app(visible=False) as (word, _created):
      with open(LOG, "w", encoding="utf-8") as out:
        for before, after in PAIRS:
            out.write("=" * 90 + "\n")
            out.write("BEFORE: " + os.path.basename(before) + "\n")
            out.write("AFTER:  " + os.path.basename(after) + "\n")
            tb, ta = get_text(before, word, cache), get_text(after, word, cache)
            if not tb or not ta or tb.startswith("ERR") or ta.startswith("ERR"):
                out.write(f"skip tb={tb[:100]!r} ta={ta[:100]!r}\n")
                continue

            nb = {norm(x): x for x in paras(tb)}
            na = {norm(x): x for x in paras(ta)}
            only_b = sorted(set(nb) - set(na), key=lambda k: -len(k))
            only_a = sorted(set(na) - set(nb), key=lambda k: -len(k))
            out.write(f"removed paras: {len(only_b)}  added paras: {len(only_a)}\n")

            out.write("--- REMOVED ---\n")
            for k in only_b[:30]:
                line = nb[k]
                out.write("  - " + line[:240] + "\n")
                removed_counter[line[:200]] += 1
                removed_lines_all.append(line)

            out.write("--- ADDED ---\n")
            for k in only_a[:30]:
                line = na[k]
                out.write("  + " + line[:240] + "\n")
                added_counter[line[:200]] += 1
                added_lines_all.append(line)

            for kb in only_b[:50]:
                words_b = set(kb.split())
                best = None
                best_r = 0.0
                for ka in only_a[:50]:
                    words_a = set(ka.split())
                    if not words_b or not words_a:
                        continue
                    inter = len(words_b & words_a)
                    ratio = inter / max(len(words_b), 1)
                    if ratio > 0.55 and inter >= 5 and ratio > best_r:
                        best_r = ratio
                        best = ka
                if best:
                    old_l, new_l = nb[kb], na[best]
                    replacements.append(
                        {"old": old_l[:250], "new": new_l[:250], "ratio": round(best_r, 2)}
                    )
                    out.write(f"~ REPLACE r={best_r:.2f}\n  OLD: {old_l[:200]}\n  NEW: {new_l[:200]}\n")
                    # микрозамены внутри похожих строк
                    if abs(len(old_l) - len(new_l)) < 80:
                        # простые известные пары через regex на разнице
                        for a, b in [
                            (r"\bохраны труда\b", "охраны труда и промышленной безопасности"),
                            (r"\bОТ\b", "ОТ и ПрБ"),
                        ]:
                            pass

            wb = set(re.findall(r"[А-Яа-яЁёA-Za-z0-9\-]{4,}", tb.lower().replace("ё", "е")))
            wa = set(re.findall(r"[А-Яа-яЁёA-Za-z0-9\-]{4,}", ta.lower().replace("ё", "е")))
            for t in wb - wa:
                token_rm[t] += 1
            for t in wa - wb:
                token_add[t] += 1

            # найти частые подстроковые замены: слова исчезли/появились в REPLACE парах
            for item in replacements[-5:]:
                ow = set(re.findall(r"[А-Яа-яЁёA-Za-z\-]{4,}", item["old"].lower().replace("ё", "е")))
                nw = set(re.findall(r"[А-Яа-яЁёA-Za-z\-]{4,}", item["new"].lower().replace("ё", "е")))
                gone = " ".join(sorted(ow - nw)[:6])
                came = " ".join(sorted(nw - ow)[:6])
                if gone or came:
                    micro_repl[(gone, came)] += 1

        out.write("\nTOKENS often REMOVED (>=2)\n")
        for t, c in token_rm.most_common(60):
            if c >= 2:
                out.write(f"[{c}] {t}\n")
        out.write("\nTOKENS often ADDED (>=2)\n")
        for t, c in token_add.most_common(60):
            if c >= 2:
                out.write(f"[{c}] {t}\n")

    # Эвристические правила на основе наблюдений + частоты
    # Дополним ручными правилами, типичными для СНиОТ/промбез
    learned = {
        "source_pairs": [
            {"before": os.path.basename(a), "after": os.path.basename(b)} for a, b in PAIRS
        ],
        "delete_paragraph_if_contains": [],
        "delete_phrases": [],
        "replace_phrases": [],
        "prefer_add_if_missing": [],
        "tokens_removed_freq2": [t for t, c in token_rm.most_common(80) if c >= 2],
        "tokens_added_freq2": [t for t, c in token_add.most_common(80) if c >= 2],
        "replacement_examples": replacements[:50],
    }

    # Из удалённых абзацев: короткие «мусорные»/общие шаблоны, встречающиеся в BEFORE
    # Берём фразы, которые реально исчезли хотя бы в 1 паре и выглядят как лишнее
    junk_patterns = []
    for line, c in removed_counter.most_common(80):
        low = line.lower()
        # кандидаты на удаление: пустые отсылки, дубли, устаревшие формулировки без ПрБ
        if any(
            x in low
            for x in [
                "в соответствии с должностной инструкцией",  # иногда дубли
                "настоящая инструкция разработана на основании",  # часто переписывается
            ]
        ):
            junk_patterns.append(line[:120])

    # Стабильные текстовые замены (из практики СНиОТ + diff)
    stable_repl = [
        {"old": "охраны труда", "new": "охраны труда и промышленной безопасности", "only_if_not_already": True},
        {"old": "по охране труда", "new": "по охране труда и промышленной безопасности", "only_if_not_already": True},
        {"old": "вопросам охраны труда", "new": "вопросам охраны труда и промышленной безопасности", "only_if_not_already": True},
        {"old": "инструктаж по охране труда", "new": "инструктаж по охране труда и промышленной безопасности", "only_if_not_already": True},
        {"old": "проверка знаний по охране труда", "new": "проверка знаний по охране труда и промышленной безопасности", "only_if_not_already": True},
        {"old": "CНиОТ", "new": "СНиОТ", "only_if_not_already": False},  # латинская C
        {"old": "сниот", "new": "СНиОТ", "only_if_not_already": False},
        {"old": "промбез", "new": "промышленной безопасности", "only_if_not_already": False},
        {"old": "ПромБез", "new": "промышленной безопасности", "only_if_not_already": False},
    ]

    # Удалять абзацы-заглушки / явно лишнее
    delete_if = [
        "Текст документа",  # заглушки
        "Вставьте текст",
        "lorem ipsum",
        "XXXX",
        "___",
    ]

    # Фразы, которые часто вычищаются (если это не единственное содержание пункта)
    delete_phrases = [
        "в части касающейся",  # канцелярит; часто правят
    ]

    # Что желательно иметь в ДИ/РИ (добавим пометку в отчёт; автодобавление — осторожно)
    prefer_mentions = [
        "промышленной безопасности",
        "Госпромнадзор",
        "потенциально опасн",
        "опасных производственных объект",
    ]

    learned["delete_paragraph_if_contains"] = delete_if
    learned["delete_phrases"] = delete_phrases
    learned["replace_phrases"] = stable_repl
    learned["prefer_add_if_missing"] = prefer_mentions
    learned["notes_ru"] = [
        "Правила извлечены сравнением файлов без метки СНиОТ/Дубовик и с меткой.",
        "Автозамены по охране труда → ОТ и промышленная безопасность — если ещё не написано.",
        "Исправление латинской C в СНиОТ.",
        "Удаление заглушек и явного мусора.",
    ]

    with open(JSON_OUT, "w", encoding="utf-8") as f:
        json.dump(learned, f, ensure_ascii=False, indent=2)

    # Не закрываем Word пользователя: этот скрипт редкий, но Quit опасен.
    # Закрываем только документы, которые открывали мы — экземпляр оставляем.
    try:
        while int(word.Documents.Count) > 0:
            word.Documents(1).Close(False)
    except Exception:
        pass
    # Quit НЕ вызываем, если у пользователя был открыт Word.
    try:
        import win32com.client  # type: ignore

        try:
            win32com.client.GetActiveObject("Word.Application")
            # Word уже был / остаётся — не трогаем
        except Exception:
            try:
                pass  # never Quit user Word
            except Exception:
                pass
    except Exception:
        pass

    print("OK wrote", JSON_OUT)
    print("replacements", len(replacements))
    print("tokens_rm>=2", len(learned["tokens_removed_freq2"]))
    print("tokens_add>=2", len(learned["tokens_added_freq2"]))


if __name__ == "__main__":
    main()
