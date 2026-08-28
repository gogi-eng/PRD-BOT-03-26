# -*- coding: utf-8 -*-
"""
Постоянное обучение агента по вашим действиям с документами.

Что делает:
1) Следит за папками РАССМОТРЕНИЕ / Дубовик / Нормативка / Desktop
2) При сохранении .docx/.doc сравнивает с предыдущим снимком текста
3) Находит повторяющиеся замены / удаления
4) После 2+ повторов дописывает правило в learned_edit_patterns.json

Интернет-шаблоны не используются — только ваши локальные правки
и требования Инструкции по делопроизводству.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LEARN_DIR = ROOT / "learning"
SNAP_DIR = LEARN_DIR / "snapshots"
OBS_PATH = LEARN_DIR / "observations.jsonl"
STATS_PATH = LEARN_DIR / "stats.json"
PATTERNS_PATH = ROOT / "learned_edit_patterns.json"
CONFIG_PATH = ROOT / "config.json"
LEARN_LOG = ROOT / "logs" / "learning.log"

# порог: сколько раз правка должна встретиться, чтобы стать правилом
PROMOTE_AFTER = 2
# не учить слишком длинные куски (шум)
MAX_PHRASE = 120
MIN_PHRASE = 4


def is_safe_learned_replacement(old: str, new: str) -> bool:
    """
    Отбраковать ядовитые автозамены (разрезание терминов, склейка «поэксплуатации»,
    подстановка чужих ФИО, вставка номеров пунктов в чужой текст).
    """
    if not old or not new or old == new:
        return False
    o = old.strip()
    n = new.strip()
    if len(o) < MIN_PHRASE or len(n) < MIN_PHRASE:
        return False
    ol, nl = o.lower(), n.lower()

    # разрезание одного слова пробелом: теплоустановок → тепло установок
    if " " not in o and " " in n:
        if re.fullmatch(r"[A-Za-zА-Яа-яЁё\-]+", o) and n.replace(" ", "") == o:
            return False
        if abs(len(n.replace(" ", "")) - len(o)) <= 1 and len(n.split()) == 2:
            if any(
                k in ol
                for k in (
                    "теплоустанов",
                    "электроустанов",
                    "газопровод",
                    "трубопровод",
                    "теплообмен",
                )
            ):
                return False

    # склейка предлога с словом: по эксплуатации → поэксплуатации
    if " " in o and " " not in n and o.replace(" ", "") == n:
        if re.search(r"(?i)\bпо\s+эксплуатац", o):
            return False

    # замена шапки предприятия / титула на ТКП, Правила, ГОСТ
    if re.match(r"(?i)^(ТКП|ГОСТ|СНиП|Правила|«Правила)\b", n) and not re.match(
        r"(?i)^(ТКП|ГОСТ|СНиП|Правила|«Правила)\b", o
    ):
        return False
    if any(
        marker in o.upper()
        for marker in (
            "КОММУНАЛЬНЫХ ТЕПЛОВЫХ СЕТЕЙ",
            "МИНСКИЙ ГОРОДСКОЙ ИСПОЛНИТЕЛЬНЫЙ",
            "МИНСККОММУНТЕПЛОСЕТЬ",
            "КОММУНАЛЬНОЕ УНИТАРНОЕ ПРОИЗВОДСТВЕННОЕ",
        )
    ) and n.upper() != o.upper():
        if "ТКП" in n.upper() or "ПРАВИЛА" in n.upper():
            return False

    # аббревиатура подразделения: ЛСиМ → Осим
    if " " not in o and " " not in n and o.isalpha() and n.isalpha():
        if 2 <= len(o) <= 6 and 2 <= len(n) <= 6 and ol != nl:
            return False

    # один номер ТКП нельзя подменять другим
    m_old = re.search(r"ТКП\s+(\d+-\d+)", o, re.I)
    m_new = re.search(r"ТКП\s+(\d+-\d+)", n, re.I)
    if m_old and m_new and m_old.group(1) != m_new.group(1):
        return False
    if "сследить" in nl and "сследить" not in ol:
        return False
    if "поэксплуатац" in nl and "поэксплуатац" not in ol:
        return False
    if "тепло установок" in nl and "тепло установок" not in ol:
        return False

    # «исполнения» → «исполнении» ломает «для исполнения»
    if ol == "исполнения" and nl == "исполнении":
        return False

    # падеж термина опрессовк* → именительный «опрессовка»
    if "опрессовк" in ol and nl in ("опрессовка", "опрессовку") and ol != nl:
        if ol != "опрессовка":
            return False

    # вставка нумерации пункта в произвольную фразу
    if re.match(r"^\d+\.\d+", n) and not re.match(r"^\d+\.\d+", o):
        return False

    # усечение должности до «… по» или подстановка конкретного ФИО вместо общей фразы
    if nl.rstrip(".").endswith(" по") and len(n) + 8 < len(o):
        return False
    if re.search(r"[А-ЯЁ]\.[А-ЯЁ]\.\s*[А-ЯЁа-яё\-]+", n) and not re.search(
        r"[А-ЯЁ]\.[А-ЯЁ]\.\s*[А-ЯЁа-яё\-]+", o
    ):
        if any(k in ol for k in ("заместитель", "начальник", "инженер", "директор")):
            return False

    # явный мусор склейки без пробела вокруг цифр
    if re.search(r"[а-яё]\d|\d[а-яё]", ol) and re.search(r"[а-яё]{4,}\d|\d[а-яё]{4,}", nl):
        return False
    if re.search(r"[а-яё]\d+\.\d+", o) and "ремонт" in nl and "ремонт" not in ol:
        return False

    return True

WATCH_SUFFIXES = {".docx", ".doc", ".rtf"}


def _log(msg: str) -> None:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(LEARN_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def watch_roots() -> list[str]:
    cfg = load_config()
    roots = list(cfg.get("example_roots", []))
    roots.extend(cfg.get("instruction_sources", []))
    # папки, а не файлы
    folder_roots = []
    for r in roots:
        if os.path.isdir(r):
            folder_roots.append(r)
        elif os.path.isfile(r):
            folder_roots.append(os.path.dirname(r))
    # Desktop / Downloads — частая работа
    desk = str(Path.home() / "Desktop")
    downs = str(Path.home() / "Downloads")
    for extra in (desk, downs, str(Path.home() / "Desktop" / "Нормативка")):
        if os.path.isdir(extra) and extra not in folder_roots:
            folder_roots.append(extra)
    # уникальные
    seen = set()
    out = []
    for r in folder_roots:
        key = os.path.normcase(os.path.abspath(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def extract_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext == ".docx":
            from docx import Document

            doc = Document(path)
            parts = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
            return "\n".join(parts)
        if ext in (".doc", ".rtf"):
            try:
                from formatters.word_com import open_docx_readonly

                with open_docx_readonly(path) as d:
                    text = d.Content.Text or ""
                return text.replace("\r", "\n")
            except Exception as e:
                _log(f"doc/rtf extract fail {path}: {e}")
                return ""
    except Exception as e:
        _log(f"extract fail {path}: {e}")
        return ""
    return ""


def snap_key(path: str) -> str:
    norm = os.path.normcase(os.path.abspath(path))
    return hashlib.sha1(norm.encode("utf-8")).hexdigest()


def load_snapshot(path: str) -> str | None:
    p = SNAP_DIR / f"{snap_key(path)}.txt"
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8", errors="replace")


def save_snapshot(path: str, text: str) -> None:
    SNAP_DIR.mkdir(parents=True, exist_ok=True)
    (SNAP_DIR / f"{snap_key(path)}.txt").write_text(text, encoding="utf-8")
    meta = {
        "path": path,
        "mtime": os.path.getmtime(path) if os.path.exists(path) else None,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "chars": len(text),
    }
    (SNAP_DIR / f"{snap_key(path)}.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_stats() -> dict:
    if not STATS_PATH.exists():
        return {"replacements": {}, "deletions": {}, "promoted": []}
    with open(STATS_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_stats(stats: dict) -> None:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATS_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)


def append_observation(obj: dict) -> None:
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    with open(OBS_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _norm_line(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def diff_paragraphs(old: str, new: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Грубый diff: пары замен похожих строк + удалённые строки."""
    old_lines = [_norm_line(x) for x in old.splitlines() if _norm_line(x)]
    new_lines = [_norm_line(x) for x in new.splitlines() if _norm_line(x)]
    set_old, set_new = set(old_lines), set(new_lines)
    only_old = [x for x in old_lines if x not in set_new]
    only_new = [x for x in new_lines if x not in set_old]

    replacements: list[tuple[str, str]] = []
    used_new = set()
    for ob in only_old:
        ow = set(ob.lower().split())
        best = None
        best_r = 0.0
        for i, nb in enumerate(only_new):
            if i in used_new:
                continue
            nw = set(nb.lower().split())
            if not ow or not nw:
                continue
            inter = len(ow & nw)
            ratio = inter / max(len(ow), 1)
            if ratio >= 0.6 and inter >= 3 and ratio > best_r:
                best_r = ratio
                best = (i, nb)
        if best and ob != best[1]:
            used_new.add(best[0])
            replacements.append((ob, best[1]))

    deleted = [x for x in only_old if all(x != a for a, _ in replacements)]
    return replacements, deleted


