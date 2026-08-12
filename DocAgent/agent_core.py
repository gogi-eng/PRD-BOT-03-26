# -*- coding: utf-8 -*-
"""Ядро агента: определение вида документа, примеры, запуск форматтеров."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from docx import Document

from rules import DOCUMENT_TYPES, SAMPLE_POLICY, detect_type_from_text, is_instruction_aligned_sample
from path_resolver import (
    READONLY_SAMPLE_DIRS,
    USER_AGENT_DIR,
    assert_path_writable,
    is_path_in_user_agent_dir,
    is_path_readonly_sample,
)
from formatters.common import apply_basic_office_format

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
LOG_DIR = ROOT / "logs"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def is_conservative_di_satp(input_path: str, doc_type: str) -> bool:
    """См. formatters.sniot_document.is_conservative_di_satp."""
    from formatters.sniot_document import is_conservative_di_satp as _conservative

    return _conservative(input_path, doc_type)


def log(msg: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LOG_DIR / "agent.log", "a", encoding="utf-8") as f:
        f.write(line)


def read_preview_text(path: str, limit: int = 2500) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in (".doc", ".rtf"):
        return _read_doc_via_word(path, limit)
    if ext != ".docx":
        return ""
    doc = Document(path)
    parts = []
    total = 0

    def _append(text: str) -> bool:
        nonlocal total
        t = text.strip()
        if not t:
            return total <= limit
        parts.append(t)
        total += len(t)
        return total <= limit

    for p in doc.paragraphs:
        if not _append(p.text):
            break
    if total <= limit:
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if not _append(cell.text):
                        break
                if total > limit:
                    break
            if total > limit:
                break
    return "\n".join(parts)


def _read_doc_via_word(path: str, limit: int = 2500) -> str:
    """Старый .doc / .rtf — через Word. Word пользователя не закрываем."""
    try:
        from formatters.word_com import open_docx_readonly
    except Exception:
        return ""
    try:
        with open_docx_readonly(path) as doc:
            return (doc.Content.Text or "")[:limit]
    except Exception:
        return ""


def ensure_pywin32() -> None:
    """Проверить пакет pywin32 (нужен для .doc и .rtf)."""
    try:
        import win32com.client  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "Для файлов .doc и .rtf нужен пакет pywin32.\n\n"
            "Что сделать:\n"
            "1) Запустите install_deps.bat в папке DocAgent\n"
            "   или в командной строке:\n"
            "   python -m pip install pywin32 python-docx\n"
            "2) Убедитесь, что на компьютере установлен Microsoft Word.\n"
            "3) Снова запустите агент (start_agent.bat)."
        ) from e


def convert_to_docx(path: str) -> str:
    """
    Конвертация .doc / .rtf → .docx рядом с исходником (через Word).
    .docx возвращает как есть.

    Важно: для конвертации всегда отдельный скрытый Word (DispatchEx),
    не трогаем и не используем уже открытый Word пользователя —
    иначе Documents.Open часто возвращает None.
    """
    import shutil
    import tempfile

    import win32com.client  # type: ignore

    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return path
    if ext not in (".doc", ".rtf"):
        raise RuntimeError(f"Неподдерживаемый формат файла: {path}")

    ensure_pywin32()
    abs_src = os.path.abspath(path)
    if not os.path.isfile(abs_src):
        raise RuntimeError(f"Файл не найден:\n{abs_src}")

    out = os.path.splitext(abs_src)[0] + "_converted.docx"
    # рабочая копия на локальном диске — надёжнее, чем открывать .doc сразу с N:\
    td = tempfile.mkdtemp(prefix="docagent_conv_")
    local_src = os.path.join(td, "source" + ext)
    local_out = os.path.join(td, "converted.docx")
    shutil.copy2(abs_src, local_src)

    word = None
    doc = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        try:
            word.DisplayAlerts = 0
        except Exception:
            pass
        # ConfirmConversions=False, ReadOnly=True, AddToRecentFiles=False
        doc = word.Documents.Open(
            local_src,
            False,
            True,
            False,
        )
        if doc is None:
            raise RuntimeError(
                "Word не смог открыть файл (Documents.Open вернул пусто).\n"
                f"Файл: {abs_src}"
            )
        # 16 = wdFormatXMLDocument (.docx)
        doc.SaveAs(local_out, FileFormat=16)
        doc.Close(False)
        doc = None
        if not os.path.isfile(local_out):
            raise RuntimeError("Word не создал временный .docx")
        shutil.copy2(local_out, out)
    except Exception as e:
        raise RuntimeError(
            f"Не удалось конвертировать {ext} → .docx через Word.\n"
            f"Исходник: {abs_src}\n"
            f"Ошибка: {e}"
        ) from e
    finally:
        if doc is not None:
            try:
                doc.Close(False)
            except Exception:
                pass
        if word is not None:
            try:
                word.Quit()
            except Exception:
                pass
        shutil.rmtree(td, ignore_errors=True)

    if not os.path.isfile(out):
        raise RuntimeError(f"Не удалось сохранить .docx:\n{out}")
    return out


# совместимость со старым именем
def convert_doc_to_docx(path: str) -> str:
    return convert_to_docx(path)


def detect_document(path: str) -> tuple[str, str]:
    text = read_preview_text(path)
    dtype = detect_type_from_text(os.path.basename(path), text)
    return dtype, text[:800]


def _tokenize_tokens(name: str) -> set[str]:
    """Значимые куски имени файла для подбора похожего образца."""
    low = (name or "").lower()
    low = re.sub(r"[_\-\.]+", " ", low)
    low = re.sub(r"\.(docx|doc|rtf)$", "", low)
    stop = {
        "docx",
        "doc",
        "rtf",
        "converted",
        "оформлен",
        "рабочая",
        "инструкция",
        "должностная",
        "положение",
        "приказ",
        "разряда",
        "разряд",
        "ого",
        "го",
        "по",
        "и",
        "для",
        "сниот",
        "изменено",
        "исправленный",
        "исправлено",
        "оформлен",
        "копия",
    }
    tokens = set()
    for t in re.findall(r"[a-zа-яё0-9]+", low, flags=re.IGNORECASE):
        t = t.lower()
        if len(t) < 3 or t in stop or t.isdigit():
            continue
        tokens.add(t)
    # числа разрядов полезны: 3, 4, 5
    for m in re.findall(r"\b([3-6])\b", low):
        tokens.add(f"разряд{m}")
    # короткие, но важные маркеры темы
    for short in ("цтп", "итп", "кл", "ри", "ди", "иот"):
        if re.search(rf"(?<![a-zа-яё0-9]){short}(?![a-zа-яё0-9])", low):
            tokens.add(short)
    return tokens


def _topic_tags(name: str) -> set[str]:
    """
    Тема документа по имени файла.
    Нельзя брать образец «силовые КЛ», если оформляем «ЦТП».
    """
    low = (name or "").lower().replace("ё", "е")
    tags: set[str] = set()
    if "цтп" in low or "итп" in low or "теплового пункта" in low or "тепловой пункт" in low:
        tags.add("ctp_itp")
    if (
        "кабельн" in low
        or "силовых кл" in low
        or "силовые кл" in low
        or "0,4-10" in low
        or "0.4-10" in low
        or re.search(r"(?<![a-zа-яё0-9])кл(?![a-zа-яё0-9])", low)
    ):
        tags.add("cable_kl")
    if "слесар" in low:
        tags.add("slesar")
    if "диспетчер" in low:
        tags.add("dispatcher")
    if "положен" in low and "эксплуатац" not in low:
        tags.add("org_polozhenie")
    if "охране труда" in low or re.search(r"(?<![a-zа-яё0-9])иот(?![a-zа-яё0-9])", low):
        tags.add("iot")
    if "эксплуатац" in low:
        tags.add("ekspluatacii")
    return tags


def _examples_topic_compatible(source_path: str, example_path: str) -> bool:
    """Эталон подходит по теме (ЦТП≠КЛ, РИ≠ДИ и т.п.)."""
    src = _topic_tags(os.path.basename(source_path))
    ex = _topic_tags(os.path.basename(example_path))
    if not src or not ex:
        return True
    # явный конфликт тем
    conflicts = [
        ("ctp_itp", "cable_kl"),
        ("slesar", "dispatcher"),
        ("iot", "ekspluatacii"),
        ("org_polozhenie", "ekspluatacii"),
    ]
    for a, b in conflicts:
        if (a in src and b in ex) or (b in src and a in ex):
            return False
    # если у обеих есть «узкие» теги — нужно пересечение
    narrow = {"ctp_itp", "cable_kl", "slesar", "dispatcher", "iot", "org_polozhenie"}
    src_n = src & narrow
    ex_n = ex & narrow
    if src_n and ex_n:
        return bool(src_n & ex_n)
    return True


def choose_best_example(doc_type: str, source_path: str, limit: int = 20) -> dict | None:
    """
    Сам выбирает лучший локальный образец:
    — сначала образцовые шаблоны (preferred_samples / master_samples),
      НО только если тема совпадает с исходником (ЦТП ≠ силовые КЛ);
    — РИ: ТОЛЬКО эталон СЛЕСАРЬ 30.07.2026 (другие файлы не брать);
    — иначе та же папка / похожее имя (должность/участок/ЦТП/разряд);
    — приоритет правок СНиОТ/Дубовик;
    — не берёт сам исходник и *_оформлен* (кроме эталонных preferred_samples).
    — имя файла не меняет: образец только для ориентира оформления.
    """
    # РИ: единственный эталон — жёстко, без поиска по папке
    if doc_type == "rabochaya_instrukciya":
        from formatters.structure_fix import RI_ETALON_PATH

        preferred_path = RI_ETALON_PATH
        try:
            from formatters.text_edits import load_patterns

            prefs = (load_patterns() or {}).get("preferred_samples") or {}
            preferred_path = (
                prefs.get("rabochaya_instrukciya")
                or prefs.get("rabochaya_instrukciya_smat_slesar_5")
                or RI_ETALON_PATH
            )
        except Exception:
            preferred_path = RI_ETALON_PATH
        try:
            from pathlib import Path
            import json as _json

            cfg_path = Path(__file__).resolve().parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            master = ((cfg.get("master_samples") or {}).get("rabochaya_instrukciya") or "").strip()
            if master:
                preferred_path = master
        except Exception:
            pass
        if preferred_path and os.path.isfile(preferred_path):
            if os.path.normcase(os.path.abspath(preferred_path)) != os.path.normcase(
                os.path.abspath(source_path)
            ):
                log(f"AUTO example PREFERRED (РИ эталон): {preferred_path}")
                return {
                    "path": preferred_path,
                    "name": os.path.basename(preferred_path),
                    "folder": os.path.basename(os.path.dirname(preferred_path)),
                    "mtime": os.path.getmtime(preferred_path),
                    "aligned": True,
                    "score": 999,
                    "label": f"[ЭТАЛОН РИ] {os.path.basename(preferred_path)}",
                    "common_tokens": [],
                }
        log("РИ: эталон СЛЕСАРЬ 30.07.2026 не найден на диске N:")
        return None

    # эталон, который пользователь явно «утвердил»
    preferred_path = None
    try:
        from formatters.text_edits import load_patterns

        prefs = (load_patterns() or {}).get("preferred_samples") or {}

        # приоритет: тип документа → master_* → общий
        if doc_type == "prikaz":
            preferred_path = (
                prefs.get("prikaz")
                or prefs.get("master_prikaz")
            )
        elif doc_type == "dolzhnostnaya_instrukciya":
            preferred_path = (
                prefs.get("dolzhnostnaya_instrukciya")
                or prefs.get("master_di")
                or prefs.get("master_instructions")
            )
        if not preferred_path:
            preferred_path = prefs.get(doc_type)

        # config.json master_samples — высший приоритет по типу
        try:
            from pathlib import Path
            import json as _json

            cfg_path = Path(__file__).resolve().parent / "config.json"
            cfg = _json.loads(cfg_path.read_text(encoding="utf-8"))
            masters = cfg.get("master_samples") or {}
            master = (masters.get(doc_type) or "").strip()
            if master and os.path.isfile(master):
                preferred_path = master
            elif doc_type == "dolzhnostnaya_instrukciya":
                # устаревшее поле master_sample_path — только если нет типизированного
                if not preferred_path:
                    legacy = (cfg.get("master_sample_path") or "").strip()
                    if legacy and os.path.isfile(legacy):
                        preferred_path = legacy
        except Exception:
            pass
    except Exception:
        preferred_path = None

    if preferred_path and os.path.isfile(preferred_path):
        # не выбирать тот же файл, что оформляем
        if os.path.normcase(os.path.abspath(preferred_path)) != os.path.normcase(
            os.path.abspath(source_path)
        ):
            if _examples_topic_compatible(source_path, preferred_path):
                log(f"AUTO example PREFERRED: {preferred_path}")
                return {
                    "path": preferred_path,
                    "name": os.path.basename(preferred_path),
                    "folder": os.path.basename(os.path.dirname(preferred_path)),
                    "mtime": os.path.getmtime(preferred_path),
                    "aligned": True,
                    "score": 999,
                    "label": f"[ЭТАЛОН] {os.path.basename(preferred_path)}",
                    "common_tokens": [],
                }
            log(
                "AUTO example PREFERRED skipped (тема не совпадает): "
                f"{os.path.basename(preferred_path)} ← для {os.path.basename(source_path)}"
            )

    items = find_examples(doc_type, limit=max(limit, 40), source_path=source_path)
    if not items:
        return None
    src_abs = os.path.normcase(os.path.abspath(source_path))
    src_dir = os.path.normcase(os.path.dirname(src_abs))
    src_name = os.path.basename(source_path)
    src_tokens = _tokenize_tokens(src_name)
    src_topics = _topic_tags(src_name)

    scored: list[tuple[float, dict]] = []
    for item in items:
        path = item["path"]
        try:
            p_abs = os.path.normcase(os.path.abspath(path))
        except OSError:
            continue
        if p_abs == src_abs:
            continue
        name_low = item["name"].lower()
        if name_low.startswith("~$"):
            continue
        # не брать собственные результаты агента как «образец»
        if any(
            x in name_low
            for x in (
                "_оформлен",
                "оформлен.",
                "_исправлен",
                "исправлен_агент",
                "_converted",
            )
        ):
            continue
        if not _examples_topic_compatible(source_path, path):
            continue
        score = 0.0
        p_dir = os.path.normcase(os.path.dirname(p_abs))
        if p_dir == src_dir:
            score += 100
        elif os.path.basename(p_dir) == os.path.basename(src_dir):
            score += 40
        if item.get("aligned"):
            score += 35
        tok = _tokenize_tokens(item["name"])
        common = src_tokens & tok
        score += 12 * len(common)
        # сильный бонус за ту же узкую тему (ЦТП, КЛ…)
        ex_topics = _topic_tags(item["name"])
        score += 80 * len(src_topics & ex_topics)
        if "цтп" in src_tokens and "цтп" in tok:
            score += 200
        if "эксплуатац" in src_name.lower() and "эксплуатац" in name_low:
            score += 60
        # штраф за чужую тему
        if ("ctp_itp" in src_topics) and ("cable_kl" in ex_topics):
            score -= 500
        if ("cable_kl" in src_topics) and ("ctp_itp" in ex_topics):
            score -= 500
        # свежесть
        score += min(item.get("mtime", 0) / 1e12, 5)
        item = dict(item)
        item["score"] = score
        item["common_tokens"] = sorted(common)
        scored.append((score, item))

    if not scored:
        return items[0] if items else None
    scored.sort(key=lambda x: (-x[0], -x[1].get("mtime", 0)))
    best = scored[0][1]
    log(
        f"AUTO example type={doc_type} score={best.get('score')} "
        f"file={best.get('name')} common={best.get('common_tokens')}"
    )
    return best


def find_examples(doc_type: str, limit: int = 12, source_path: str | None = None) -> list[dict]:
    """
    Ищет локальные примеры ТОЛЬКО из рабочих папок пользователя.
    Приоритет — файлы с правками СНиОТ/Дубовик (уже с учётом Инструкции).
    Интернет-шаблоны не используются (SAMPLE_POLICY.allow_web_templates = False).
    """
    if SAMPLE_POLICY.get("allow_web_templates"):
        log("WARNING: web templates enabled — отклонение от политики")

    cfg = load_config()
    keywords = list(DOCUMENT_TYPES.get(doc_type, {}).get("keywords", []) or [])
    # для инструкций по эксплуатации ищем по теме исходника, а не только «положение о»
    src_l = (os.path.basename(source_path) if source_path else "").lower()
    if doc_type == "polozhenie":
        keywords.extend(
            [
                "инструкция по эксплуатации",
                "по эксплуатации",
                "эксплуатации цтп",
                "эксплуатации итп",
            ]
        )
        if "цтп" in src_l:
            keywords.append("цтп")
        if "кл" in src_l or "кабель" in src_l:
            keywords.extend(["силовых кл", "кабельн", "0,4-10", "0.4-10"])
    roots = [str(r) for r in READONLY_SAMPLE_DIRS if r.is_dir()]
    for r in cfg.get("example_roots") or SAMPLE_POLICY.get("local_practice_roots", []):
        if is_path_readonly_sample(r) and os.path.isdir(r) and r not in roots:
            roots.append(r)
    results = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            rel = os.path.relpath(dirpath, root)
            if rel.count(os.sep) > 3:
                continue
            for name in filenames:
                low = name.lower()
                if not low.endswith((".docx", ".doc", ".rtf")):
                    continue
                if keywords and not any(k in low for k in keywords):
                    if doc_type == "prikaz" and "приказ" not in low:
                        continue
                    if doc_type != "prikaz":
                        continue
                full = os.path.join(dirpath, name)
                try:
                    mtime = os.path.getmtime(full)
                except OSError:
                    continue
                aligned = is_instruction_aligned_sample(name)
                results.append(
                    {
                        "path": full,
                        "name": name,
                        "folder": os.path.basename(dirpath),
                        "mtime": mtime,
                        "aligned": aligned,
                        "label": (
                            f"[по Инструкции/СНиОТ] {os.path.basename(dirpath)} | {name}"
                            if aligned
                            else f"[локальный] {os.path.basename(dirpath)} | {name}"
                        ),
                    }
                )
    # сначала ваши правки (aligned), потом свежие
    results.sort(key=lambda x: (not x["aligned"], -x["mtime"]))
    seen = set()
    uniq = []
    for item in results:
        if item["name"] in seen:
            continue
        seen.add(item["name"])
        uniq.append(item)
        if len(uniq) >= limit:
            break
    return uniq


def _clean_output_stem(stem: str) -> str:
    """Имя результата = имя исходника + «_оформлен», без накопления суффиксов."""
    s = stem or ""
    while True:
        low = s.lower()
        if low.endswith("_converted"):
            s = s[: -len("_converted")]
            continue
        if low.endswith("_оформлен"):
            s = s[: -len("_оформлен")]
            continue
        if low.endswith("_исправлен_агент"):
            s = s[: -len("_исправлен_агент")]
            continue
        break
    return s


def output_path_for(input_path: str) -> str:
    stem = _clean_output_stem(Path(input_path).stem)
    return str(USER_AGENT_DIR / f"{stem}_оформлен.docx")


def _can_write_file(path: str) -> bool:
    """Проверка: можно ли создать/перезаписать файл (только папка Агент)."""
    try:
        assert_path_writable(path)
    except PermissionError:
        return False
    try:
        parent = os.path.dirname(path) or "."
        if not os.path.isdir(parent):
            return False
        if os.path.exists(path):
            with open(path, "r+b"):
                pass
            return True
        test = os.path.join(parent, f"~docagent_wtest_{os.getpid()}.tmp")
        with open(test, "wb") as f:
            f.write(b"0")
        os.remove(test)
        return True
    except OSError:
        return False


def _is_desktop_path(path: str | Path) -> bool:
    try:
        resolved = str(Path(path).resolve()).lower()
        desktop = str(Path.home() / "Desktop").lower()
        return resolved.startswith(desktop)
    except OSError:
        return False


def _output_candidates_for_input(input_path: str) -> list[str]:
    """Пути для *_оформлен.docx: папка Агент / рядом с исходником; Desktop только если источник там."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = _clean_output_stem(Path(input_path).stem)
    candidates: list[str] = []

    if is_path_in_user_agent_dir(input_path):
        same_dir = Path(input_path).parent
        candidates.extend(
            [
                str(same_dir / f"{stem}_оформлен.docx"),
                str(same_dir / f"{stem}_оформлен_{stamp}.docx"),
            ]
        )
        preferred = output_path_for(input_path)
        for path in (preferred, f"{os.path.splitext(preferred)[0]}_{stamp}.docx"):
            if path not in candidates:
                candidates.append(path)
    else:
        preferred = output_path_for(input_path)
        candidates.extend(
            [
                preferred,
                f"{os.path.splitext(preferred)[0]}_{stamp}.docx",
            ]
        )

    if _is_desktop_path(input_path):
        desktop = Path.home() / "Desktop"
        for path in (
            str(desktop / f"{stem}_оформлен.docx"),
            str(desktop / f"{stem}_оформлен_{stamp}.docx"),
        ):
            if path not in candidates:
                candidates.append(path)

    return candidates


