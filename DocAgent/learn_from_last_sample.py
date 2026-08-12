# -*- coding: utf-8 -*-
"""
Разовое обучение: взять ваш готовый образец и записать ВСЕ отличия в правила.
Сравнивает «до» и «после», сразу продвигает правки в learned_edit_patterns.json.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(r"C:\Users\v.dubovik\DocAgent")
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from learner import (  # noqa: E402
    PATTERNS_PATH,
    diff_paragraphs,
    extract_micro_replacements,
    extract_text,
    load_stats,
    save_stats,
    _bump,
    append_observation,
    _log,
)

BEFORE = (
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ"
    r"\ТУ (СМАТ)\Рабочая инструкция СЛЕСАРЮ 5-го разряда СМАТ (изменено 20.07.2026).docx"
)
AFTER = (
    r"N:\9 - Служба надёжности и охраны труда (СНиОТ)\!!!ОБМЕН\РАССМОТРЕНИЕ  ДИ, РИ, ПОЛОЖЕНИЙ"
    r"\ТУ (СМАТ)\Рабочая инструкция СЛЕСАРЮ 5-го разряда СМАТ 30.07.2026_оформлен.docx"
)

REPORT = ROOT / "logs" / "learn_from_sample_30_07.txt"
MIN_L = 3
MAX_L = 160


def load_patterns() -> dict:
    if PATTERNS_PATH.exists():
        return json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    return {"replace_phrases": [], "delete_phrases": [], "notes_ru": []}


def save_patterns(patterns: dict) -> None:
    PATTERNS_PATH.write_text(
        json.dumps(patterns, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_noisy(s: str) -> bool:
    s = s.strip()
    if len(s) < MIN_L or len(s) > MAX_L:
        return True
    # только цифры / пункты нумерации без смысла
    if re.fullmatch(r"[\d\.\-\s]+", s):
        return True
    return False


def main() -> int:
    if not os.path.exists(BEFORE):
        print("NO_BEFORE", BEFORE)
        return 1
    if not os.path.exists(AFTER):
        print("NO_AFTER", AFTER)
        return 1

    old_text = extract_text(BEFORE)
    new_text = extract_text(AFTER)
    print("chars_before", len(old_text), "chars_after", len(new_text))

    replacements, deleted = diff_paragraphs(old_text, new_text)
    print("para_repl", len(replacements), "para_del", len(deleted))

    stats = load_stats()
    patterns = load_patterns()
    existing_repl = {
        (x.get("old"), x.get("new"))
        for x in patterns.get("replace_phrases", [])
        if isinstance(x, dict)
    }
    existing_del = set(patterns.get("delete_phrases", []) or [])
    existing_del_para = set(
        x.lower() for x in (patterns.get("delete_paragraph_if_contains", []) or [])
    )

    added_repl: list[dict] = []
    added_del: list[str] = []
    added_del_para: list[str] = []
    lines: list[str] = []
    lines.append(f"Обучение по образцу {datetime.now():%Y-%m-%d %H:%M:%S}")
    lines.append(f"ДО:  {BEFORE}")
    lines.append(f"ПОСЛЕ (ваш образец): {AFTER}")
    lines.append("")

    # микро-замены + целые короткие абзацы
    cand_repl: dict[tuple[str, str], int] = {}
    for old_l, new_l in replacements:
        for o, n in extract_micro_replacements(old_l, new_l):
            if is_noisy(o) or is_noisy(n):
                continue
            cand_repl[(o, n)] = cand_repl.get((o, n), 0) + 1
        if (
            not is_noisy(old_l)
            and not is_noisy(new_l)
            and old_l != new_l
            and abs(len(old_l) - len(new_l)) < 80
        ):
            # целиком, если строки сопоставимы
            if len(old_l) <= MAX_L and len(new_l) <= MAX_L:
                cand_repl[(old_l, new_l)] = cand_repl.get((old_l, new_l), 0) + 1

    for (o, n), cnt in sorted(cand_repl.items(), key=lambda x: -x[1]):
        key = f"{o}|||{n}"
        _bump(stats.setdefault("replacements", {}), key, n=max(cnt, 2))
        if (o, n) in existing_repl:
            continue
        # не писать слишком длинные «уникальные» абзацы как replace (шум)
        if len(o) > 100 or len(n) > 100:
            # только если похожи (общая часть большая)
            if len(set(o.split()) & set(n.split())) < 3:
                continue
        item = {
            "old": o,
            "new": n,
            "from_sample": "СЛЕСАРЮ 5 СМАТ 30.07.2026_оформлен",
        }
        patterns.setdefault("replace_phrases", []).append(item)
        existing_repl.add((o, n))
        added_repl.append(item)
        lines.append(f"ЗАМЕНА: «{o}»  →  «{n}»")

    for d in deleted:
        d = d.strip()
        if is_noisy(d):
            continue
        _bump(stats.setdefault("deletions", {}), d, n=2)
        # короткие фразы — в delete_phrases
        if MIN_L <= len(d) <= 90:
            if d not in existing_del:
                patterns.setdefault("delete_phrases", []).append(d)
                existing_del.add(d)
                added_del.append(d)
                lines.append(f"УДАЛИТЬ ФРАЗУ: «{d}»")
        # типичные «должен знать» / законы / заглушки — в delete_paragraph_if_contains
        low = d.lower()
        markers = (
            "кодекс",
            "закон республики",
            "декрет",
            "указ президента",
            "настоящая инструкция",
            "номер инструкции",
        )
        if any(m in low for m in markers) and 8 <= len(d) <= 120:
            # ключ — устойчивый кусок
            key = d[:80]
            if key.lower() not in existing_del_para:
                patterns.setdefault("delete_paragraph_if_contains", []).append(key)
                existing_del_para.add(key.lower())
                added_del_para.append(key)
                lines.append(f"УДАЛИТЬ АБЗАЦ ЕСЛИ СОДЕРЖИТ: «{key}»")

    # метаданные образца
    patterns["last_sample_learn"] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "before": BEFORE,
        "after": AFTER,
        "added_replacements": len(added_repl),
        "added_delete_phrases": len(added_del),
        "added_delete_paragraph_contains": len(added_del_para),
        "doc_type": "rabochaya_instrukciya",
        "note_ru": (
            "Образец пользователя: Рабочая инструкция СЛЕСАРЮ 5-го разряда СМАТ "
            "30.07.2026_оформлен.docx — все отличия от черновика записаны в правила."
        ),
    }
    notes = patterns.setdefault("notes_ru", [])
    note = (
        f"Образец СМАТ слесарь 5р 30.07.2026: +{len(added_repl)} замен, "
        f"+{len(added_del)} удалений фраз, +{len(added_del_para)} удалений абзацев"
    )
    if note not in notes:
        notes.append(note)
    patterns["preferred_samples"] = patterns.get("preferred_samples") or {}
    patterns["preferred_samples"]["rabochaya_instrukciya_smat_slesar_5"] = AFTER

    save_patterns(patterns)
    save_stats(stats)
    append_observation(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "force_learn_from_sample",
            "before": BEFORE,
            "after": AFTER,
            "added_replacements": len(added_repl),
            "added_delete_phrases": len(added_del),
            "added_delete_paragraph_contains": len(added_del_para),
        }
    )
    _log(
        f"force learn sample: +repl={len(added_repl)} +del={len(added_del)} "
        f"+del_para={len(added_del_para)}"
    )

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print("ADDED_REPL", len(added_repl))
    print("ADDED_DEL", len(added_del))
    print("ADDED_DEL_PARA", len(added_del_para))
    print("REPORT", REPORT)
    print("PATTERNS", PATTERNS_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