def extract_micro_replacements(old: str, new: str) -> list[tuple[str, str]]:
    """Короткие замены фраз внутри похожих строк."""
    if old == new:
        return []
    # простые: общая голова/хвост
    # найти отличающиеся куски разумной длины
    out: list[tuple[str, str]] = []
    # по словам
    ow, nw = old.split(), new.split()
    # если почти одинаковая длина — искать одно слово/фразу
    if abs(len(ow) - len(nw)) <= 3 and len(ow) >= 4:
        # LCS-подобно упрощённо: убрать общий префикс/суффикс
        i = 0
        while i < min(len(ow), len(nw)) and ow[i] == nw[i]:
            i += 1
        j = 0
        while j < min(len(ow) - i, len(nw) - i) and ow[-(j + 1)] == nw[-(j + 1)]:
            j += 1
        old_mid = " ".join(ow[i : len(ow) - j if j else len(ow)])
        new_mid = " ".join(nw[i : len(nw) - j if j else len(nw)])
        if (
            old_mid
            and new_mid
            and old_mid != new_mid
            and MIN_PHRASE <= len(old_mid) <= MAX_PHRASE
            and MIN_PHRASE <= len(new_mid) <= MAX_PHRASE
        ):
            out.append((old_mid, new_mid))
    return out


