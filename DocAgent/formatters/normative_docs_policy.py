# -*- coding: utf-8 -*-
"""
Правило СНиОТ / Дубовик В.В. — какие нормативные документы допустимы.

Человеческая логика:
• Специалисты и руководители — допускаются Законы, Кодексы, Директивы,
  Указы, Правила (НПА РБ и иные).
• Рабочие профессии — только локальные документы предприятия:
  инструкции, технологические карты, стандарты, положения,
  приказы предприятия; как исключение — Правила в части,
  касающейся их деятельности.
• «законодательство» / «основы трудового законодательства» в общем виде
  для рабочих не считаем отдельным Законом и не вычищаем как НПА-название,
  если это не «Закон Республики Беларусь №…».
"""

from __future__ import annotations

import re
from pathlib import Path

# Категории
CATEGORY_WORKER = "worker"
CATEGORY_SPECIALIST = "specialist"

WORKER_NAME_HINTS = (
    "слесар",
    "водител",
    "электрик",
    "электромонтер",
    "электромонтаж",
    "монтажник",
    "сварщик",
    "газорезчик",
    "оператор",
    "аппаратчик",
    "кочегар",
    "машинист",
    "стропальщик",
    "плотник",
    "маляр",
    "штукатур",
    "каменщик",
    "уборщик",
    "дворник",
    "грузчик",
    "кладовщик",
    "сторож",
    "вахтёр",
    "вахтер",
    "санитар",
    "рабоч",
    "разряда",
    "разряды",
)

SPECIALIST_NAME_HINTS = (
    "инженер",
    "начальник",
    "заместитель",
    "руководитель",
    "специалист",
    "диспетчер",
    "экономист",
    "юрист",
    "бухгалтер",
    "мастер ",  # мастер участка / смены — ИТР
    "ведущий",
    "главный",
    "технолог",
    "энергетик",
    "механик",  # часто ИТР; слесарь-механик поймается по «слесар»
)

# НПА: Закон, Кодекс, Декрет, Указ, Директива (не «законодательство»)
NPA_TITLE_RE = re.compile(
    r"(?i)\b("
    r"закон(?!одател)\w*"
    r"|кодекс\w*"
    r"|декрет\w*"
    r"|указ\w*"
    r"|директив\w*"
    r")\b",
)

# «Правила …» (отдельный класс — для рабочих с исключением)
RULES_RE = re.compile(r"(?i)\bправил[аеыу]?\b")

# Локальные документы предприятия — оставляем рабочим
LOCAL_DOC_RE = re.compile(
    r"(?i)\b("
    r"инструкци\w*"
    r"|технологическ\w*\s+карт\w*"
    r"|стандарт\w*"
    r"|положени\w*"
    r"|приказ\w*"
    r"|распоряжени\w*"
    r"|регламент\w*"
    r"|стп\b"
    r"|сту\b"
    r"|ткп\b"
    r"|гост\b"
    r"|паспорт\w*"
    r"|руководств\w*\s+по\s+эксплуатаци\w*"
    r"|коллективн\w*\s+договор\w*"
    r"|устав\w*"
    r")\b",
)

# Исключение: Правила, касающиеся деятельности рабочего
RULES_ACTIVITY_KEEP_RE = re.compile(
    r"(?i)("
    r"внутреннего\s+трудового\s+распорядка"
    r"|охраны\s+труда"
    r"|пожарн\w*\s+безопасн"
    r"|промышленн\w*\s+безопасн"
    r"|дорогн\w*\s+движен"
    r"|техническ\w*\s+эксплуатац"
    r"|безопасн\w*\s+эксплуатац"
    r"|электробезопасн"
    r"|работ\w*\s+на\s+высоте"
    r"|строповк"
    r"|грузоподъемн|грузоподъёмн"
    r"|сосудов?\s+под\s+давлени"
    r"|газоопасн"
    r"|земляных\s+работ"
    r"|сварочн"
    r")",
)

# Зоны текста, где особенно смотрим списки документов
DOC_LIST_ZONE_START = re.compile(
    r"(?i)(должен\s+знать|руководствуется|в\s+своей\s+деятельности|"
    r"обязан\s+знать|обязанности\s+знать|нормативн\w*\s+правов)",
)


