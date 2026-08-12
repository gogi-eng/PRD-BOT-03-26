# -*- coding: utf-8 -*-
from pathlib import Path

p = Path(__file__).resolve().parent / "formatters" / "structure_norm.py"
text = p.read_text(encoding="utf-8")

old_label = '''def _doc_kind_label(doc_type: str, blob: str = "") -> str:
    low = (blob or "").lower()
    if doc_type == "rabochaya_instrukciya" or "рабоч" in low:
        return "РАБОЧАЯ ИНСТРУКЦИЯ"
    if doc_type == "dolzhnostnaya_instrukciya" or "должностн" in low:
        return "ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ"
    if "рабоч" in low:
        return "РАБОЧАЯ ИНСТРУКЦИЯ"
    if "должностн" in low:
        return "ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ"
    return "ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ"


def _collect_mashed_title_paras(doc: Document) -> list:'''

new_label = '''def _doc_kind_label(doc_type: str, blob: str = "") -> str:
    """
    Подпись вида документа на титуле.
    Не подставлять «ДОЛЖНОСТНАЯ» по умолчанию — для КЛ/положения это ошибка.
    """
    low = (blob or "").lower()
    hint = (doc_type or "").lower().strip()
    if hint == "rabochaya_instrukciya" or "рабочая инструкц" in low or (
        "рабоч" in low and "инструкц" in low and "должностн" not in low
    ):
        return "РАБОЧАЯ ИНСТРУКЦИЯ"
    if hint == "dolzhnostnaya_instrukciya" or "должностн" in low:
        return "ДОЛЖНОСТНАЯ ИНСТРУКЦИЯ"
    if hint == "instrukciya_ot" or "охране труда" in low:
        return "ИНСТРУКЦИЯ"
    if hint == "polozhenie" or "положен" in low:
        if "инструкц" in low or "эксплуатац" in low:
            return "ИНСТРУКЦИЯ"
        return "ПОЛОЖЕНИЕ"
    if "эксплуатац" in low and "инструкц" in low:
        return "ИНСТРУКЦИЯ"
    if "инструкц" in low and "должностн" not in low and "рабоч" not in low:
        return "ИНСТРУКЦИЯ"
    return "ИНСТРУКЦИЯ"


def _is_kl_tab_title_layout(doc: Document) -> bool:
    """
    Титул эталона КЛ 31.07.2026: «ИНСТРУКЦИЯ … УТВЕРЖДАЮ» табами в абзацах,
    без таблицы УТВЕРЖДАЮ. Такой титул НЕ пересобирать в таблицу ДИ/РИ.
    """
    if _find_title_table(doc) is not None:
        return False
    for p in doc.paragraphs[:20]:
        t = (p.text or "").replace("\\xa0", " ")
        low = t.lower()
        if "утверждаю" in low and ("\\t" in t or "инструкция" in low):
            return True
        if "\\t" in t and "заместитель" in low and "директор" in low:
            return True
    return False


def _collect_mashed_title_paras(doc: Document) -> list:'''

# fix escapes - use real chars
new_label = new_label.replace("\\xa0", "\xa0").replace("\\t", "\t")

if "_is_kl_tab_title_layout" in text:
    print("kl helper already present")
elif old_label not in text:
    # maybe already partially patched
    if 'return "ИНСТРУКЦИЯ"' in text and "def _doc_kind_label" in text:
        print("label already patched differently")
    else:
        raise SystemExit("old _doc_kind_label block not found")
else:
    text = text.replace(old_label, new_label, 1)
    print("patched _doc_kind_label")

old_ensure = '''def ensure_title_table_like_sample(doc: Document, doc_type: str = "unsupported") -> int:
    """
    Титул как в эталоне СЛЕСАРЬ 30.07.2026:
    — если таблица уже есть: только подправить, склеенные абзацы УДАЛИТЬ (не оставлять пустыми);
    — если таблицы нет, а титул склеен пробелами/табами: собрать таблицу 3×2 и удалить старые строки.
    """
    changed = 0
    table = _find_title_table(doc)
    mashed = _collect_mashed_title_paras(doc)
    mashed_with_text = [p for p in mashed if p.text.strip()]
'''

new_ensure = '''def ensure_title_table_like_sample(doc: Document, doc_type: str = "unsupported") -> int:
    """
    Титул:
    — РИ/ДИ: таблица как в эталоне СЛЕСАРЬ / ДИ;
    — КЛ / положение / инструкция по эксплуатации: НЕ ломать титул с табами
      (эталон 31.07.2026--- ПОЛОЖЕНИЕ) — повторять расположение надписей как есть.
    """
    changed = 0
    dtype = (doc_type or "").lower().strip()
    # титул эталона КЛ (табы) — не пересобирать в таблицу ДИ
    if _is_kl_tab_title_layout(doc):
        return 0
    # положение / инструкция по эксплуатации без таблицы УТВЕРЖДАЮ —
    # не создавать таблицу ДИ «с нуля»
    if dtype == "polozhenie" and _find_title_table(doc) is None:
        head = " ".join((p.text or "") for p in doc.paragraphs[:25]).lower()
        if "утверждаю" in head or "инструкция" in head:
            return 0

    table = _find_title_table(doc)
    mashed = _collect_mashed_title_paras(doc)
    mashed_with_text = [p for p in mashed if p.text.strip()]
'''

if "НЕ ломать титул с табами" in text:
    print("ensure_title already patched")
elif old_ensure not in text:
    raise SystemExit("old ensure_title block not found")
else:
    text = text.replace(old_ensure, new_ensure, 1)
    print("patched ensure_title_table_like_sample")

p.write_text(text, encoding="utf-8")
print("OK saved", p)