def _bump(counter_map: dict, key: str, n: int = 1) -> int:
    counter_map[key] = int(counter_map.get(key, 0)) + n
    return counter_map[key]


def promote_to_rules(stats: dict) -> list[str]:
    """Повторяющиеся правки → learned_edit_patterns.json."""
    if not PATTERNS_PATH.exists():
        patterns = {"replace_phrases": [], "delete_phrases": [], "notes_ru": []}
    else:
        with open(PATTERNS_PATH, encoding="utf-8") as f:
            patterns = json.load(f)

    promoted = []
    existing_repl = {
        (x.get("old"), x.get("new")) for x in patterns.get("replace_phrases", []) if isinstance(x, dict)
    }
    existing_del = set(patterns.get("delete_phrases", []))

    for key, cnt in list(stats.get("replacements", {}).items()):
        if cnt < PROMOTE_AFTER:
            continue
        if "|||" not in key:
            continue
        old, new = key.split("|||", 1)
        if (old, new) in existing_repl:
            continue
        if len(old) < MIN_PHRASE or len(new) < MIN_PHRASE:
            continue
        if not is_safe_learned_replacement(old, new):
            _log(f"SKIP unsafe promote: «{old[:60]}» -> «{new[:60]}»")
            continue
        patterns.setdefault("replace_phrases", []).append({"old": old, "new": new})
        existing_repl.add((old, new))
        promoted.append(f"замена x{cnt}: «{old}» -> «{new}»")
        stats.setdefault("promoted", []).append(
            {"type": "replace", "old": old, "new": new, "count": cnt, "at": datetime.now().isoformat()}
        )

    for key, cnt in list(stats.get("deletions", {}).items()):
        if cnt < PROMOTE_AFTER:
            continue
        # удаляем только короткие устойчивые хвосты/фразы
        if not (MIN_PHRASE <= len(key) <= 80):
            continue
        if key in existing_del:
            continue
        # не удалять целые осмысленные длинные обязанности автоматически как delete_phrases
        # только если выглядит как канцелярский хвост / заглушка
        low = key.lower()
        if not any(
            x in low
            for x in (
                "в части",
                "настоящая инструкция",
                "номер инструкции",
                "первичную",
                "в течении",
                "в течение месяца после назначения",
            )
        ):
            continue
        patterns.setdefault("delete_phrases", []).append(key)
        existing_del.add(key)
        promoted.append(f"удаление x{cnt}: «{key}»")
        stats.setdefault("promoted", []).append(
            {"type": "delete", "phrase": key, "count": cnt, "at": datetime.now().isoformat()}
        )

    if promoted:
        notes = patterns.setdefault("notes_ru", [])
        stamp = datetime.now().strftime("%Y-%m-%d")
        note = f"Автообучение {stamp}: добавлено правил {len(promoted)}"
        if note not in notes:
            notes.append(note)
        patterns["last_auto_learn"] = datetime.now().isoformat(timespec="seconds")
        with open(PATTERNS_PATH, "w", encoding="utf-8") as f:
            json.dump(patterns, f, ensure_ascii=False, indent=2)
        _log("PROMOTED: " + "; ".join(promoted[:10]))
    return promoted