def detect_staff_category(
    *,
    source_path: str = "",
    doc_type: str = "",
    text_blob: str = "",
) -> str:
    """
    worker — рабочая профессия (РИ слесарь/водитель/…).
    specialist — специалисты и руководители (ДИ инженер/диспетчер/начальник/…).
    """
    name = Path(source_path).name.lower() if source_path else ""
    blob = f"{name}\n{text_blob}".lower()
    dtype = (doc_type or "").lower()

    score_w = sum(1 for h in WORKER_NAME_HINTS if h in blob)
    score_s = sum(1 for h in SPECIALIST_NAME_HINTS if h in blob)

    # явные маркеры категории в тексте ДИ/РИ
    if re.search(r"категории\s+специалистов", blob):
        score_s += 3
    if re.search(r"категории\s+руководителей", blob):
        score_s += 3
    if re.search(r"рабоч(ая|ей)\s+професси|разряд\w*\s+работ", blob):
        score_w += 2
    if "рабочая инструкция" in blob or dtype == "rabochaya_instrukciya":
        score_w += 2
    if "должностная инструкция" in blob or dtype == "dolzhnostnaya_instrukciya":
        score_s += 2

    # «слесарь» важнее общего «механик»
    if "слесар" in blob:
        score_w += 2

    if score_w > score_s:
        return CATEGORY_WORKER
    if score_s > score_w:
        return CATEGORY_SPECIALIST

    # ничья: тип документа
    if dtype == "rabochaya_instrukciya":
        return CATEGORY_WORKER
    if dtype == "dolzhnostnaya_instrukciya":
        return CATEGORY_SPECIALIST
    return CATEGORY_SPECIALIST  # по умолчанию не вычищаем НПА


def extract_profession_tokens(source_path: str = "", text_blob: str = "") -> set[str]:
    """Токены профессии из имени файла / титула — для исключения по Правилам."""
    raw = f"{Path(source_path).stem if source_path else ''} {text_blob[:2000]}"
    low = raw.lower()
    tokens: set[str] = set()
    for h in WORKER_NAME_HINTS + SPECIALIST_NAME_HINTS:
        if h.strip() in low:
            tokens.add(h.strip())
    # отдельные слова из имени файла (пропускаем служебные test/in/out)
    skip = {"test", "out", "converted", "docx", "оформлен", "temp", "copy", "agent"}
    for part in re.split(r"[\s_\-.,()]+", Path(source_path).stem.lower() if source_path else ""):
        if len(part) >= 4 and part not in skip and not part.isdigit():
            tokens.add(part)
    # отраслевые корни из текста (котельная, теплосеть…) — даже если нет в hints
    for stem in (
        "котельн",
        "теплосет",
        "котл",
        "слесар",
        "свар",
        "электромонт",
        "насос",
    ):
        if stem in low:
            tokens.add(stem)
    return tokens


def rules_ok_for_worker(text: str, profession_tokens: set[str] | None = None) -> bool:
    """
    Исключение: Правила в части деятельности рабочего.
    Человеческая логика — оставляем типовые ОТ/ПБ/ПДД/эксплуатацию
    и Правила, где в тексте есть слова профессии.
    """
    t = text or ""
    if RULES_ACTIVITY_KEEP_RE.search(t):
        return True
    low = t.lower()
    for tok in profession_tokens or ():
        if len(tok) >= 4 and tok in low:
            return True
    # короткие общие «правила и нормы охраны труда…»
    if re.search(r"(?i)правила\s+и\s+нормы\s+охраны\s+труда", t):
        return True
    # режим работы оборудования / показания приборов — по деятельности
    if re.search(
        r"(?i)(веден\w*\s+режим|показан\w*\s+прибор|режимн\w*\s+карт)",
        t,
    ):
        return True
    return False


# Обрывки директив без слова «Директива»: «от 27 декабря 2006 г. № 2 «О дебюрократизации…»
ORPHAN_DIRECTIVE_RE = re.compile(
    r"(?is)^\s*от\s+"
    r"(?:"
    r"\d{1,2}\s+[а-яё]+\s+\d{4}\s*г\.?"  # 27 декабря 2006 г.
    r"|\d{1,2}\.\d{2}\.\d{4}"  # 14.06.2007
    r")"
    r"\s*№\s*\d+"
)