def pick_output_path(input_path: str, work_path: str | None = None) -> tuple[str, str | None]:
    """
    Выбрать путь для *_оформлен.docx.
    Имя — от исходного файла (.doc/.rtf/.docx), не от _converted.
    Для документов из папки Агент — сохранение в ту же папку, не на Desktop.
    """
    candidates = _output_candidates_for_input(input_path)
    note = None
    for i, path in enumerate(candidates):
        if _can_write_file(path):
            if i > 0:
                note = (
                    "Рядом с исходником сохранить не удалось (часто файл уже открыт в Word "
                    f"или нет прав на сетевую папку). Сохранено сюда:\n{path}"
                )
                log(f"OUTPUT fallback -> {path}")
            return path, note

    last = candidates[-1]
    note = (
        "Нет доступа к папке документа. Попытка сохранить:\n"
        f"{last}\n\nЗакройте в Word файлы с окончанием «_оформлен.docx» и повторите."
    )
    return last, note


def friendly_save_error(err: BaseException, out_path: str) -> RuntimeError:
    msg = str(err)
    if isinstance(err, PermissionError) or "Permission denied" in msg or "[Errno 13]" in msg:
        return RuntimeError(
            "Нет доступа к файлу результата (Permission denied).\n\n"
            "Что сделать:\n"
            "1) Закройте в Word файл с окончанием «_оформлен.docx» "
            "(и сам исходник, если он открыт только для чтения с блокировкой).\n"
            "2) Нажмите «Оформить» ещё раз — агент сохранит в папку Агент с новым именем, "
            "если исходный файл открыт в Word.\n\n"
            f"Путь: {out_path}"
        )
    return RuntimeError(f"Ошибка оформления: {err}")


