# -*- coding: utf-8 -*-

"""Анимированный аватар на рабочем столе (поверх окон, перетаскивание)."""



from __future__ import annotations



import math

import tkinter as tk

from pathlib import Path



from PIL import Image, ImageEnhance, ImageTk



ROOT = Path(__file__).resolve().parent

AVATAR_PATH = ROOT / "avatar" / "mascot.png"



# Крупный заметный аватар на рабочем столе

DEFAULT_SIZE = 200





def _prepare_avatar_image(path: Path, size: int) -> Image.Image:

    """Загрузить, усилить яркость/цвет и масштабировать."""

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





class FloatingAvatar:

    def __init__(self, on_click=None, size=DEFAULT_SIZE, title: str = "АГЕНТ Дубовика (№ 007)"):

        self.on_click = on_click

        self.size = size

        self.root = tk.Toplevel() if tk._default_root else tk.Tk()

        self.root.title(title)

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

        self.root.geometry(f"{win_w}x{win_h}+{sw - win_w - 28}+{max(40, sh // 4)}")



        self.canvas = tk.Canvas(

            self.root,

            width=win_w,

            height=win_h,

            bg="magenta",

            highlightthickness=0,

            bd=0,

        )

        self.canvas.pack(fill="both", expand=True)



        self.base_img = _prepare_avatar_image(AVATAR_PATH, size)

        self.tk_img = ImageTk.PhotoImage(self.base_img)

        self.img_id = self.canvas.create_image(

            win_w // 2, size // 2 + 10, image=self.tk_img

        )

        self.bubble = self.canvas.create_text(

            win_w // 2,

            size + 38,

            text="Нажмите — оформить документ",

            fill="#063d3a",

            font=("Segoe UI", 11, "bold"),

            width=win_w - 8,

        )



        self._tick = 0

        self._drag = None

        self.canvas.bind("<Button-1>", self._on_press)

        self.canvas.bind("<B1-Motion>", self._on_drag)

        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.root.bind("<Button-3>", lambda e: self._show_menu(e))

        self._animate()



    def _show_menu(self, event):

        menu = tk.Menu(self.root, tearoff=0)

        menu.add_command(label="Открыть агента", command=self._fire_click)

        menu.add_command(label="Скрыть аватар", command=self.hide)

        menu.add_separator()

        menu.add_command(label="Выход", command=self.quit_app)

        menu.tk_popup(event.x_root, event.y_root)



    def _fire_click(self):

        if self.on_click:

            self.on_click()



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



    def _on_release(self, event):

        if self._drag and not getattr(self, "_moved", False):

            self._fire_click()

        self._drag = None



    def _animate(self):

        self._tick += 1

        # лёгкое «дыхание» и покачивание

        bob = math.sin(self._tick / 12) * 5

        scale = 1.0 + 0.04 * math.sin(self._tick / 18)

        new_size = max(8, int(self.size * scale))

        frame = self.base_img.resize((new_size, new_size), Image.Resampling.LANCZOS)

        self.tk_img = ImageTk.PhotoImage(frame)

        self.canvas.itemconfigure(self.img_id, image=self.tk_img)

        win_w = self.size + 36

        self.canvas.coords(self.img_id, win_w // 2, self.size // 2 + 10 + bob)

        # мигание подсказки

        if (self._tick // 40) % 2 == 0:

            self.canvas.itemconfigure(self.bubble, state="normal")

        else:

            self.canvas.itemconfigure(self.bubble, state="hidden")

        self.root.after(50, self._animate)



    def say(self, text: str, seconds: float = 4.0):

        self.canvas.itemconfigure(self.bubble, text=text, state="normal")

        self.root.after(int(seconds * 1000), lambda: self.canvas.itemconfigure(

            self.bubble, text="Нажмите — оформить документ"

        ))



    def hide(self):

        self.root.withdraw()



    def show(self):

        self.root.deiconify()



    def quit_app(self):

        try:

            self.root.destroy()

        except tk.TclError:

            pass

        if tk._default_root:

            try:

                tk._default_root.destroy()

            except tk.TclError:

                pass