def is_orphan_presidential_directive_line(text: str) -> bool:
    """
    Абзац вида «от 27 декабря 2006 г. № 2 «О дебюрократизации…»» —
    хвост списка Директив Президента без заголовка «Директива…».
    В рабочих инструкциях такие абзацы УДАЛЯТЬ.
    """
    t = (text or "").strip()
    if not t or len(t) > 500:
        return False
    if not ORPHAN_DIRECTIVE_RE.match(t):
        return False
    # типично есть кавычки с названием «О …»
    if "«" in t or '"' in t or "„" in t:
        return True
    # без кавычек, но короткий переченьный хвост с №
    return bool(re.search(r"№\s*\d+", t)) and len(t) < 350


def is_directives_header_line(text: str) -> bool:
    """Строка-заголовок списка: «Директивы Президента Республики Беларусь:»."""
    t = re.sub(r"\s+", " ", (text or "").strip().lower())
    return bool(
        re.match(
            r"^директив\w*\s+президента(\s+республики\s+беларусь)?\s*:?\s*$",
            t,
        )
    )


def is_npa_title_line(text: str) -> bool:
    """Строка — название Закона / Кодекса / Декрета / Указа / Директивы."""
    t = text.strip()
    if not t or len(t) > 500:
        return False
    if is_orphan_presidential_directive_line(t) or is_directives_header_line(t):
        return True
    if NPA_TITLE_RE.search(t):
        # отсечь «основы трудового законодательства» без «Закон Республики…»
        low = t.lower()
        if "законодательств" in low and not re.search(
            r"(?i)закон\s+республики|закон\s+рб|№\s*\d+", t
        ):
            return False
        return True
    return False


def is_rules_line(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    return bool(RULES_RE.search(t)) and not LOCAL_DOC_RE.search(t)


def is_local_enterprise_line(text: str) -> bool:
    """Локальный документ предприятия — для рабочих оставляем."""
    return bool(LOCAL_DOC_RE.search(text or ""))


def is_external_approved_rules(text: str) -> bool:
    """
    Внешние Правила с реквизитами утверждения (постановление/приказ МЧС и т.п.).
    Для рабочих в РИ такие строки не оставляем (ни в «должен знать», ни в «примерах работ»).
    """
    t = text or ""
    if not RULES_RE.search(t):
        return False
    if not re.search(r"(?i)утвержден", t):
        return False
    return bool(
        re.search(
            r"(?i)(постановлен\w*|приказ\w*\s+мчс|министерств\w*|совмина|совета\s+министров)",
            t,
        )
    )


def should_remove_normative_line(
    text: str,
    category: str,
    *,
    profession_tokens: set[str] | None = None,
) -> bool:
    """
    Нужно ли убрать строку из перечня документов (должен знать / руководствуется).
    Для специалистов/руководителей — НПА не трогаем.
    """
    t = (text or "").strip()
    if not t:
        return False

    if category != CATEGORY_WORKER:
        return False

    # полные внешние Правила с утверждением — всегда убрать у рабочих
    if is_external_approved_rules(t):
        return True

    # локальные — всегда оставляем
    if is_local_enterprise_line(t) and not is_npa_title_line(t):
        # «приказ Министерства» — не локальный; «приказ директора/предприятия» — локальный
        low = t.lower()
        if re.search(r"приказ\w*\s+(министерств|президента|совмина|совета\s+министров)", low):
            return True
        return False

    if is_npa_title_line(t):
        return True

    if is_rules_line(t) or (RULES_RE.search(t) and not is_local_enterprise_line(t)):
        # Правила: оставить только в части деятельности
        return not rules_ok_for_worker(t, profession_tokens)

    return False


def is_npa_delete_phrase(phrase: str) -> bool:
    """Фраза из learned delete_phrases относится к НПА (не применять к специалистам)."""
    return is_npa_title_line(phrase) or bool(
        re.search(r"(?i)\b(закон|кодекс|декрет|указ|директив)", phrase or "")
    )


def category_label_ru(category: str) -> str:
    if category == CATEGORY_WORKER:
        return "рабочая профессия (только локальные документы + Правила по деятельности)"
    return "специалист/руководитель (НПА допускаются)"