def _apply_russian_language_check(summary: dict, cfg: dict, enabled: bool) -> dict:
    """После оформления — внимательно перечитать весь текст на русский язык."""
    if not enabled:
        return summary
    out = summary.get("output")
    if not out or not os.path.isfile(out):
        return summary
    try:
        from formatters.russian_check import check_and_fix_document

        log(f"RUSSIAN CHECK start: {out}")
        rep = check_and_fix_document(
            out,
            use_yandex=bool(cfg.get("russian_check_yandex", True)),
            use_languagetool=bool(cfg.get("russian_check_languagetool", True)),
            auto_fix=True,
            lt_max_paragraphs=int(cfg.get("russian_check_lt_max_paragraphs", 20)),
        )
        summary["russian_check"] = rep
        summary["actions"].append(
            "Проверка русского языка (весь текст): "
            f"локально {rep.get('local_fixes', 0)}, "
            f"орфография {rep.get('speller_fixes', 0)}, "
            f"грамматика/замечания {rep.get('grammar_notes', 0)}"
        )
        if rep.get("report_path"):
            summary["actions"].append(f"Отчёт по русскому языку: {rep['report_path']}")
        for s in (rep.get("suggestions") or [])[:5]:
            summary["actions"].append(s)
        log(
            f"RUSSIAN CHECK done spell={rep.get('speller_fixes')} "
            f"notes={rep.get('grammar_notes')}"
        )
        # проверка русского может переписать runs — вернуть эталон подписантов
        try:
            from docx import Document
            from formatters.common import save_docx_unprotected
            from formatters.structure_fix import (
                apply_signatory_block_format,
                resolve_instruction_doc_type,
            )

            dtype = resolve_instruction_doc_type(
                doc_type=summary.get("type") or "",
                doc=Document(out),
                source_path=summary.get("input") or out,
            )
            if dtype in ("rabochaya_instrukciya", "dolzhnostnaya_instrukciya"):
                doc_s = Document(out)
                n_sign = apply_signatory_block_format(doc_s, doc_type=dtype)
                if n_sign:
                    save_docx_unprotected(doc_s, out)
                    summary["actions"].append(
                        f"После проверки русского — подписанты восстановлены ({n_sign})"
                    )
        except Exception as e_sign:
            log(f"signatories restore after RU check: {e_sign}")
    except Exception as e:
        log(f"RUSSIAN CHECK fail: {e}")
        summary["actions"].append(
            "Проверка русского языка не завершена (нет сети или сервис недоступен). "
            "Проверьте вручную: https://languagetool.org/ru и https://text.ru/spelling. "
            f"Ошибка: {e}"
        )
    return summary


