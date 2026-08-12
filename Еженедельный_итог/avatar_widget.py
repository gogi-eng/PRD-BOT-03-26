# -*- coding: utf-8 -*-
"""Плавающий аватар «Еженедельный итог» на рабочем столе. Клик — запуск отчёта."""

from __future__ import annotations

import math
import subprocess
import sys
import tkinter as tk
from pathlib import Path

from PIL import Image, ImageEnhance, ImageTk

ROOT = Path(__file__).resolve().parent
AVATAR = ROOT / "avatar" / "аватар.png"
BAT = ROOT / "Сделать еженедельный итог.bat"
PY = Path(r"C:\Users\v.dubovik\AppData\Local\Programs\Python\Python311\python.exe")
SCRIPT = ROOT / "weekly_report.py"

# Крупный заметный аватар
DEFAULT_SIZE = 200


def _prepare_avatar_image(path: Path, size: int) -> Image.Image:
    img = Image.open(path).convert("RGBA")
    rgb = Image.new("RGB", img.size, (255, 255, 255))
    rgb.paste(img, mask=img.split()[-1])
    rgb = ImageEnhance.Brightness(rgb).enhance(1.22)
    rgb = ImageEnhance.Color(rgb).enhance(1.30)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.15)
    out = Image.new("RGBA", img.size)
    out.paste(rgb, (0, 0))
    out.putalpha(img.split()[-1])
    return out.resize((size, size), Image.Resampling.LANCZOS)


class WeeklyAvatar:
    def __init__(self, size: int = DEFAULT_SIZE):
        self.size = size
        self.root = tk.Tk()
        self.root.title("Еженедельный итог")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        try:
            self.root.wm_attributes("-transparentcolor", "magenta")
        except tk.TclError:
            pass
        self.root.configure(bg="magenta")

        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        win_w = size + 36
        win_h = size + 64
        # чуть левее/ниже делопроизводителя, чтобы не перекрывались
        self.root.geometry(f"{win_w}x{win_h}+{sw - win_w - 28}+{max(40, sh // 2)}")

        self.canvas = tk.Canvas(
            self.root,
            width=win_w,
            height=win_h,
            bg="magenta",
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack(fill="both", expand=True)

        self.base_img = _prepare_avatar_image(AVATAR, size)
        self.tk_img = ImageTk.PhotoImage(self.base_img)
        self.img_id = self.canvas.create_image(
            win_w // 2, size // 2 + 10, image=self.tk_img
        )
        self.bubble = self.canvas.create_text(
            win_w // 2,
            size + 38,
            text="Клик — итог недели",
            fill="#063d3a",
            font=("Segoe UI", 11, "bold"),
            width=win_w - 8,
        )

        self._tick = 0
        self._drag = None
        self._moved = False
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Button-3>", self._menu)
        self._animate()

    def _menu(self, event):
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="Сделать еженедельный итог", command=self.run_report)
        menu.add_command(label="Открыть папку скрипта", command=self.open_folder)
        menu.add_separator()
        menu.add_command(label="Скрыть аватар", command=self.root.destroy)
        menu.tk_popup(event.x_root, event.y_root)

    def open_folder(self):
        subprocess.Popen(["explorer", str(ROOT)])

    def run_report(self):
        py = str(PY if PY.exists() else sys.executable)
        subprocess.Popen([py, str(SCRIPT)], cwd=str(ROOT))

    def _on_press(self, event):
        self._drag = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())
        self._moved = False

    def _on_drag(self, event):
        if not self._drag:
            return
        x0, y0, wx, wy = self._drag
        dx, dy = event.x_root - x0, event.y_root - y0
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        self.root.geometry(f"+{wx + dx}+{wy + dy}")

    def _on_release(self, _event):
        if self._drag and not self._moved:
            self.run_report()
        self._drag = None

    def _animate(self):
        self._tick += 1
        bob = math.sin(self._tick / 12) * 5
        scale = 1.0 + 0.04 * math.sin(self._tick / 18)
        new_size = max(8, int(self.size * scale))
        frame = self.base_img.resize((new_size, new_size), Image.Resampling.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(frame)
        self.canvas.itemconfigure(self.img_id, image=self.tk_img)
        win_w = self.size + 36
        self.canvas.coords(self.img_id, win_w // 2, self.size // 2 + 10 + bob)
        if (self._tick // 40) % 2 == 0:
            self.canvas.itemconfigure(self.bubble, state="normal")
        else:
            self.canvas.itemconfigure(self.bubble, state="hidden")
        self.root.after(50, self._animate)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    if not AVATAR.exists():
        print("Нет файла аватара:", AVATAR)
        sys.exit(1)
    WeeklyAvatar().run()
