# -*- coding: utf-8 -*-
"""
Аварийный контроль: удалённый доступ и вход администратора.

Что делает:
1) Следит за сессиями RDP / удалёнными инструментами (TeamViewer, AnyDesk и т.п.)
2) При обнаружении — яркое окно на рабочем столе (аварийное оповещение)
3) Пишет все события и действия в файл на Рабочем столе с актуальной датой:
   АВАРИЯ_удалённый_доступ_ГГГГ-ММ-ДД.txt

Это защита вашего ПК: только наблюдение и журнал, без вмешательства в чужие системы.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
GUARD_LOG = ROOT / "logs" / "remote_guard.log"
STATE_PATH = ROOT / "learning" / "remote_guard_state.json"

# процессы типичных средств удалённого доступа (имена без .exe)
REMOTE_TOOL_NAMES = {
    "teamviewer",
    "teamviewer_service",
    "tv_w32",
    "tv_x64",
    "anydesk",
    "rustdesk",
    "rustdesk_service",
    "vncserver",
    "winvnc",
    "tvnserver",
    "ultravnc",
    "radmin",
    "radmin.exe",
    "amservice",  # AnyDesk helper
    "splashtop",
    "srserver",
    "dwagent",
    "dwservice",
    "chrome_remote_desktop_host",
    "remotedesktop",
    "msrdc",  # Microsoft Remote Desktop client — если админ с вашей машины
    "quickassist",
    "remotepasswordreset",
}

POLL_SEC = 4


def _log(msg: str) -> None:
    (ROOT / "logs").mkdir(exist_ok=True)
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {msg}\n"
    with open(GUARD_LOG, "a", encoding="utf-8") as f:
        f.write(line)


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def desktop_dir() -> Path:
    return Path.home() / "Desktop"


def daily_alert_path() -> Path:
    day = datetime.now().strftime("%Y-%m-%d")
    return desktop_dir() / f"АВАРИЯ_удалённый_доступ_{day}.txt"


def append_daily(comment: str, details: dict | None = None) -> Path:
    """Дописать событие в файл на рабочем столе с сегодняшней датой."""
    path = daily_alert_path()
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    block = [f"{'=' * 60}", f"ВРЕМЯ: {ts}", f"КОММЕНТАРИЙ: {comment}"]
    if details:
        for k, v in details.items():
            if v is None or v == "" or v == []:
                continue
            if isinstance(v, (list, tuple)):
                block.append(f"  {k}:")
                for item in v:
                    block.append(f"    - {item}")
            else:
                block.append(f"  {k}: {v}")
    block.append("")
    text = "\n".join(block)
    path.parent.mkdir(parents=True, exist_ok=True)
    need_header = (not path.exists()) or path.stat().st_size == 0
    with open(path, "a", encoding="utf-8") as f:
        if need_header:
            f.write(
                "ЖУРНАЛ АВАРИЙНОГО КОНТРОЛЯ — удалённый доступ / администратор\n"
                f"Компьютер: {os.environ.get('COMPUTERNAME', '?')}\n"
                f"Пользователь сеанса: {os.environ.get('USERNAME', '?')}\n"
                "АГЕНТ Дубовика (№ 007) записывает сюда все обнаруженные действия.\n\n"
            )
        f.write(text + "\n")
    _log(f"daily write: {comment}")
    return path


def _run_cmd(args: list[str], timeout: int = 8) -> str:
    try:
        # не показываем окно консоли
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        r = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="oem",
            errors="replace",
            timeout=timeout,
            startupinfo=startup,
        )
        return (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception as e:
        return f"[cmd error] {e}"


def query_sessions() -> list[dict]:
    """Сессии Windows (qwinsta) — Active/Disc, RDP и др."""
    out = _run_cmd(["qwinsta"])
    sessions = []
    for line in out.splitlines():
        raw = line.rstrip()
        if not raw.strip() or "SESSIONNAME" in raw.upper() or "ИМЯ СЕАНСА" in raw.upper():
            continue
        # формат: sessionname username id state ...
        parts = raw.split()
        if len(parts) < 3:
            continue
        # первая колонка может быть с пробелом в начале (активная *)
        mark = "*" if "*" in raw[:2] or raw.lstrip().startswith("*") else ""
        clean = raw.replace("*", " ").split()
        if len(clean) < 3:
            continue
        # варианты: console user 1 Active  OR  rdp-tcp#1 admin 2 Active
        session_name = clean[0]
        # если второй токен — число, username пустой
        if clean[1].isdigit():
            username = ""
            sid = clean[1]
            state = clean[2] if len(clean) > 2 else ""
        else:
            username = clean[1]
            sid = clean[2] if len(clean) > 2 else ""
            state = clean[3] if len(clean) > 3 else ""
        sessions.append(
            {
                "session": session_name,
                "user": username,
                "id": sid,
                "state": state,
                "current": bool(mark),
                "raw": raw.strip(),
            }
        )
    return sessions


def is_remote_session(s: dict) -> bool:
    name = (s.get("session") or "").lower()
    if name.startswith("rdp") or "rdp-tcp" in name:
        return True
    if "ica" in name:  # Citrix
        return True
    return False


def list_remote_tool_processes() -> list[dict]:
    """Запущенные процессы средств удалённого доступа."""
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-Process | Select-Object Id,ProcessName,StartTime,Path |"
                " ConvertTo-Json -Compress"
            ),
        ],
        timeout=15,
    )
    found = []
    try:
        data = json.loads(out.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        for p in data:
            pname = str(p.get("ProcessName") or "").lower()
            if pname in REMOTE_TOOL_NAMES or any(t in pname for t in REMOTE_TOOL_NAMES):
                found.append(
                    {
                        "pid": p.get("Id"),
                        "name": p.get("ProcessName"),
                        "path": p.get("Path"),
                        "start": str(p.get("StartTime") or ""),
                    }
                )
    except Exception:
        # запасной путь через tasklist
        tl = _run_cmd(["tasklist", "/FO", "CSV", "/NH"])
        for line in tl.splitlines():
            low = line.lower()
            for t in REMOTE_TOOL_NAMES:
                if t in low:
                    found.append({"name": t, "raw": line.strip()})
                    break
    return found


def list_admin_users_logged() -> list[str]:
    """Локальные администраторы, у кого есть сеанс."""
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "$admins = @(); "
                "try { $admins = (Get-LocalGroupMember -Group 'Administrators' -ErrorAction Stop | "
                "ForEach-Object { $_.Name }) } catch { "
                "$admins = (net localgroup Administrators) }; "
                "$sessions = (quser 2>$null); "
                "$result = @{ admins = @($admins); sessions = $sessions }; "
                "$result | ConvertTo-Json -Compress"
            ),
        ],
        timeout=12,
    )
    admins: list[str] = []
    try:
        data = json.loads(out.strip() or "{}")
        raw_admins = data.get("admins") or []
        if isinstance(raw_admins, str):
            raw_admins = [raw_admins]
        # также разбор net localgroup строк
        names = []
        for a in raw_admins:
            s = str(a).strip()
            if s and "The command completed" not in s and "---" not in s and "Alias" not in s:
                if not s.lower().startswith("members") and "комментар" not in s.lower():
                    names.append(s)
        sessions = query_sessions()
        session_users = {(s.get("user") or "").lower() for s in sessions if s.get("user")}
        for n in names:
            short = n.split("\\")[-1].lower()
            if short in session_users or n.lower() in session_users:
                admins.append(n)
        # если текущий пользователь — админ и есть RDP
        me = os.environ.get("USERNAME", "").lower()
        for n in names:
            if n.split("\\")[-1].lower() == me:
                # отметим только если есть удалённая сессия
                if any(is_remote_session(s) and (s.get("state") or "").lower().startswith("act") for s in sessions):
                    if n not in admins:
                        admins.append(n + " (текущий + RDP)")
    except Exception as e:
        _log(f"admin check fail: {e}")
    return admins


def snapshot_processes() -> set[tuple[int, str]]:
    out = _run_cmd(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-Process | ForEach-Object { \"$($_.Id)|$($_.ProcessName)\" }",
        ],
        timeout=12,
    )
    s: set[tuple[int, str]] = set()
    for line in out.splitlines():
        line = line.strip()
        if "|" not in line:
            continue
        pid_s, name = line.split("|", 1)
        try:
            s.add((int(pid_s), name))
        except ValueError:
            continue
    return s


def scan_once() -> dict:
    sessions = query_sessions()
    remote_sessions = [s for s in sessions if is_remote_session(s)]
    tools = list_remote_tool_processes()
    admins = list_admin_users_logged()
    # активный удалённый доступ: RDP Active/Disc или живой remote-tool
    live_rdp = []
    for s in remote_sessions:
        st = (s.get("state") or "").lower()
        if "listen" in st or "слуша" in st:
            continue
        live_rdp.append(s)
    remote_active = bool(live_rdp) or bool(tools)
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "sessions": sessions,
        "remote_sessions": live_rdp,
        "remote_tools": tools,
        "admin_users": admins,
        "remote_active": remote_active,
        "computer": os.environ.get("COMPUTERNAME"),
        "local_user": os.environ.get("USERNAME"),
    }


DESKTOP_BANNER = "Мой компьютер не женская баня!!! Прошу не подсматривать"


class AlertWindow:
    """Яркое аварийное окно поверх всех окон — крупный текст на весь экран."""

    def __init__(self, title: str, message: str, log_path: str):
        self.title = title
        self.message = message
        self.log_path = log_path
        self.root: tk.Toplevel | tk.Tk | None = None

    def show(self, master: tk.Misc | None = None) -> None:
        def _ui():
            if master is not None:
                win = tk.Toplevel(master)
            else:
                win = tk.Tk()
            self.root = win
            win.title(self.title)
            win.attributes("-topmost", True)
            win.configure(bg="#8B0000")
            # почти на весь экран — чтобы фраза была видна удалённому гостю
            try:
                sw = win.winfo_screenwidth()
                sh = win.winfo_screenheight()
                win.geometry(f"{sw}x{sh}+0+0")
                win.state("zoomed")
            except tk.TclError:
                win.geometry("900x600+40+40")
            win.resizable(True, True)

            tk.Label(
                win,
                text="⚠ АВАРИЙНОЕ ОПОВЕЩЕНИЕ — УДАЛЁННЫЙ / АДМИН ДОСТУП",
                bg="#8B0000",
                fg="white",
                font=("Segoe UI", 18, "bold"),
            ).pack(pady=(24, 12))

            # ГЛАВНАЯ ФРАЗА — крупно, по центру экрана
            banner = tk.Label(
                win,
                text=DESKTOP_BANNER,
                bg="#8B0000",
                fg="#FFFF00",
                font=("Segoe UI", 42, "bold"),
                wraplength=max(win.winfo_screenwidth() - 80, 600),
                justify="center",
            )
            banner.pack(expand=True, fill="both", padx=40, pady=20)

            tk.Label(
                win,
                text=self.message,
                bg="#8B0000",
                fg="#FFE4E1",
                font=("Segoe UI", 12),
                wraplength=max(win.winfo_screenwidth() - 100, 500),
                justify="center",
            ).pack(padx=24, pady=8)
            tk.Label(
                win,
                text=f"Журнал на рабочем столе:\n{self.log_path}",
                bg="#8B0000",
                fg="#FFD700",
                font=("Segoe UI", 10),
                wraplength=max(win.winfo_screenwidth() - 100, 500),
                justify="center",
            ).pack(padx=24, pady=4)
            tk.Button(
                win,
                text="Понял, закрыть окно",
                command=win.destroy,
                font=("Segoe UI", 14, "bold"),
                bg="white",
                fg="#8B0000",
                padx=20,
                pady=8,
            ).pack(pady=20)
            self._flash(win, banner, 0)
            if master is None:
                win.mainloop()

        if master is not None:
            try:
                master.after(0, _ui)
                return
            except Exception:
                pass
        threading.Thread(target=_ui, daemon=True).start()

    def _flash(self, win: tk.Misc, banner: tk.Label, n: int) -> None:
        if n > 40:
            return
        try:
            if not win.winfo_exists():
                return
            bg = ("#8B0000", "#CC0000")[n % 2]
            fg = ("#FFFF00", "#FFFFFF")[n % 2]
            win.configure(bg=bg)
            banner.configure(bg=bg, fg=fg)
            win.after(350, lambda: self._flash(win, banner, n + 1))
        except tk.TclError:
            pass


class RemoteAccessGuard:
    """Фоновый контроль удалённого доступа и действий админа."""

    def __init__(self, ui_root: tk.Misc | None = None):
        cfg = load_config()
        self.enabled = bool(cfg.get("remote_access_guard", True))
        self.ui_root = ui_root
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._alerted_keys: set[str] = set()
        self._armed = False  # режим сбора действий после обнаружения
        self._proc_snapshot: set[tuple[int, str]] = set()
        self._baseline_tool_pids: set[int] = set()
        self._baseline_ready = False
        self.last_event = "ожидание…"
        self.alerts_count = 0
        self._load_state()

    def _load_state(self) -> None:
        try:
            if STATE_PATH.exists():
                data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                self._alerted_keys = set(data.get("alerted_keys", []))
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            (ROOT / "learning").mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                json.dumps({"alerted_keys": list(self._alerted_keys)[-200:]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def status_text(self) -> str:
        state = "ВКЛ" if self.enabled and not self._stop.is_set() else "ВЫКЛ"
        mode = "СБОР ДЕЙСТВИЙ" if self._armed else "дежурство"
        return f"Охрана доступа: {state} | {mode} | тревог: {self.alerts_count} | {self.last_event}"

    def start(self) -> None:
        if not self.enabled:
            self.last_event = "выключено в config"
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="RemoteAccessGuard", daemon=True)
        self._thread.start()
        _log("RemoteAccessGuard started")
        self.last_event = "охрана запущена"

    def stop(self) -> None:
        self._stop.set()
        self.last_event = "охрана остановлена"
        _log("RemoteAccessGuard stop")

    def _key_for(self, kind: str, payload: str) -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        return f"{day}|{kind}|{payload}"

    def _raise_alert(self, title: str, comment: str, details: dict) -> None:
        details = dict(details or {})
        details["сообщение_на_экране"] = DESKTOP_BANNER
        path = append_daily(comment, details)
        append_daily(
            f"На экран выведено крупным шрифтом: «{DESKTOP_BANNER}»",
            {"тип_тревоги": title, "computer": details.get("computer")},
        )
        self.alerts_count += 1
        self.last_event = comment[:80]
        self._armed = True
        self._proc_snapshot = snapshot_processes()
        msg = (
            f"{comment}\n\n"
            f"Компьютер: {details.get('computer')}\n"
            f"Идёт запись всех действий в файл на рабочем столе."
        )
        AlertWindow(title, msg, str(path)).show(master=self.ui_root)
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            pass

    def _run(self) -> None:
        # базовый снимок: уже работающие remote-tools не считаем новой тревогой
        try:
            snap0 = scan_once()
            self._baseline_tool_pids = {
                int(t["pid"]) for t in snap0.get("remote_tools", []) if t.get("pid") is not None
            }
            self._baseline_ready = True
            _log(
                "baseline ready; preexisting tools="
                + str([(t.get("name"), t.get("pid")) for t in snap0.get("remote_tools", [])])
            )
            self.last_event = "базовый снимок готов"
        except Exception as e:
            _log(f"baseline fail: {e}")
            self._baseline_ready = True

        while not self._stop.is_set():
            if not self.enabled:
                time.sleep(2)
                continue
            try:
                self._tick()
            except Exception as e:
                _log(f"tick error: {e}")
            time.sleep(POLL_SEC)

    def _tick(self) -> None:
        snap = scan_once()
        # --- RDP ---
        for s in snap["remote_sessions"]:
            state = (s.get("state") or "").lower()
            if "listen" in state or "слуша" in state:
                continue
            # на русской Windows: Активно / Отключен / Диск
            key = self._key_for("rdp", f"{s.get('session')}|{s.get('user')}|{s.get('id')}|{state}")
            if key not in self._alerted_keys:
                self._alerted_keys.add(key)
                self._save_state()
                self._raise_alert(
                    "УДАЛЁННЫЙ ДОСТУП (RDP)",
                    f"Обнаружена удалённая сессия RDP: {s.get('session')} пользователь={s.get('user') or 'неизвестно'} состояние={s.get('state')}",
                    {
                        "тип": "RDP / Terminal Services",
                        "сеанс": s.get("session"),
                        "пользователь": s.get("user"),
                        "id_сеанса": s.get("id"),
                        "состояние": s.get("state"),
                        "строка": s.get("raw"),
                        "computer": snap["computer"],
                        "local_user": snap["local_user"],
                        "все_сеансы": [x.get("raw") for x in snap["sessions"]],
                    },
                )

        # --- remote tools (только НОВЫЕ процессы после старта агента) ---
        for t in snap["remote_tools"]:
            pid = t.get("pid")
            try:
                pid_i = int(pid) if pid is not None else -1
            except (TypeError, ValueError):
                pid_i = -1
            if self._baseline_ready and pid_i in self._baseline_tool_pids:
                continue
            key = self._key_for("tool", f"{t.get('name')}|{pid}")
            if key not in self._alerted_keys:
                self._alerted_keys.add(key)
                self._save_state()
                if pid_i > 0:
                    self._baseline_tool_pids.add(pid_i)
                self._raise_alert(
                    "УДАЛЁННЫЙ ДОСТУП (программа)",
                    f"Запущено средство удалённого доступа: {t.get('name')} (PID {t.get('pid')})",
                    {
                        "тип": "Remote tool process",
                        "процесс": t.get("name"),
                        "pid": t.get("pid"),
                        "путь": t.get("path"),
                        "старт": t.get("start"),
                        "computer": snap["computer"],
                        "local_user": snap["local_user"],
                    },
                )

        # --- admin in session while remote ---
        if snap["admin_users"] and snap["remote_active"]:
            for a in snap["admin_users"]:
                key = self._key_for("admin", a)
                if key not in self._alerted_keys:
                    self._alerted_keys.add(key)
                    self._save_state()
                    self._raise_alert(
                        "АДМИНИСТРАТОР + УДАЛЁННЫЙ ДОСТУП",
                        f"Администратор в сеансе при удалённом доступе: {a}",
                        {
                            "тип": "Admin + remote",
                            "админ": a,
                            "remote_sessions": [x.get("raw") for x in snap["remote_sessions"]],
                            "tools": [f"{x.get('name')} pid={x.get('pid')}" for x in snap["remote_tools"]],
                            "computer": snap["computer"],
                        },
                    )

        # --- сбор новых процессов в режиме «вооружён» ---
        if self._armed and snap["remote_active"]:
            now = snapshot_processes()
            new_procs = now - self._proc_snapshot
            # фильтр шума
            ignore = {
                "idle",
                "system",
                "registry",
                "memory compression",
                "fontdrvhost",
                "csrss",
                "smss",
                "svchost",
                "runtimebroker",
                "searchhost",
                "shellexperiencehost",
                "startmenuexperiencehost",
                "textinputhost",
                "conhost",
                "dllhost",
                "sihost",
                "taskhostw",
                "ctfmon",
                "dwm",
                "python",
                "pythonw",
            }
            interesting = []
            for pid, name in sorted(new_procs, key=lambda x: x[0]):
                if name.lower() in ignore:
                    continue
                interesting.append(f"{name} (PID {pid})")
            if interesting:
                append_daily(
                    "Действие во время удалённого доступа: запущены новые процессы",
                    {
                        "комментарий": "Появились процессы после обнаружения удалённого/админ доступа",
                        "новые_процессы": interesting[:40],
                        "computer": snap["computer"],
                        "local_user": snap["local_user"],
                        "remote_sessions": [x.get("raw") for x in snap["remote_sessions"]],
                    },
                )
                self.last_event = f"новые процессы: {len(interesting)}"
            # изменения файлов на рабочем столе / в загрузках
            self._log_recent_file_activity(snap)
            self._proc_snapshot = now
        elif self._armed and not snap["remote_active"]:
            append_daily(
                "Удалённый доступ завершён (сеансы/инструменты не видны)",
                {"computer": snap["computer"], "local_user": snap["local_user"]},
            )
            self._armed = False
            self.last_event = "удалённый доступ завершён"

    def _log_recent_file_activity(self, snap: dict) -> None:
        """Файлы, изменённые за последние POLL*2 секунд на Desktop/Downloads."""
        if not hasattr(self, "_last_file_scan"):
            self._last_file_scan = 0.0
            self._seen_file_mtimes: dict[str, float] = {}
        now = time.time()
        if now - self._last_file_scan < POLL_SEC:
            return
        self._last_file_scan = now
        roots = [desktop_dir(), Path.home() / "Downloads"]
        changed = []
        for root in roots:
            if not root.is_dir():
                continue
            try:
                for p in root.iterdir():
                    if not p.is_file():
                        continue
                    if p.name.startswith("АВАРИЯ_удалённый_доступ_"):
                        continue
                    if p.name.startswith("~$"):
                        continue
                    try:
                        mt = p.stat().st_mtime
                    except OSError:
                        continue
                    key = str(p)
                    prev = self._seen_file_mtimes.get(key)
                    self._seen_file_mtimes[key] = mt
                    if prev is None:
                        continue
                    if mt > prev:
                        changed.append(f"{p.name} (папка: {root.name})")
            except OSError:
                continue
        if changed:
            append_daily(
                "Действие во время удалённого доступа: изменены/сохранены файлы",
                {
                    "комментарий": "Файлы на Рабочем столе или в Загрузках изменены в период удалённого доступа",
                    "файлы": changed[:30],
                    "computer": snap.get("computer"),
                    "local_user": snap.get("local_user"),
                },
            )
            self.last_event = f"файлы: {len(changed)}"


_guard: RemoteAccessGuard | None = None


def get_guard(ui_root: tk.Misc | None = None) -> RemoteAccessGuard:
    global _guard
    if _guard is None:
        _guard = RemoteAccessGuard(ui_root=ui_root)
    elif ui_root is not None:
        _guard.ui_root = ui_root
    return _guard