def learn_from_file_change(path: str) -> dict:
    """Обработать одно изменение файла."""
    result = {"path": path, "learned": 0, "promoted": []}
    if not os.path.isfile(path):
        return result
    ext = os.path.splitext(path)[1].lower()
    if ext not in WATCH_SUFFIXES:
        return result
    # временные файлы Word
    name = os.path.basename(path)
    if name.startswith("~$") or name.startswith("."):
        return result

    new_text = extract_text(path)
    if not new_text or len(new_text) < 40:
        return result

    old_text = load_snapshot(path)
    save_snapshot(path, new_text)

    if old_text is None:
        append_observation(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "event": "snapshot_init",
                "path": path,
                "chars": len(new_text),
            }
        )
        return result

    if _norm_line(old_text) == _norm_line(new_text):
        return result

    replacements, deleted = diff_paragraphs(old_text, new_text)
    stats = load_stats()
    learned = 0

    for old_l, new_l in replacements:
        for o, n in extract_micro_replacements(old_l, new_l):
            key = f"{o}|||{n}"
            c = _bump(stats.setdefault("replacements", {}), key)
            learned += 1
            append_observation(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "event": "replacement",
                    "path": path,
                    "old": o,
                    "new": n,
                    "count": c,
                }
            )
        # также целиком короткие строки
        if MIN_PHRASE <= len(old_l) <= MAX_PHRASE and MIN_PHRASE <= len(new_l) <= MAX_PHRASE:
            key = f"{old_l}|||{new_l}"
            c = _bump(stats.setdefault("replacements", {}), key)
            learned += 1

    for d in deleted:
        if MIN_PHRASE <= len(d) <= MAX_PHRASE:
            c = _bump(stats.setdefault("deletions", {}), d)
            learned += 1
            append_observation(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "event": "deletion",
                    "path": path,
                    "text": d[:200],
                    "count": c,
                }
            )

    if learned:
        save_stats(stats)
        promoted = promote_to_rules(stats)
        result["learned"] = learned
        result["promoted"] = promoted
        _log(f"learn {path}: +{learned}, promoted={len(promoted)}")

    return result


def record_agent_action(action: str, details: dict | None = None) -> None:
    """Запись действий самого агента (выбор типа, оформление) — тоже обучение."""
    append_observation(
        {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "agent_action",
            "action": action,
            "details": details or {},
        }
    )


