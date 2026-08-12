# -*- coding: utf-8 -*-
"""
Точка входа локального агента «АГЕНТ Дубовика (№ 007)».

Запуск:
  start_agent.bat
  или: python start_agent.py
"""

from __future__ import annotations

import os
import sys
import tkinter as tk

# работаем из папки агента
ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from avatar_widget import FloatingAvatar
from agent_gui import AgentWizard
from agent_core import log, load_config
from learner import get_watcher
from remote_guard import get_guard


def main():
    log("Agent start")
    cfg = load_config()
    agent_name = cfg.get("agent_name", "АГЕНТ Дубовика (№ 007)")

    # постоянное обучение по вашим правкам документов
    watcher = get_watcher()
    watcher.start()
    log("Learning watcher started")

    root = tk.Tk()
    root.withdraw()  # главное окно скрыто — виден аватар
    root.title(agent_name)

    # аварийный контроль удалённого доступа / админа
    guard = get_guard(ui_root=root)
    guard.start()
    log("Remote access guard started")

    wizard_holder = {"w": None}

    def open_wizard():
        if wizard_holder["w"] is None or not wizard_holder["w"].winfo_exists():
            wizard_holder["w"] = AgentWizard(master=root, avatar=avatar)
        else:
            wizard_holder["w"].deiconify()
            wizard_holder["w"].lift()
        if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
            wizard_holder["w"].open_with_optional_file(sys.argv[1])
        else:
            wizard_holder["w"].open_with_optional_file(None)

    avatar = FloatingAvatar(
        on_click=open_wizard,
        size=200,
        title=agent_name,
    )
    avatar.say("АГЕНТ Дубовика № 007 на связи. Нажмите — оформить документ.")

    if len(sys.argv) > 1:
        root.after(400, open_wizard)

    try:
        root.mainloop()
    finally:
        guard.stop()
        watcher.stop()
        log("Agent stop")


if __name__ == "__main__":
    main()
