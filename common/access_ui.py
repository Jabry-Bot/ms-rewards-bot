"""
Pantalla del código de acceso (6 caracteres), compartida por el panel y el
instalador. Mismo lenguaje visual que el panel: colores de Bing.

    ask_code(master, on_ok=..., on_cancel=...)

Se muestra sobre `master` (que debe estar oculta). Si el código es válido llama
a on_ok(); si se cierra la ventana, a on_cancel().
"""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import customtkinter as ctk

try:
    from common import access
except ImportError:
    import access  # type: ignore

BG = "#16181d"
CARD = "#1d2027"
BORDER = "#3a3f4b"
TEXT = "#eef1f6"
MUTED = "#8e96a4"
GOLD = "#4aa8ff"
GOLD_HOVER = "#2a7cf0"
ON_GOLD = "#ffffff"
ERROR = "#ff8a80"

MAX_TRIES = 5
LOCK_SECONDS = 60


def _family(root, *names: str) -> str:
    try:
        have = set(tkfont.families(root))
    except Exception:
        have = set()
    return next((n for n in names if n in have), "Segoe UI")


class CodeDialog(ctk.CTkToplevel):
    def __init__(self, master, on_ok, on_cancel):
        super().__init__(master, fg_color=BG)
        self._on_ok, self._on_cancel = on_ok, on_cancel
        self._fails = 0
        self._locked_until = 0.0
        self._busy = False
        self.title("ms_rewards")
        self.resizable(False, False)
        # customtkinter pone su propio icono a los ~200 ms: poner el de la app después.
        try:
            ico = master.wm_iconbitmap()
            if ico:
                self.after(300, lambda: self.iconbitmap(ico))
        except Exception:
            pass
        disp = _family(self, "Segoe UI Variable Display", "Segoe UI")
        text = _family(self, "Segoe UI Variable Text", "Segoe UI")

        card = ctk.CTkFrame(self, fg_color=CARD, corner_radius=16,
                            border_width=1, border_color=BORDER)
        card.pack(padx=18, pady=18, fill="both", expand=True)

        self.status = ctk.CTkLabel(card, text="", text_color=MUTED,
                                   font=(text, 11), anchor="w", justify="left")
        self.status.pack(fill="x", padx=24, pady=(18, 14))

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(padx=24)
        self.boxes: list[ctk.CTkEntry] = []
        for i in range(access.CODE_LEN):
            e = ctk.CTkEntry(row, width=50, height=60, justify="center",
                             font=(disp, 26, "bold"), fg_color=BG, text_color=TEXT,
                             border_color=BORDER, border_width=2, corner_radius=10)
            e.grid(row=0, column=i, padx=(0 if i == 0 else 8, 0))
            e.bind("<KeyRelease>", lambda ev, i=i: self._on_key(ev, i))
            e.bind("<FocusIn>", lambda _e, w=e: w.configure(border_color=GOLD))
            e.bind("<FocusOut>", lambda _e, w=e: w.configure(border_color=BORDER))
            e.bind("<<Paste>>", lambda _e, i=i: self._on_paste(i))
            self.boxes.append(e)

        self.msg = ctk.CTkLabel(card, text="", text_color=ERROR, font=(text, 12), height=20)
        self.msg.pack(pady=(10, 2))

        self.btn = ctk.CTkButton(card, text="Activar", height=44, corner_radius=10,
                                 font=(text, 14, "bold"), fg_color="#1a6fe0",
                                 hover_color=GOLD_HOVER, text_color=ON_GOLD,
                                 command=self._submit)
        self.btn.pack(fill="x", padx=24, pady=(4, 22))

        self.bind("<Return>", lambda _e: self._submit())
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self._tick()
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 3
        self.geometry(f"+{x}+{y}")
        self.after(50, lambda: self.boxes[0].focus_set())

    # --- Campo de 6 casillas ---------------------------------------------
    def code(self) -> str:
        return "".join(access.normalize(b.get())[:1] for b in self.boxes)

    def _set(self, i: int, ch: str) -> None:
        self.boxes[i].delete(0, "end")
        if ch:
            self.boxes[i].insert(0, ch)

    def _on_key(self, ev, i: int) -> None:
        if ev.keysym in ("Return", "Tab", "Shift_L", "Shift_R"):
            return
        if ev.keysym == "BackSpace":
            if not self.boxes[i].get() and i > 0:
                self._set(i - 1, "")
                self.boxes[i - 1].focus_set()
            return
        if ev.keysym == "Left" and i > 0:
            self.boxes[i - 1].focus_set()
            return
        if ev.keysym == "Right" and i < len(self.boxes) - 1:
            self.boxes[i + 1].focus_set()
            return
        val = access.normalize(self.boxes[i].get())
        if len(val) > 1:          # escribió varios o pegó: repartir
            self._spread(i, val)
            return
        self._set(i, val)
        self.msg.configure(text="")
        if val and i < len(self.boxes) - 1:
            self.boxes[i + 1].focus_set()
        elif val and i == len(self.boxes) - 1:
            self._submit()

    def _on_paste(self, i: int) -> str:
        try:
            self._spread(i, access.normalize(self.clipboard_get()))
        except tk.TclError:
            pass
        return "break"

    def _spread(self, i: int, val: str) -> None:
        for j, ch in enumerate(val[: len(self.boxes) - i]):
            self._set(i + j, ch)
        nxt = min(i + len(val), len(self.boxes) - 1)
        self.boxes[nxt].focus_set()
        if len(self.code()) == len(self.boxes):
            self._submit()

    # --- Comprobación ------------------------------------------------------
    def _tick(self) -> None:
        self.status.configure(text=access.status_line())
        left = self._locked_until - time.monotonic()
        if left > 0:
            self.msg.configure(text=f"Demasiados intentos. Espera {int(left) + 1} s.")
        elif self._locked_until:
            self._locked_until = 0.0
            self.msg.configure(text="")
            self.btn.configure(state="normal")
        self.after(1000, self._tick)

    def _submit(self) -> None:
        if self._busy or self._locked_until:
            return
        code = self.code()
        if len(code) != access.CODE_LEN:
            self.msg.configure(text="Escribe los 6 caracteres.")
            return
        self._busy = True
        self.btn.configure(text="Comprobando…", state="disabled")
        result: list[bool] = []

        def work():
            try:
                result.append(access.check(code))
            except Exception:
                result.append(False)
        t = threading.Thread(target=work, daemon=True)
        t.start()
        self._wait(t, result)

    def _wait(self, t: threading.Thread, result: list[bool]) -> None:
        if t.is_alive():
            self.after(60, lambda: self._wait(t, result))
            return
        self._busy = False
        self.btn.configure(text="Activar", state="normal")
        if result and result[0]:
            self.destroy()
            self._on_ok()
            return
        self._fails += 1
        for b in self.boxes:
            b.delete(0, "end")
        self.boxes[0].focus_set()
        if self._fails % MAX_TRIES == 0:
            self._locked_until = time.monotonic() + LOCK_SECONDS
            self.btn.configure(state="disabled")
            self.msg.configure(text=f"Demasiados intentos. Espera {LOCK_SECONDS} s.")
        else:
            self.msg.configure(text="Código no válido.")

    def _cancel(self) -> None:
        self.destroy()
        self._on_cancel()


def ask_code(master, on_ok, on_cancel) -> CodeDialog:
    return CodeDialog(master, on_ok, on_cancel)