class LearningWatcher:
    """Фоновое наблюдение за документами."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._debounce: dict[str, float] = {}
        self._lock = threading.Lock()
        try:
            cfg = load_config()
            self.enabled = bool(cfg.get("continuous_learning", True))
            global PROMOTE_AFTER
            PROMOTE_AFTER = int(cfg.get("learning_promote_after", PROMOTE_AFTER))
        except Exception:
            self.enabled = True
        self.last_event = "ожидание…"
        self.events_count = 0
        self.promoted_count = 0

    def start(self) -> None:
        if not self.enabled:
            self.last_event = "обучение выключено в config"
            _log("LearningWatcher not started (disabled in config)")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="DocAgentLearner", daemon=True)
        self._thread.start()
        _log("LearningWatcher started")
        self.last_event = "обучение запущено"

    def stop(self) -> None:
        self._stop.set()
        self.last_event = "обучение остановлено"
        _log("LearningWatcher stop requested")

    def status_text(self) -> str:
        state = "ВКЛ" if self.enabled and not self._stop.is_set() else "ВЫКЛ"
        return f"Обучение: {state} | событий: {self.events_count} | новых правил: {self.promoted_count} | {self.last_event}"

    def _schedule(self, path: str) -> None:
        with self._lock:
            self._debounce[os.path.abspath(path)] = time.time()

    def _run(self) -> None:
        # попробуем watchdog; иначе polling
        try:
            self._run_watchdog()
        except Exception as e:
            _log(f"watchdog fail, fallback poll: {e}")
            self._run_poll()

    def _run_watchdog(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer

        watcher = self

        class Handler(FileSystemEventHandler):
            def on_modified(self, event):
                if event.is_directory:
                    return
                path = event.src_path
                if os.path.splitext(path)[1].lower() in WATCH_SUFFIXES:
                    watcher._schedule(path)

            def on_created(self, event):
                if event.is_directory:
                    return
                path = event.src_path
                if os.path.splitext(path)[1].lower() in WATCH_SUFFIXES:
                    watcher._schedule(path)

        observer = Observer()
        roots = watch_roots()
        for root in roots:
            try:
                observer.schedule(Handler(), root, recursive=True)
                _log(f"watching {root}")
            except Exception as e:
                _log(f"cannot watch {root}: {e}")
        observer.start()
        try:
            while not self._stop.is_set():
                self._flush_debounce()
                time.sleep(0.5)
        finally:
            observer.stop()
            observer.join(timeout=5)

    def _run_poll(self) -> None:
        mtimes: dict[str, float] = {}
        while not self._stop.is_set():
            if not self.enabled:
                time.sleep(2)
                continue
            for root in watch_roots():
                for dirpath, _, files in os.walk(root):
                    # не слишком глубоко в огромных деревьях Desktop
                    rel = os.path.relpath(dirpath, root)
                    if rel.count(os.sep) > 3:
                        continue
                    for name in files:
                        if os.path.splitext(name)[1].lower() not in WATCH_SUFFIXES:
                            continue
                        if name.startswith("~$"):
                            continue
                        path = os.path.join(dirpath, name)
                        try:
                            mt = os.path.getmtime(path)
                        except OSError:
                            continue
                        prev = mtimes.get(path)
                        if prev is None:
                            mtimes[path] = mt
                            continue
                        if mt > prev:
                            mtimes[path] = mt
                            self._schedule(path)
            self._flush_debounce()
            time.sleep(5)

    def _flush_debounce(self) -> None:
        if not self.enabled:
            return
        now = time.time()
        ready = []
        with self._lock:
            for path, ts in list(self._debounce.items()):
                if now - ts >= 2.0:  # файл «устаканился» после сохранения
                    ready.append(path)
                    del self._debounce[path]
        for path in ready:
            try:
                res = learn_from_file_change(path)
                if res.get("learned"):
                    self.events_count += 1
                    self.last_event = f"учтено: {os.path.basename(path)}"
                if res.get("promoted"):
                    self.promoted_count += len(res["promoted"])
                    self.last_event = f"новое правило: {res['promoted'][0][:60]}"
            except Exception as e:
                _log(f"flush error {path}: {e}")


# глобальный экземпляр для GUI
_watcher: LearningWatcher | None = None


def get_watcher() -> LearningWatcher:
    global _watcher
    if _watcher is None:
        _watcher = LearningWatcher()
    return _watcher
