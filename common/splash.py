"""
Splash animado con el logo, mostrado al abrir cualquiera de las apps.

`play(app, on_done)` oculta la ventana principal, muestra un splash sin bordes
con el logo (entra con fundido + deslizamiento) y una barra dorada animada, y al
terminar destruye el splash y llama `on_done` (que reaparece la ventana).

Usa la propia cola de eventos de Tk (app.after), así que no crea un segundo root
ni bloquea. Sin dependencias de runtime extra: la imagen se carga con
tkinter.PhotoImage (PNG) y el fundido con el alfa de la ventana.
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path

import customtkinter as ctk

_BG = "#0e1116"
_FG = "#e6e9ef"
_SUB = "#8a93a6"
_GOLD = "#e8b923"


def _asset(name: str) -> Path | None:
    """Localiza assets/<name> en el .exe (sys._MEIPASS) o en el repo (dev)."""
    bases = [
        Path(getattr(sys, "_MEIPASS", "")) if getattr(sys, "_MEIPASS", "") else None,
        Path(__file__).resolve().parent.parent,
    ]
    for base in bases:
        if base is None:
            continue
        p = base / "assets" / name
        if p.exists():
            return p
    return None


def play(app, on_done, title: str = "ms_rewards", subtitle: str = "Microsoft Rewards") -> None:
    """Muestra el splash animado y llama on_done() al terminar."""
    try:
        app.withdraw()
    except Exception:
        pass

    # Si algo falla creando el splash, no dejes la app oculta: muéstrala.
    try:
        sp = ctk.CTkToplevel(app)
    except Exception:
        on_done()
        return

    sp.overrideredirect(True)
    try:
        sp.attributes("-topmost", True)
        sp.attributes("-alpha", 0.0)
    except Exception:
        pass

    W, H = 400, 340
    try:
        x = (sp.winfo_screenwidth() - W) // 2
        y = (sp.winfo_screenheight() - H) // 2
    except Exception:
        x, y = 600, 300
    sp.geometry(f"{W}x{H}+{x}+{y}")
    sp.configure(fg_color=_BG)

    card = ctk.CTkFrame(sp, fg_color=_BG, corner_radius=0)
    card.pack(fill="both", expand=True)

    # --- Logo (PNG) ---
    Y_LOGO = 110
    logo = None
    img = None
    png = _asset("splash.png")
    if png is not None:
        try:
            img = tk.PhotoImage(file=str(png))
            logo = tk.Label(card, image=img, bg=_BG, bd=0, highlightthickness=0)
            logo.image = img  # evitar GC
            logo.place(relx=0.5, y=Y_LOGO - 28, anchor="center")
        except Exception:
            logo = None

    ctk.CTkLabel(card, text=title, text_color=_FG,
                 font=ctk.CTkFont(size=24, weight="bold")).place(
        relx=0.5, y=210, anchor="center")
    ctk.CTkLabel(card, text=subtitle, text_color=_SUB,
                 font=ctk.CTkFont(size=12)).place(relx=0.5, y=238, anchor="center")

    bar = ctk.CTkProgressBar(card, width=220, height=6, mode="indeterminate",
                             progress_color=_GOLD)
    bar.place(relx=0.5, y=288, anchor="center")
    try:
        bar.start()
    except Exception:
        pass

    # --- Animación: fundido + deslizamiento de entrada, espera, fundido salida ---
    IN, HOLD, OUT, DT = 16, 34, 12, 20
    state = {"phase": "in", "i": 0}

    def _alive() -> bool:
        try:
            return bool(sp.winfo_exists())
        except Exception:
            return False

    def _finish() -> None:
        try:
            bar.stop()
        except Exception:
            pass
        try:
            sp.destroy()
        except Exception:
            pass
        try:
            on_done()
        except Exception:
            pass

    def _tick() -> None:
        if not _alive():
            try:
                on_done()
            except Exception:
                pass
            return
        phase, i = state["phase"], state["i"]
        if phase == "in":
            t = i / IN
            e = 1 - (1 - t) * (1 - t)  # ease-out
            try:
                sp.attributes("-alpha", e)
            except Exception:
                pass
            if logo is not None:
                logo.place_configure(y=int((Y_LOGO - 28) + 28 * e))
            state["i"] += 1
            if i >= IN:
                state.update(phase="hold", i=0)
            app.after(DT, _tick)
        elif phase == "hold":
            state["i"] += 1
            if i >= HOLD:
                state.update(phase="out", i=0)
            app.after(DT, _tick)
        else:
            try:
                sp.attributes("-alpha", max(0.0, 1.0 - i / OUT))
            except Exception:
                pass
            state["i"] += 1
            if i >= OUT:
                _finish()
                return
            app.after(DT, _tick)

    app.after(10, _tick)
