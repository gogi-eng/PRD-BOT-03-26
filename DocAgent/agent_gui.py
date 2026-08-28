# -*- coding: utf-8 -*-
"""Окно-мастер агента: вопросы о виде документа и образце."""

from __future__ import annotations

import os
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from path_resolver import (
    canonical_fs_path,
    is_allowed_sample_path,
    is_sniot_doc,
    live_user_agent_dir,
    resolve_document_path,
    save_last_used_path,
    user_agent_dir_unavailable_hint,
)
from rules import DOCUMENT_TYPES
from agent_core import detect_document, find_examples, choose_best_example, process_document, log, load_config
from learner import get_watcher, record_agent_action, learn_from_file_change
from remote_guard import get_guard
from formatters.sniot_document import pick_sniot_build_line


class AgentWizard(tk.Toplevel):
    def __init__(self, master=None, avatar=None):
        super().__init__(master)
        self.avatar = avatar
        try:
            self.agent_name = load_config().get("agent_name", "АГЕНТ Дубовика (№ 007)")
        except Exception:
            self.agent_name = "АГЕНТ Дубовика (№ 007)"
        self.title(f"{self.agent_name} — {pick_sniot_build_line()}")
        self.geometry("700x580")
        self.minsize(560, 480)
        self.configure(bg="#f3f7f6")

        self.file_path = tk.StringVar()
        self.doc_type = tk.StringVar(value="unsupported")
        self.example_path = tk.StringVar(value="")
        self.status = tk.StringVar(value="Выберите документ — я спрошу, как его оформить.")
        self.apply_text_edits = tk.BooleanVar(value=False)
        self.apply_russian_check = tk.BooleanVar(value=True)
        self.structure_rebuild = tk.BooleanVar(value=False)
        self.learn_enabled = tk.BooleanVar(value=True)
        self.guard_enabled = tk.BooleanVar(value=True)

        self._build()
        self.protocol("WM_DELETE_WINDOW", self.withdraw)
        # после построения гарантируем видимость кнопок
        self.after(100, self._ensure_buttons_visible)
        self.after(1000, self._tick_learn_status)

    def _ensure_buttons_visible(self):
        try:
            self.update_idletasks()
            # если окно слишком низкое — чуть поднимем высоту
            h = self.winfo_height()
            if h < 420:
                self.geometry(f"{max(self.winfo_width(), 560)}x480")
            self.bottom_bar.lift()
        except tk.TclError:
            pass

    def _build(self):
        # --- НИЖНЯЯ ПАНЕЛЬ СНАЧАЛА (всегда видна) ---
        self.bottom_bar = tk.Frame(self, bg="#e7efee", bd=1, relief="solid")
        self.bottom_bar.pack(side="bottom", fill="x")

        btn_row = tk.Frame(self.bottom_bar, bg="#e7efee")
        btn_row.pack(fill="x", padx=10, pady=(8, 4))

        self.btn_run = tk.Button(
            btn_row,
            text="Оформить документ",
            command=self._run,
            bg="#1a4d4a",
            fg="white",
            activebackground="#2a6d68",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=6,
        )
        self.btn_run.pack(side="left")

        tk.Button(
            btn_row,
            text="Помощь Cursor…",
            command=self._ask_cursor_help,
            bg="#3d5a80",
            fg="white",
            activebackground="#4a6fa5",
            activeforeground="white",
            font=("Segoe UI", 10, "bold"),
            padx=12,
            pady=6,
        ).pack(side="left", padx=8)

        tk.Button(
            btn_row,
            text="Свернуть",
            command=self.withdraw,
            font=("Segoe UI", 10),
            padx=12,
            pady=6,
        ).pack(side="left", padx=8)

        self.status_label = tk.Label(
            self.bottom_bar,
            textvariable=self.status,
            bg="#e7efee",
            fg="#333",
            wraplength=660,
            justify="left",
            font=("Segoe UI", 8),
            anchor="w",
        )
        self.status_label.pack(fill="x", padx=10, pady=(0, 8))

        # --- ВЕРХ / СЕРЕДИНА ---
        top = tk.Frame(self, bg="#f3f7f6")
        top.pack(side="top", fill="both", expand=True)

        tk.Label(
            top,
            text="АГЕНТ Дубовика (№ 007)",
            font=("Segoe UI", 14, "bold"),
            bg="#f3f7f6",
            fg="#1a4d4a",
        ).pack(anchor="w", padx=12, pady=(8, 2))

        tk.Label(
            top,
            text="Только Инструкция по делопроизводству + ваши правки СНиОТ. "
                 "Интернет-шаблоны не использую.",
            justify="left",
            bg="#f3f7f6",
            fg="#345",
            font=("Segoe UI", 8),
        ).pack(anchor="w", padx=12, pady=(0, 2))

        tk.Label(
            top,
            text="Путь к файлу — в поле «1. Документ» (вставьте или «Обзор…») "
                 "или в таблице handoff (request_latest.json). В чат Cursor путь не нужен.",
            justify="left",
            bg="#f3f7f6",
            fg="#1a4d4a",
            font=("Segoe UI", 8),
            wraplength=660,
        ).pack(anchor="w", padx=12, pady=(0, 4))

        # 1. Файл
        fr = ttk.LabelFrame(top, text="1. Документ — путь к редактируемому файлу")
        fr.pack(fill="x", padx=12, pady=4)
        doc_entry = ttk.Entry(fr, textvariable=self.file_path)
        doc_entry.pack(side="left", fill="x", expand=True, padx=6, pady=6)
        doc_entry.bind("<FocusOut>", lambda _e: self._on_path_edited())
        doc_entry.bind("<Return>", lambda _e: self._on_path_edited())
        ttk.Button(fr, text="Обзор…", command=self._browse).pack(side="left", padx=6, pady=6)

        # 2. Тип
        fr2 = ttk.LabelFrame(top, text="2. Вид документа (агент определяет сам по названию и титулу)")
        fr2.pack(fill="x", padx=12, pady=4)
        self.type_box = ttk.Combobox(fr2, state="readonly")
        self.type_box["values"] = [
            f"{k} — {DOCUMENT_TYPES[k]['title']}" for k in DOCUMENT_TYPES
        ]
        self.type_box.pack(fill="x", padx=6, pady=6)
        self.type_box.bind("<<ComboboxSelected>>", self._on_type_change)

        self.type_notes = tk.Label(
            fr2, text="", justify="left", wraplength=640, fg="#1a4d4a", font=("Segoe UI", 8)
        )
        self.type_notes.pack(anchor="w", padx=6, pady=(0, 6))

        # 3. Пример
        fr3 = ttk.LabelFrame(
            top,
            text="3. Образец для редактирования — только папка Агент, в имени слово «образец»",
        )
        fr3.pack(fill="both", expand=True, padx=12, pady=4)

        self.example_list = tk.Listbox(fr3, height=5, font=("Segoe UI", 9), exportselection=False)
        self.example_list.pack(fill="both", expand=True, padx=6, pady=(6, 2))
        self.example_list.insert(
            tk.END,
            "Стандарт: Инструкция по делопроизводству 2025 (без файла-образца; не ОБМЕН/сеть)",
        )
        self._examples = [None]

        btns = ttk.Frame(fr3)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btns, text="Обновить примеры", command=self._reload_examples).pack(side="left")
        ttk.Button(btns, text="Указать свой образец…", command=self._pick_example).pack(
            side="left", padx=6
        )

        # 4. Текстовые правки по сравнению ваших файлов
        fr4 = ttk.LabelFrame(top, text="4. Правки текста (по сравнению ваших правок в РАССМОТРЕНИЕ)")
        fr4.pack(fill="x", padx=12, pady=4)
        tk.Checkbutton(
            fr4,
            text="Текстовые правки по сравнению файлов (для ДИ/РИ/положений выключено: "
                 "только оформление, слова исходника не менять)",
            variable=self.apply_text_edits,
            bg="#f3f7f6",
            activebackground="#f3f7f6",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=6, pady=(6, 2))
        tk.Checkbutton(
            fr4,
            text="Проверка русского языка (все документы; аббревиатуры ЛСиМ, СНиОТ, ТКП не менять)",
            variable=self.apply_russian_check,
            bg="#f3f7f6",
            activebackground="#f3f7f6",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=6, pady=(0, 2))
        tk.Checkbutton(
            fr4,
            text="Перестроить СОДЕРЖАНИЕ по структуре образца (для Положений: "
                 "как П.ЦЭМ 10-02-2023 — не только шрифты). Если не сможет сам — "
                 "передаст задание в Cursor",
            variable=self.structure_rebuild,
            bg="#f3f7f6",
            activebackground="#f3f7f6",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=6, pady=(0, 6))

        # 5. Постоянное обучение
        fr5 = ttk.LabelFrame(top, text="5. Постоянное обучение")
        fr5.pack(fill="x", padx=12, pady=4)
        tk.Checkbutton(
            fr5,
            text="Следить за моими правками документов и записывать повторяющиеся в правила",
            variable=self.learn_enabled,
            command=self._toggle_learning,
            bg="#f3f7f6",
            activebackground="#f3f7f6",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=6, pady=(6, 2))
        self.learn_status = tk.Label(
            fr5,
            text="Обучение: …",
            bg="#f3f7f6",
            fg="#1a4d4a",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.learn_status.pack(fill="x", padx=6, pady=(0, 6))

        # 6. Аварийная охрана доступа
        fr6 = ttk.LabelFrame(top, text="6. Аварийное оповещение (удалённый доступ / админ)")
        fr6.pack(fill="x", padx=12, pady=4)
        tk.Checkbutton(
            fr6,
            text="Следить за RDP/TeamViewer/AnyDesk и входом админа; тревога на рабочем столе + журнал",
            variable=self.guard_enabled,
            command=self._toggle_guard,
            bg="#f3f7f6",
            activebackground="#f3f7f6",
            font=("Segoe UI", 9),
            wraplength=640,
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=6, pady=(6, 2))
        self.guard_status = tk.Label(
            fr6,
            text="Охрана доступа: …",
            bg="#f3f7f6",
            fg="#8B0000",
            font=("Segoe UI", 8),
            anchor="w",
            justify="left",
            wraplength=640,
        )
        self.guard_status.pack(fill="x", padx=6, pady=(0, 6))

        self.bind("<Configure>", self._on_resize)
        self._reload_examples()

    def _toggle_learning(self):
        w = get_watcher()
        w.enabled = bool(self.learn_enabled.get())
        if w.enabled and (w._thread is None or not w._thread.is_alive()):
            w.start()
        self._tick_learn_status()
        record_agent_action("toggle_learning", {"enabled": w.enabled})

    def _toggle_guard(self):
        g = get_guard(ui_root=self.master)
        g.enabled = bool(self.guard_enabled.get())
        if g.enabled and (g._thread is None or not g._thread.is_alive()):
            g.start()
        self._tick_learn_status()
        record_agent_action("toggle_remote_guard", {"enabled": g.enabled})

    def _tick_learn_status(self):
        try:
            if not self.winfo_exists():
                return
            w = get_watcher()
            self.learn_enabled.set(bool(w.enabled))
            self.learn_status.configure(text=w.status_text())
            g = get_guard()
            self.guard_enabled.set(bool(g.enabled))
            self.guard_status.configure(text=g.status_text())
            self.after(3000, self._tick_learn_status)
        except tk.TclError:
            pass

    def _on_resize(self, _event=None):
        try:
            w = max(self.winfo_width() - 40, 300)
            self.status_label.configure(wraplength=w)
            self.type_notes.configure(wraplength=w)
        except tk.TclError:
            pass

    def open_with_optional_file(self, path: str | None = None):
        self.deiconify()
        self.lift()
        self.focus_force()
        self._ensure_buttons_visible()
        if path:
            self.file_path.set(str(canonical_fs_path(path)))
            self._analyze()
        elif not self.file_path.get().strip():
            self._auto_fill_path()
        else:
            self._on_path_edited(silent=True)
        record_agent_action("open_wizard", {"path": path or self.file_path.get().strip() or None})

    def _resolve_document_path_or_warn(self, *, for_cursor: bool = False) -> str | None:
        """Путь из поля «1. Документ» или handoff/последний/по умолчанию."""
        path = self.file_path.get().strip()
        if path and os.path.exists(path):
            return str(canonical_fs_path(path))
        found, source = resolve_document_path()
        if found:
            self.file_path.set(str(canonical_fs_path(found)))
            save_last_used_path(found)
            if not for_cursor:
                self._analyze()
            self.status.set(f"Путь ({source}):\n{found.name}")
            return str(canonical_fs_path(found))
        messagebox.showwarning(
            "Нужен файл",
            "Укажите путь в поле «1. Документ» (вставьте или «Обзор…»)\n"
            "или положите source_path в handoff\\request_latest.json (папка Агент).",
        )
        return None

    def _on_path_edited(self, silent: bool = False):
        """Пользователь указал путь в поле — не перезаписывать автоматически."""
        path = self.file_path.get().strip()
        if not path:
            if not silent:
                self.status.set(
                    "Укажите путь в поле «1. Документ» или в таблице handoff "
                    "(request_latest.json → source_path)."
                )
            return
        if not os.path.exists(path):
            if not silent:
                self.status.set(f"Файл не найден: {path}")
            return
        path = str(canonical_fs_path(path))
        self.file_path.set(path)
        save_last_used_path(path)
        self._analyze()

    def _auto_fill_path(self):
        """Подставить путь только из таблицы handoff, если поле пустое."""
        if self.file_path.get().strip():
            self._on_path_edited(silent=True)
            return
        found, source = resolve_document_path()
        if not found:
            self.status.set(
                "Укажите путь в поле «1. Документ» (вставьте или «Обзор…») "
                "или положите путь в handoff\\request_latest.json (source_path)."
            )
            return
        self.file_path.set(str(canonical_fs_path(found)))
        save_last_used_path(found)
        self._analyze()
        self.status.set(f"Путь из таблицы handoff ({source}):\n{found.name}")
        if self.avatar:
            self.avatar.say(f"Из таблицы: {found.name}")

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Выберите документ Word",
            filetypes=[
                ("Word и RTF", "*.docx *.doc *.rtf"),
                ("Word (новый)", "*.docx"),
                ("Word (старый .doc)", "*.doc"),
                ("RTF", "*.rtf"),
                ("Все файлы", "*.*"),
            ],
        )
        if path:
            self.file_path.set(str(canonical_fs_path(path)))
            self._analyze()
            save_last_used_path(path)

    def _analyze(self):
        path = self.file_path.get().strip()
        if not path or not os.path.exists(path):
            return
        self.status.set("Смотрю документ…")
        self.update_idletasks()
        try:
            dtype, preview = detect_document(path)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось прочитать файл:\n{e}")
            return
        self.doc_type.set(dtype)
        self._apply_conservative_gui_defaults(dtype)
        for i, key in enumerate(DOCUMENT_TYPES):
            if key == dtype:
                self.type_box.current(i)
                break
        self._show_type_notes(dtype)
        best = self._reload_examples(auto_select_for=path)
        title = DOCUMENT_TYPES[dtype]["title"]
        ex_name = os.path.basename(best["path"]) if best else "стандарт Инструкции 2025"
        self.status.set(
            f"Определено автоматически: {title}. Образец: {ex_name}.  |  "
            f"{preview[:100].replace(chr(10), ' ')}…"
        )
        if self.avatar:
            self.avatar.say(f"{title}. Образец выбран сам.")
        record_agent_action(
            "detect_type",
            {
                "path": path,
                "type": dtype,
                "auto_example": best.get("path") if best else None,
            },
        )

    def _apply_conservative_gui_defaults(self, dtype: str) -> None:
        if dtype in (
            "dolzhnostnaya_instrukciya",
            "rabochaya_instrukciya",
            "polozhenie",
            "instrukciya_ot",
        ):
            self.apply_text_edits.set(False)

    def _on_type_change(self, _event=None):
        val = self.type_box.get()
        key = val.split(" — ", 1)[0].strip()
        if key in DOCUMENT_TYPES:
            self.doc_type.set(key)
            self._apply_conservative_gui_defaults(key)
            self._show_type_notes(key)
            self._reload_examples(auto_select_for=self.file_path.get().strip() or None)

    def _show_type_notes(self, key: str):
        notes = DOCUMENT_TYPES.get(key, {}).get("notes", [])
        # короче, чтобы не выталкивать кнопки
        short = notes[:3]
        self.type_notes.configure(text="• " + "  • ".join(short))

    def _reload_examples(self, auto_select_for: str | None = None) -> dict | None:
        self.example_list.delete(0, tk.END)
        self.example_list.insert(
            tk.END,
            "Стандарт: Инструкция по делопроизводству 2025 (без файла-образца; не ОБМЕН/сеть)",
        )
        self._examples = [None]
        key = self.doc_type.get()
        if not isinstance(auto_select_for, str):
            auto_select_for = self.file_path.get().strip() or None
        try:
            items = find_examples(key, limit=100, source_path=auto_select_for)
        except Exception as e:
            log(f"find_examples error: {e}")
            items = []
        if not items and key != "ezhenedelnyy_itog":
            live = live_user_agent_dir()
            if live is None:
                hint = user_agent_dir_unavailable_hint()
            else:
                hint = (
                    "В папке Агент нет файлов *образец*.docx. "
                    "Оформление по Инструкции 2025; ОБМЕН не подставляется."
                )
            self.example_list.insert(tk.END, hint)
            self._examples.append(None)
            self.status.set(hint)
        for item in items:
            label = item.get("label") or f"{item['folder']}  |  {item['name']}"
            self.example_list.insert(tk.END, label)
            self._examples.append(item["path"])

        best = None
        select_idx = 0
        if auto_select_for and os.path.exists(auto_select_for):
            try:
                best = choose_best_example(key, auto_select_for, limit=20)
            except Exception as e:
                log(f"choose_best_example error: {e}")
                best = None
            if best:
                # подсветить выбранный образец в списке
                for i, p in enumerate(self._examples):
                    if p and os.path.normcase(os.path.abspath(p)) == os.path.normcase(
                        os.path.abspath(best["path"])
                    ):
                        select_idx = i
                        break
                else:
                    # если лучший не в урезанном списке — добавить первым после стандарта
                    self.example_list.insert(
                        1,
                        f"[АВТО] {best.get('label') or best['name']}",
                    )
                    self._examples.insert(1, best["path"])
                    select_idx = 1
        self.example_list.selection_clear(0, tk.END)
        self.example_list.selection_set(select_idx)
        self.example_list.see(select_idx)
        return best

    def _pick_example(self):
        live = live_user_agent_dir()
        initial = str(live) if live is not None else None
        path = filedialog.askopenfilename(
            title="Образец — только папка Агент, в имени слово «образец»",
            initialdir=initial,
            filetypes=[
                ("Word DOCX", "*.docx"),
                ("Все файлы", "*.*"),
            ],
        )
        if not path:
            return
        if not is_allowed_sample_path(path):
            messagebox.showwarning(
                "Образец не подходит",
                "Образец можно брать только из папки Агент "
                "и только если в названии файла есть слово «образец».\n\n"
                "Файлы из ОБМЕН и файлы «_оформлен» без слова «образец» не используются.",
            )
            return
        self.example_list.insert(
            tk.END,
            f"[Агент / образец] {os.path.basename(path)}",
        )
        self._examples.append(path)
        self.example_list.selection_clear(0, tk.END)
        self.example_list.selection_set(tk.END)

    def _run(self):
        path = self._resolve_document_path_or_warn()
        if not path:
            return
        dtype = self.doc_type.get()
        if dtype not in DOCUMENT_TYPES:
            dtype = "unsupported"

        # выделен пункт 0 «Стандарт» — не подменять чужим *_образец (напр. старший мастер)
        sel = self.example_list.curselection()
        example = None
        user_chose_standard = False
        if sel:
            example = self._examples[sel[0]]
            user_chose_standard = sel[0] == 0 and not example
        if example and not is_allowed_sample_path(example):
            example = None
        if example is None and dtype != "unsupported" and not user_chose_standard:
            best = choose_best_example(dtype, path)
            if best:
                example = best["path"]

        do_edits = bool(self.apply_text_edits.get())
        do_ru = bool(self.apply_russian_check.get())
        do_struct = bool(self.structure_rebuild.get())
        msg = (
            f"Документ:\n{path}\n\n"
            f"Вид (определён сам): {DOCUMENT_TYPES[dtype]['title']}\n"
            f"Образец (выбран сам): {os.path.basename(example) if example else 'Инструкция 2025 (стандарт)'}\n"
            f"Текстовые правки/удаления: {'ДА' if do_edits else 'нет'}\n"
            f"Проверка русского языка: {'ДА' if do_ru else 'нет'}\n"
            f"Перестройка содержания по образцу: {'ДА' if do_struct else 'нет'}\n\n"
            "Можно сменить вид/образец в списках выше.\nПродолжить оформление?"
        )
        if not messagebox.askyesno("Подтвердите", msg):
            return

        self.status.set("Оформляю… подождите.")
        self.btn_run.configure(state="disabled")
        if self.avatar:
            self.avatar.say("Оформляю документ…")

        def worker():
            try:
                result = process_document(
                    path,
                    dtype,
                    example_path=example,
                    apply_text_edits_flag=do_edits,
                    apply_russian_check_flag=do_ru,
                    structure_rebuild_flag=do_struct,
                    auto_pick_example=not user_chose_standard,
                )
                record_agent_action(
                    "process_document",
                    {
                        "path": path,
                        "type": dtype,
                        "example": example,
                        "text_edits": do_edits,
                        "structure_rebuild": do_struct,
                        "output": result.get("output"),
                    },
                )
                # после оформления — тоже снимок для обучения
                try:
                    if result.get("output") and os.path.exists(result["output"]):
                        learn_from_file_change(result["output"])
                except Exception:
                    pass
                self.after(0, lambda r=result: self._done_ok(r))
            except Exception as e:
                # Важно: e=e в lambda — иначе Python 3 обнуляет e после except → «Ошибка None»
                err = e
                tb = traceback.format_exc()
                self.after(0, lambda err=err, tb=tb: self._done_err(err, tb))

        threading.Thread(target=worker, daemon=True).start()

    def _ask_cursor_help(self):
        """Передать сложную задачу (перестройка по образцу) в Cursor."""
        path = self._resolve_document_path_or_warn(for_cursor=True)
        if not path:
            return
        dtype = self.doc_type.get() if self.doc_type.get() in DOCUMENT_TYPES else "unsupported"
        sel = self.example_list.curselection()
        example = self._examples[sel[0]] if sel else None
        if example and not is_allowed_sample_path(example):
            example = None
        if example is None and dtype != "unsupported":
            user_std = bool(sel) and sel[0] == 0
            if not user_std:
                best = choose_best_example(dtype, path)
                if best:
                    example = best["path"]

        try:
            from formatters.ai_handoff import (
                needs_cursor_assist,
                open_cursor_with_task,
                write_cursor_task,
            )

            need, why = needs_cursor_assist(
                source_path=path, sample_path=example, doc_type=dtype
            )
            goal = (
                "Перестроить / исправить документ по структуре образца. "
                + (f"Причина: {why}." if why else "")
                + " Сохранить новым .docx."
            )
            if not messagebox.askyesno(
                "Помощь Cursor",
                "Делопроизводитель умеет оформлять (шрифты, поля, нумерацию),\n"
                "но НЕ переписывает смысл и структуру как человек-юрист.\n\n"
                "Сейчас задание будет передано в Cursor (чат с ИИ-помощником).\n\n"
                f"Документ: {os.path.basename(path)}\n"
                f"Образец: {os.path.basename(example) if example else '—'}\n"
                f"{'Замечено: ' + why if need else ''}\n\n"
                "Продолжить?",
            ):
                return
            prompt = write_cursor_task(
                source_path=path,
                sample_path=example,
                doc_type=dtype,
                goal=goal,
            )
            ok, msg = open_cursor_with_task(prompt)
            self.status.set("Задание передано в Cursor.")
            if self.avatar:
                self.avatar.say("Передал задачу в Cursor!")
            messagebox.showinfo(
                "Cursor — задание записано",
                msg + "\n\nФайл JSON:\n"
                + str(Path(__file__).resolve().parent / "handoff" / "request_latest.json"),
            )
        except Exception as e:
            messagebox.showerror("Ошибка", str(e))

    def _done_ok(self, result: dict):
        self.btn_run.configure(state="normal")
        out = result.get("output") or ""
        sniot = result.get("sniot_pass") or {}
        build = pick_sniot_build_line(result)
        if sniot.get("ok") and sniot.get("applied"):
            sniot_status = "Правила СНиОТ: применены (отступы, символы, нумерация)."
        elif sniot.get("applied"):
            sniot_status = "Правила СНиОТ: применены с замечаниями — см. список ниже."
        elif sniot:
            sniot_status = (
                "⚠ Правила СНиОТ НЕ применены! Закройте файл в Word и повторите."
            )
        else:
            sniot_status = "Правила СНиОТ: проход не выполнялся."
        sniot_status = f"{sniot_status}\n{build}"

        actions = result.get("actions") or []
        sniot_lines = []
        for action in actions:
            if action == build:
                continue
            if "СНиОТ" not in action:
                continue
            if action.startswith("СНиОТ !"):
                continue
            if "Validation OK" in action or "Сохранено:" in action or "исправлено замечаний" in action:
                sniot_lines.append(action)
            elif action.startswith("СНиОТ:") and "⛔" not in action:
                sniot_lines.append(action)
        sniot_lines = sniot_lines[:6]
        extra = "\n".join(f"• {a}" for a in sniot_lines) if sniot_lines else ""

        if sniot and not sniot.get("applied"):
            self.status.set("Правила СНиОТ не применены.")
            if self.avatar:
                self.avatar.say("Правила не записаны. Закройте Word.")
            fail_msg = (
                f"{build}\n\n"
                f"Правила СНиОТ НЕ записаны в файл.\n{out}\n\n"
                f"{sniot_status}\n"
                "Это не «Готово». Закройте все окна агента и Word с этим документом,\n"
                "запустите start_agent.bat и нажмите «Оформить документ» снова.\n\n"
                "Скрипт: C:\\Users\\v.dubovik\\AttestationSync\\fix_sniot_document.py"
            )
            if extra:
                fail_msg += f"\n\n{extra}"
            messagebox.showerror(f"Правила СНиОТ не применены — {build}", fail_msg)
            return

        self.status.set(f"Готово: {out}")
        if self.avatar:
            self.avatar.say("Готово! Откройте _оформлен.docx")
        msg = (
            f"{build}\n\n"
            f"Документ оформлен:\n{out}\n\n"
            "Открывайте файл с окончанием «_оформлен.docx» "
            "(не черновик без суффикса).\n\n"
            f"{sniot_status}\n"
            "Скрипт: C:\\Users\\v.dubovik\\AttestationSync\\fix_sniot_document.py"
        )
        if extra:
            msg += f"\n\n{extra}"
        if messagebox.askyesno(f"Готово — {build}", msg + "\n\nОткрыть папку?"):
            folder = os.path.dirname(out or self.file_path.get())
            os.startfile(folder)

    def _done_err(self, err: BaseException | None, tb: str = ""):
        self.btn_run.configure(state="normal")
        if err is None:
            text = "Неизвестная ошибка оформления.\n\n" + (tb or "Подробности в logs\\agent.log")
        else:
            text = str(err).strip() or repr(err)
            if not text or text.lower() == "none":
                text = f"{type(err).__name__}: сбой оформления.\n\n{tb or ''}".strip()
        log(f"ERROR {text}")
        if tb:
            log(tb)
        self.status.set(f"Ошибка: {text[:120]}")
        if self.avatar:
            self.avatar.say("Ошибка. Смотрите сообщение.")
        messagebox.showerror("Ошибка оформления", text)