def process_document(
    input_path: str,
    doc_type: str,
    example_path: str | None = None,
    use_basic_only: bool = False,
    apply_text_edits_flag: bool = True,
    apply_russian_check_flag: bool | None = None,
    structure_rebuild_flag: bool = False,
) -> dict:
    """
    Главный обработчик.
    example_path — образец (read-only, часто из ОБМЕН); если не указан, агент выбирает сам.
    apply_text_edits_flag — правки/удаления по сравнению ваших файлов в РАССМОТРЕНИЕ.
    apply_russian_check_flag — перечитать весь текст на правила русского языка.
    structure_rebuild_flag — перестроить содержание по структуре образца (ЦЭМ и т.п.).
    """
    if not is_path_in_user_agent_dir(input_path):
        raise RuntimeError(
            "Редактировать можно только файлы в папке Агент:\n"
            f"{USER_AGENT_DIR}\n\n"
            f"Указан: {input_path}\n\n"
            "Образцы из папки ОБМЕН — только для чтения (эталон), не для правки."
        )
    cfg = load_config()
    if apply_russian_check_flag is None:
        apply_russian_check_flag = bool(cfg.get("russian_language_check", True))

    def finish(summary: dict) -> dict:
        # ВСЕГДА проверять документ перед выдачей результата
        out_path = summary.get("output") or ""
        try:
            from formatters.publish_check import verify_document_before_publish

            if out_path and os.path.isfile(out_path):
                chk = verify_document_before_publish(
                    out_path, doc_type=summary.get("type") or doc_type
                )
                summary["publish_check"] = chk
                if chk.get("details"):
                    summary.setdefault("actions", []).append(
                        "Проверка перед публикацией: "
                        + (
                            "ОК"
                            if chk.get("ok") and not chk.get("issues")
                            else "есть замечания"
                        )
                    )
                for d in (chk.get("details") or [])[:6]:
                    if d not in (summary.get("actions") or []):
                        summary.setdefault("actions", []).append(d)
                for iss in (chk.get("issues") or [])[:5]:
                    summary.setdefault("actions", []).append("⚠ " + iss)
                    log(f"PUBLISH CHECK issue: {iss}")
        except Exception as e:
            log(f"publish_check error: {e}")
            summary.setdefault("actions", []).append(
                f"проверка перед публикацией: ошибка ({e})"
            )
        summary = _apply_russian_language_check(
            summary, cfg, bool(apply_russian_check_flag)
        )
        # ВСЕГДА в самом конце: примечания + подписанты по эталону
        # Для ДИ САТП «Старший мастер» — пропуск: finalize ломает нумерацию; финал = fix_sniot_document
        try:
            if summary.get("conservative_di_satp"):
                log("Conservative DI: skip finalize_notes_and_signatories")
                summary.setdefault("actions", []).append(
                    "ДИ САТП (Старший мастер): без text_edits и finalize — только fix_sniot_document"
                )
            else:
                from formatters.structure_fix import finalize_notes_and_signatories

                if out_path and os.path.isfile(out_path):
                    fin = finalize_notes_and_signatories(
                        out_path, summary.get("type") or doc_type
                    )
                    summary["finalize_tail"] = fin
                    if fin.get("notes_moved"):
                        summary.setdefault("actions", []).append(
                            f"Примечания перенесены перед «Разработал:» ({fin['notes_moved']})"
                        )
                    if fin.get("razrabotal_added"):
                        summary.setdefault("actions", []).append(
                            "Добавлена строка «Разработал:» перед подписантом"
                        )
                    if fin.get("signatories"):
                        summary.setdefault("actions", []).append(
                            f"Подписанты по эталону ({fin.get('doc_type')}): {fin['signatories']}"
                        )
                    if fin.get("indents_fixed") is not None:
                        summary.setdefault("actions", []).append(
                            f"Отступ 1,25 см на текст (правок: {fin.get('indents_fixed', 0)})"
                        )
                    if fin.get("chapters_styled") or fin.get("chapters_centered"):
                        summary.setdefault("actions", []).append(
                            f"Главы по центру: {fin.get('chapters_styled') or fin.get('chapters_centered')}; "
                            f"пустых строк перед главами: {fin.get('chapter_spacers', 0)}"
                        )
                    ch_rep = fin.get("chapters_repaired") or {}
                    if ch_rep.get("numbered") or ch_rep.get("merged"):
                        summary.setdefault("actions", []).append(
                            f"Восстановлены номера глав: {ch_rep.get('numbered', 0)}, "
                            f"склеено строк: {ch_rep.get('merged', 0)}"
                        )
                    toc = fin.get("contents_page") or {}
                    if toc.get("found"):
                        summary.setdefault("actions", []).append(
                            "«Содержание» на отдельной странице; первая глава с верха листа"
                        )
                    if fin.get("table_fonts") is not None:
                        summary.setdefault("actions", []).append(
                            f"Таблицы тела — 12 пт (правок шрифта: {fin.get('table_fonts', 0)})"
                        )
                    if fin.get("numbers_fixed") is not None:
                        summary.setdefault("actions", []).append(
                            f"Нумерация проверена (+{fin.get('numbers_fixed', 0)})"
                        )
        except Exception as e:
            log(f"finalize_tail error: {e}")
            summary.setdefault("actions", []).append(
                f"финал примечаний/подписантов: ошибка ({e})"
            )
        # ВСЕГДА ПОСЛЕДНИМ: полные правила СНиОТ (fix_sniot_document.py)
        try:
            from formatters.sniot_document import (
                apply_sniot_rules_to_output,
                should_apply_sniot_pass,
            )

            src = summary.get("input") or input_path
            dtype = summary.get("type") or doc_type
            if (
                out_path
                and os.path.isfile(out_path)
                and out_path.lower().endswith(".docx")
                and dtype != "prikaz"
                and should_apply_sniot_pass(src, out_path, dtype)
            ):
                log(f"SNIOT pass start: {out_path}")
                sniot = apply_sniot_rules_to_output(out_path)
                summary["sniot_pass"] = sniot
                for act in sniot.get("actions") or []:
                    if act not in (summary.get("actions") or []):
                        summary.setdefault("actions", []).append(act)
                after_n = len(sniot.get("after_issues") or [])
                if sniot.get("ok") and sniot.get("applied"):
                    log(f"SNIOT pass OK: validation issues={after_n}")
                elif sniot.get("applied"):
                    log(f"SNIOT pass done with issues ({after_n}): {sniot.get('after_issues')}")
                elif not sniot.get("applied"):
                    log(f"SNIOT pass not applied: {sniot.get('actions')}")
                else:
                    log(f"SNIOT pass failed: {sniot.get('actions')}")
            elif out_path and dtype != "prikaz":
                log(
                    f"SNIOT pass skipped src={src!r} out={out_path!r} "
                    f"type={dtype} apply={should_apply_sniot_pass(src, out_path, dtype)}"
                )
        except Exception as e:
            log(f"sniot_pass error: {e}")
            summary.setdefault("actions", []).append(
                f"финал СНиОТ (fix_sniot_document): ошибка ({e})"
            )
        return summary

    # если в имени/титуле ясно написано РИ/ДИ — не оформлять как приказ
    try:
        detected, _preview = detect_document(input_path)
    except Exception:
        detected = doc_type
    name_l = os.path.basename(input_path).lower()
    strong = {
        "rabochaya_instrukciya": ("рабоч" in name_l and "инструкц" in name_l)
        or detected == "rabochaya_instrukciya",
        "dolzhnostnaya_instrukciya": (
            ("должностн" in name_l and "инструкц" in name_l)
            or name_l.startswith("ди ")
            or ("проект" in name_l and "мастер" in name_l)
            or detected == "dolzhnostnaya_instrukciya"
        ),
        "polozhenie": (
            ("положен" in name_l)
            or ("инструкц" in name_l and "эксплуатац" in name_l)
            or (
                "инструкц" in name_l
                and "рабоч" not in name_l
                and "должностн" not in name_l
                and "охране труда" not in name_l
                and "иот" not in name_l
            )
            or detected == "polozhenie"
        ),
        "instrukciya_ot": "охране труда" in name_l or "иот" in name_l,
    }
    if detected in strong and strong.get(detected) and detected != doc_type:
        log(f"TYPE override {doc_type} -> {detected} (имя/титул документа)")
        doc_type = detected

    conservative_di = is_conservative_di_satp(input_path, doc_type)
    if conservative_di:
        apply_text_edits_flag = False
        log("Conservative DI САТП: text_edits и structure_fix отключены")

    if not example_path and doc_type not in ("unsupported",):
        best = choose_best_example(doc_type, input_path)
        if best:
            example_path = best["path"]
            log(f"AUTO-selected example: {example_path}")

    log(
        f"PROCESS type={doc_type} file={input_path} example={example_path} "
        f"text_edits={apply_text_edits_flag} russian_check={apply_russian_check_flag} "
        f"structure_rebuild={structure_rebuild_flag}"
    )

    work_path = input_path
    ext = os.path.splitext(work_path)[1].lower()
    if ext in (".doc", ".rtf"):
        try:
            work_path = convert_to_docx(work_path)
            log(f"Converted to {work_path}")
        except Exception as e:
            raise RuntimeError(
                "Не удалось открыть файл старого формата (.doc / .rtf).\n\n"
                "Нужны:\n"
                "• пакет pywin32 — запустите install_deps.bat в папке DocAgent\n"
                "• установленный Microsoft Word\n\n"
                "Либо откройте файл в Word и сохраните как .docx, затем оформите снова.\n\n"
                f"Ошибка: {e}"
            ) from e

    # --- Перестройка содержания по образцу (Положение о ПК ↔ ЦЭМ) ---
    if structure_rebuild_flag or doc_type == "polozhenie":
        try:
            from formatters.polozhenie_pk_rebuild import (
                is_cem_sample,
                is_pk_polozhenie,
                rebuild_pk_by_cem_structure,
            )

            want = structure_rebuild_flag or (
                is_pk_polozhenie(input_path) and is_cem_sample(example_path)
            )
            if want and (is_pk_polozhenie(input_path) or is_cem_sample(example_path)):
                rebuilt = rebuild_pk_by_cem_structure(
                    input_path,
                    sample_path=example_path,
                    python_exe=cfg.get("python_exe"),
                )
                rebuilt.setdefault("input", input_path)
                rebuilt.setdefault("work", work_path)
                rebuilt.setdefault("type", "polozhenie")
                rebuilt.setdefault("example", example_path)
                rebuilt.setdefault("actions", [])
                # довести отступы / пустые / заголовки по правилам делопроизводства
                try:
                    from formatters.format_polozhenie_pk import format_polozhenie_pk

                    fmt = format_polozhenie_pk(rebuilt["output"], backup=False)
                    rebuilt["actions"].append(
                        f"Оформление: заголовков {fmt.get('headings', 0)}, "
                        f"пустых перед главами +{fmt.get('empties_added', 0)}"
                    )
                except Exception as fe:
                    log(f"format_polozhenie_pk error: {fe}")
                    rebuilt["actions"].append(f"оформление отступов: ошибка ({fe})")
                rebuilt["actions"].insert(
                    0,
                    "Режим: перестройка содержания по структуре образца (не только шрифты)",
                )
                return finish(rebuilt)
        except Exception as e:
            log(f"structure_rebuild skipped/failed: {e}")
            if structure_rebuild_flag:
                # явный запрос — не молча откатываться к «оформить»
                from formatters.ai_handoff import open_cursor_with_task, write_cursor_task

                prompt = write_cursor_task(
                    source_path=input_path,
                    sample_path=example_path,
                    doc_type=doc_type,
                    goal=(
                        "Перестроить положение о производственном контроле МКТС "
                        "по структуре образца П.ЦЭМ 10-02-2023. "
                        f"Локальная перестройка не удалась: {e}"
                    ),
                )
                ok, msg = open_cursor_with_task(prompt)
                raise RuntimeError(
                    "Автоматическая перестройка не удалась — задание передано в Cursor.\n\n"
                    + msg
                ) from e

    out, out_note = pick_output_path(input_path, work_path)
    margins = dict(cfg.get("margins_mm", {"left": 30, "right": 15, "top": 20, "bottom": 20}))
    font_name = cfg.get("font_name", "Times New Roman")
    font_size = int(cfg.get("font_size_pt", 14))
    first_indent = float(cfg.get("first_indent_mm", 12.5))

    # Для РИ в ваших примерах правое поле часто 12,5 мм
    if doc_type == "rabochaya_instrukciya":
        margins["right"] = 12.5

    summary = {
        "input": input_path,
        "work": work_path,
        "output": out,
        "type": doc_type,
        "example": example_path,
        "actions": [],
        "conservative_di_satp": conservative_di,
    }
    if out_note:
        summary["actions"].append(out_note)

    meta = DOCUMENT_TYPES.get(doc_type, DOCUMENT_TYPES["unsupported"])

    try:
        if doc_type == "prikaz" and not use_basic_only:
            from formatters import prikaz_builder

            prikaz_builder.Settings.DIRECTOR_NAME = cfg.get("director_name", "А.А.Вирочкин")
            prikaz_builder.Settings.DIRECTOR_TITLE = cfg.get("director_title", "Директор")
            prikaz_builder.format_prikaz(work_path, out)
            summary["actions"].append("Приказ пересобран по образцам папки «Приказы»")
            summary["mode"] = "prikaz_rebuild"
            return finish(summary)

        # ДИ / РИ / Положение / ОТ — оформление + текстовые правки по вашей практике
        text_types = {
            "rabochaya_instrukciya",
            "dolzhnostnaya_instrukciya",
            "polozhenie",
            "instrukciya_ot",
        }
        if apply_text_edits_flag and doc_type in text_types:
            from formatters.text_edits import apply_text_edits

            edit_report = apply_text_edits(
                work_path,
                out,
                also_basic_format=True,
                apply_name_updates=True,
                apply_list_markers=False,
                doc_type=doc_type,
            )
            summary["actions"].extend(meta.get("notes", []))
            if edit_report.get("staff_category"):
                from formatters.normative_docs_policy import category_label_ru

                summary["actions"].append(
                    "Категория персонала: " + category_label_ru(edit_report["staff_category"])
                )
            summary["actions"].append(
                f"Текстовые правки по вашей практике: всего {edit_report.get('total_edits', 0)} "
                f"(замен {edit_report['replacements']}, кодов ТКП {edit_report['tkp_codes']}, "
                f"удалений абзацев {edit_report['deleted_paragraphs']})"
            )
            for d in edit_report.get("details", [])[:8]:
                summary["actions"].append(d)
            if example_path:
                summary["actions"].append(f"Ориентир по образцу: {os.path.basename(example_path)}")
            summary["mode"] = "basic_plus_text_edits"
            summary["edit_report"] = edit_report
            return finish(summary)

        # прочее / без текстовых правок — только базовое оформление
        result = apply_basic_office_format(
            work_path,
            out,
            margins=margins,
            font_name=font_name,
            font_size_pt=font_size,
            first_indent_mm=first_indent,
            right_margin_mm=margins["right"],
        )
        summary["actions"].extend(meta.get("notes", []))
        summary["actions"].append(
            f"Применено базовое оформление: абзацев {result['paragraphs_touched']}, "
            f"фрагментов текста {result['runs_touched']}"
        )
        if example_path:
            summary["actions"].append(f"Ориентир по образцу: {os.path.basename(example_path)}")
        summary["mode"] = result["mode"]
        summary.update(result)
        return finish(summary)
    except (PermissionError, OSError) as e:
        # повтор в папке Агент (с timestamp), не на Desktop
        if isinstance(e, PermissionError) or getattr(e, "errno", None) == 13 or "Permission denied" in str(e):
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            retry_candidates: list[str] = []
            if is_path_in_user_agent_dir(input_path):
                same_dir = Path(input_path).parent
                stem = _clean_output_stem(Path(work_path).stem)
                retry_candidates.extend(
                    [
                        str(same_dir / f"{stem}_оформлен_{stamp}.docx"),
                        str(same_dir / f"{Path(work_path).stem}_оформлен_{stamp}.docx"),
                    ]
                )
            if _is_desktop_path(input_path):
                desk = Path.home() / "Desktop"
                retry_candidates.append(
                    str(desk / f"{Path(work_path).stem}_оформлен_{stamp}.docx")
                )
            alt = None
            for candidate in retry_candidates:
                if _can_write_file(candidate):
                    alt = candidate
                    break
            if alt is None:
                raise friendly_save_error(e, out) from e
            log(f"SAVE retry -> {alt} after {e}")
            try:
                if doc_type == "prikaz" and not use_basic_only:
                    from formatters import prikaz_builder

                    prikaz_builder.format_prikaz(work_path, alt)
                    summary["output"] = alt
                    summary["actions"].append(
                        "Исходная папка была занята (часто файл открыт в Word). "
                        f"Результат сохранён:\n{alt}"
                    )
                    summary["mode"] = "prikaz_rebuild"
                    return finish(summary)
                text_types = {
                    "rabochaya_instrukciya",
                    "dolzhnostnaya_instrukciya",
                    "polozhenie",
                    "instrukciya_ot",
                }
                if apply_text_edits_flag and doc_type in text_types:
                    from formatters.text_edits import apply_text_edits

                    edit_report = apply_text_edits(
                        work_path,
                        alt,
                        also_basic_format=True,
                        apply_name_updates=True,
                        apply_list_markers=False,
                        doc_type=doc_type,
                    )
                    summary["output"] = alt
                    summary["actions"].append(
                        "Исходная папка была занята. Результат сохранён:\n" + alt
                    )
                    summary["mode"] = "basic_plus_text_edits"
                    summary["edit_report"] = edit_report
                    return finish(summary)
                result = apply_basic_office_format(
                    work_path,
                    alt,
                    margins=margins,
                    font_name=font_name,
                    font_size_pt=font_size,
                    first_indent_mm=first_indent,
                    right_margin_mm=margins["right"],
                )
                summary["output"] = alt
                summary["actions"].append(
                    "Исходная папка была занята. Результат на Рабочем столе:\n" + alt
                )
                summary["mode"] = result["mode"]
                summary.update(result)
                return finish(summary)
            except Exception as e2:
                raise friendly_save_error(e2, alt) from e2
        raise friendly_save_error(e, out) from e
    except Exception as e:
        log(f"PROCESS fail: {type(e).__name__}: {e!r}")
        msg = str(e).strip()
        if not msg or msg.lower() == "none":
            msg = f"{type(e).__name__}: сбой при оформлении документа"
        raise RuntimeError(f"Ошибка оформления: {msg}") from e
