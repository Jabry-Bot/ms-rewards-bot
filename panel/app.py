"""
Panel de control de Jab-rewards (customtkinter).

Composición: a la izquierda, la columna de marca (medalla, saldo, nivel y las
acciones secundarias); en el centro, el protagonista —el anillo del día con
los puntos ganados y «Ejecutar ahora»— junto al historial de puntos del mes;
debajo, el detalle de hoy, la app de Bing y la tarea programada; y al pie, la
barra de actividad con el proceso en marcha y el registro plegado.

Toda la lógica (rutas, comandos, textos de estado, historial) vive en
panel/core.py; los design tokens en panel/theme.py, los gráficos en
panel/art.py y los componentes en panel/widgets.py. Aquí solo se compone la
interfaz, se lanzan subprocesos sin congelarla (hilo lector + cola + after())
y se vuelca su salida al registro.

Reglas: los subprocesos van siempre con CREATE_NO_WINDOW (cero consolas) y
nada de topmost/focus_force nuevos (los usuarios juegan mientras corre el bot).
"""
from __future__ import annotations

import ctypes
import os
import queue
import subprocess
import threading
import time
import tkinter
from datetime import date, datetime

import customtkinter as ctk

# Import del core: como paquete (python -m panel.app) o suelto (exe empaquetado).
try:
    from panel import core, theme as T, widgets as W
except ImportError:
    import core
    import theme as T
    import widgets as W

try:
    from common import splash
except ImportError:
    splash = None

try:
    from common import access, access_ui
except ImportError:
    import access  # type: ignore
    import access_ui  # type: ignore


# --- Tema global ---------------------------------------------------------
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_HERO_ICONS = {"check": "check", "warning": "warning", "error": "error",
               "clock": "clock", "info": "info"}
_LOG_MAX_LINES = 4000          # el registro se recorta para no crecer sin fin
_STATUS_REFRESH_MS = 60_000    # relee last_run.json (barato) cada minuto
_TASK_REFRESH_EVERY = 10       # …y la tarea programada cada 10 refrescos


# =========================================================================
# Diálogos
# =========================================================================
class UninstallDialog(W.Dialog):
    """Un checkbox por opción de desinstalación.

    `self.result` queda como la lista de claves marcadas si el usuario confirma,
    o None si cancela.
    """

    def __init__(self, master):
        super().__init__(master, "Desinstalar", width=500)
        self.header("Desinstalar",
                    "Marca lo que quieres eliminar. Lo seleccionado se borra de forma "
                    "permanente.",
                    icon="delete", tone="error")

        box = ctk.CTkFrame(self.body, fg_color=T.SURFACE_2, corner_radius=T.R_CONTROL,
                           border_width=1, border_color=T.BORDER)
        box.grid(row=1, column=0, sticky="ew", pady=(T.S5, 0))
        box.grid_columnconfigure(0, weight=1)

        # Un checkbox por opción de core.UNINSTALL_OPTIONS.
        self._vars: list[tuple[str, ctk.BooleanVar]] = []
        for i, (key, text, default) in enumerate(core.UNINSTALL_OPTIONS):
            main, hint = core.split_option_label(text)
            var = ctk.BooleanVar(value=default)
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.grid(row=i * 2, column=0, sticky="ew", padx=T.S4,
                     pady=(T.S3 if i == 0 else T.S2, T.S3 if i == len(
                         core.UNINSTALL_OPTIONS) - 1 else T.S2))
            row.grid_columnconfigure(0, weight=1)
            ctk.CTkCheckBox(
                row, text=main, variable=var, command=self._update_count,
                font=T.font("body"), text_color=T.TEXT, checkbox_width=20,
                checkbox_height=20, corner_radius=T.R_SMALL, border_width=2,
                border_color=T.BORDER_STRONG, fg_color=T.DANGER,
                hover_color=T.DANGER_HOVER, checkmark_color=T.ON_DANGER,
            ).grid(row=0, column=0, sticky="w")
            if hint:
                # Mayúscula inicial en frases, no en nombres de archivo.
                if " " in hint:
                    hint = hint[:1].upper() + hint[1:]
                W.label(row, hint, font="small", color=T.MUTED, height=16).grid(
                    row=1, column=0, sticky="w", padx=(30, 0))
            if i < len(core.UNINSTALL_OPTIONS) - 1:
                W.separator(box).grid(row=i * 2 + 1, column=0, sticky="ew", padx=T.S4)
            self._vars.append((key, var))

        bar = self.footer(row=2)
        self.ok_btn = W.make_button(bar, "Desinstalar", self._accept, style="danger",
                                    icon="delete", icons=self._icons, width=150)
        self.ok_btn.pack(side="right")
        W.make_button(bar, "Cancelar", self._cancel, style="secondary",
                      width=110).pack(side="right", padx=(0, T.S2))
        self._update_count()
        self.show()

    def _selected(self) -> list[str]:
        return [key for key, var in self._vars if var.get()]

    def _update_count(self):
        n = len(self._selected())
        W.restyle(self.ok_btn, text=f"Desinstalar ({n})" if n else "Desinstalar")
        W.set_enabled(self.ok_btn, n > 0)

    def _accept(self):
        self.close(self._selected())


class GoalDialog(W.Dialog):
    """Calculadora de precios y objetivo de compra.

    Mientras se escribe el precio calcula al momento, sin guardar nada: los
    puntos que cuesta y con qué tarjetas, lo que ya tienes, lo que falta y
    cuándo llegarías a tu ritmo. Dos modos:
      · objetivo (por defecto): nombre + precio; «Guardar» es el principal y,
        al editar, se puede quitar.
      · consulta (query=True): «Calcular un precio»; «Cerrar» es el principal
        y «Guardar como objetivo» queda como secundario.
    `self.result`: {"name", "price_eur"} al guardar, "clear" al quitarlo o
    None si solo se consultó / se canceló.
    """

    WIDTH = 480

    def __init__(self, master, goal: dict | None = None, *, balance: int | None = None,
                 pace: float = 0, query: bool = False):
        super().__init__(master, "Calcular un precio" if query else "Objetivo",
                         width=self.WIDTH)
        self._balance, self._pace, self._query = balance, pace, query
        if query:
            self.header("Calcular un precio",
                        "Escribe un precio y mira cuántos puntos son y cuándo te "
                        "llegaría. No se guarda nada.",
                        icon="calculator", tone="accent")
        else:
            self.header("Editar objetivo" if goal else "Fijar un objetivo",
                        "Pon el precio de lo que quieres comprar: te digo cuántos "
                        "puntos son y cuándo llegarías a tu ritmo.",
                        icon="flag", tone="accent")
        form = ctk.CTkFrame(self.body, fg_color="transparent")
        form.grid(row=1, column=0, sticky="ew", pady=(T.S5, 0))
        form.grid_columnconfigure(0, weight=1)

        def entry(row: int, title: str, placeholder: str) -> ctk.CTkEntry:
            W.caption(form, title, height=14).grid(row=row, column=0, sticky="w",
                                                   pady=(0 if row == 0 else T.S3, T.S1))
            e = ctk.CTkEntry(form, height=36, corner_radius=T.R_CONTROL, border_width=1,
                             fg_color=T.SURFACE_2, border_color=T.BORDER_STRONG,
                             text_color=T.TEXT, font=T.font("body"),
                             placeholder_text=placeholder,
                             placeholder_text_color=T.FAINT)
            e.grid(row=row + 1, column=0, sticky="ew")
            return e

        self.name = None if query else entry(0, "Nombre (opcional)", "p. ej. CoD MW4")
        self.price = entry(2, "Precio en €", "56,99")
        if goal and not query:
            if goal.get("name"):
                self.name.insert(0, goal["name"])
            self.price.insert(0, core.format_eur(goal["price_eur"]).replace(" €", "")
                              .replace(".", ""))

        self._build_result(form, row=4)
        # Qué elegir al canjear (cuando el cálculo usa la personalizada) y el
        # aviso de caducidad.
        self.note = W.label(form, core.GIFT_CARD_NOTE, font="small", color=T.MUTED,
                            wraplength=self.WIDTH - 2 * T.S6)
        self.note.grid(row=5, column=0, sticky="w", pady=(T.S3, 0))

        bar = self.footer(row=2)
        if query:
            close = W.make_button(bar, "Cerrar", self._cancel, style="primary", width=110)
            close.pack(side="right")
            self.ok_btn = W.make_button(bar, "Guardar como objetivo", self._accept,
                                        style="secondary", icon="flag",
                                        icons=self._icons, width=196)
            self.ok_btn.pack(side="right", padx=(0, T.S2))
        else:
            self.ok_btn = W.make_button(bar, "Guardar", self._accept, style="primary",
                                        width=120)
            self.ok_btn.pack(side="right")
            W.make_button(bar, "Cancelar", self._cancel, style="secondary",
                          width=110).pack(side="right", padx=(0, T.S2))
            if goal:
                W.make_button(bar, "Quitar objetivo", lambda: self.close("clear"),
                              style="ghost", icon="delete", icons=self._icons,
                              width=150).pack(side="left")
            self.bind("<Return>", lambda _e: self._accept())
        self.price.bind("<KeyRelease>", lambda _e: self._update())
        self._shown = object()
        self._update()
        self.show()
        self.price.focus_set()

    def _build_result(self, parent, row: int):
        """Bloque de resultado: la cifra grande (puntos) con su desglose, una
        barra de lo que ya tienes y tres datos: tienes / te faltan / llegarías."""
        bg = T.SURFACE_2
        box = ctk.CTkFrame(parent, fg_color=bg, corner_radius=T.R_CARD, border_width=1,
                           border_color=T.BORDER)
        box.grid(row=row, column=0, sticky="ew", pady=(T.S4, 0))
        box.grid_columnconfigure(0, weight=1)
        pad = T.S4
        top = ctk.CTkFrame(box, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=pad, pady=(T.S3, 0))
        top.grid_columnconfigure(1, weight=1)
        W.caption(top, "Cuesta", height=14).grid(row=0, column=0, columnspan=2, sticky="w")
        self.r_points = W.Figure(top, font="figure", drop=7)
        self.r_points.grid(row=1, column=0, sticky="w")
        self.r_cards = W.label(top, "", font="small", color=T.TEXT_2, anchor="e",
                               justify="right", height=34)
        self.r_cards.grid(row=1, column=1, sticky="se", pady=(0, 5))
        self.r_bar = ctk.CTkProgressBar(box, height=6, corner_radius=3, border_width=0,
                                        fg_color=T.SURFACE_3, progress_color=T.GOLD)
        self.r_bar.grid(row=1, column=0, sticky="ew", padx=pad, pady=(T.S1, T.S3))
        self.r_bar.set(0)
        stats = ctk.CTkFrame(box, fg_color="transparent")
        stats.grid(row=2, column=0, sticky="ew", padx=pad)
        self.r_have = W.Stat(stats, "Tienes")
        self.r_left = W.Stat(stats, "Te faltan")
        self.r_when = W.Stat(stats, "Llegarías en")
        for i, stat in enumerate((self.r_have, self.r_left, self.r_when)):
            stats.grid_columnconfigure(i, weight=1, uniform="r")
            stat.grid(row=0, column=i, sticky="w")
        self.r_foot = W.label(box, "", font="small", color=T.MUTED, height=16)
        self.r_foot.grid(row=3, column=0, sticky="w", padx=pad, pady=(T.S2, T.S3))

    def _update(self):
        """Recalcula el bloque con lo escrito (barato: se llama en cada tecla,
        pero solo repinta si el precio ha cambiado)."""
        text = self.price.get().strip()
        price = core.parse_price(text)
        if price == self._shown:
            return
        self._shown = price
        g = core.goal_progress({"price_eur": price}, self._balance, self._pace) \
            if price is not None else None
        W.set_enabled(self.ok_btn, g is not None)
        hint = g["card_hint"] if g else ""
        self.note.configure(text=(hint + ".\n" if hint else "") + core.GIFT_CARD_NOTE)
        if g is None:
            self.r_points.set(None)
            self.r_cards.configure(text="")
            self.r_bar.set(0)
            for stat in (self.r_have, self.r_left, self.r_when):
                stat.set("—", color=T.FAINT)
            self.r_foot.configure(
                text="Escribe un precio mayor que 0, p. ej. 56,99" if text
                else "Escribe un precio para ver el cálculo",
                text_color=T.tone("warn") if text else T.MUTED)
            return
        self.r_points.set(core.format_points(g["points"]), unit="pts")
        self.r_cards.configure(
            text=g["breakdown"] + ("" if g["covered_eur"] == price else
                                   f"\ncubre {core.format_eur(g['covered_eur'])}"))
        self.r_bar.set(g["fraction"])
        self.r_bar.configure(progress_color=T.tone("ok") if g["reached"] else T.GOLD)
        if g["balance"] is None:
            self.r_have.set("—", "sin datos", T.FAINT)
        else:
            self.r_have.set(core.format_points(g["balance"]), f"{g['percent']} %")
        if g["reached"]:
            self.r_left.set("¡Ya te llega!", color=T.tone("ok"))
            self.r_when.set("Hoy")
            foot = f"Te sobrarían {core.format_points(g['surplus'])} pts"
        else:
            self.r_left.set(core.format_points(g["left"]), "pts")
            if g["days"] is None:
                self.r_when.set("—", color=T.FAINT)
                foot = "Sin datos suficientes para estimar cuándo llegarías"
            else:
                when, _, target = g["eta_text"].partition(" · ")
                self.r_when.set(when)
                foot = f"{target[:1].upper()}{target[1:]} · {g['pace_text']}"
        self.r_foot.configure(text=foot, text_color=T.MUTED)

    def _accept(self):
        price = core.parse_price(self.price.get())
        if price is not None:
            self.close({"name": self.name.get().strip() if self.name else "",
                        "price_eur": price})


class SettingsDialog(W.Dialog):
    """Ajustes: cuenta, navegador y mantenimiento, como botones de verdad con
    una línea que dice lo que hace cada uno.

    No ejecuta nada por sí mismo: al pulsar una acción se cierra y deja en
    `self.result` lo elegido para que la ventana principal lo lance con sus
    confirmaciones de siempre: ("action", id) · ("switch",) · ("logs",) ·
    ("uninstall",) · ("browser", "Chrome" | "Edge") · None si solo se cierra.
    Con `enabled=False` (proceso en marcha o falta el venv) las acciones del
    bot y el selector de navegador salen deshabilitados.
    """

    WIDTH = 580
    _KNOWN = ("login", "link_app", "kill")

    def __init__(self, master, *, enabled: bool = True, browser: str = "chrome"):
        super().__init__(master, "Ajustes", width=self.WIDTH)
        self._enabled = enabled
        self.header("Ajustes",
                    "Cuenta, navegador y mantenimiento. Cada botón lanza su acción y "
                    "cierra esta ventana.", icon="settings", tone="accent")
        row = 1
        if not enabled:
            W.label(self.body, "Hay un proceso en marcha (o falta instalar el bot): las "
                    "acciones estarán disponibles cuando termine.", font="small",
                    color=T.tone("warn"), wraplength=self.WIDTH - 2 * T.S6).grid(
                row=row, column=0, sticky="w", pady=(T.S3, 0))
            row += 1

        acts = core.ACTIONS
        box = self._section(row, "Cuenta")
        if "login" in acts:
            self._action_row(box, "key", "Login manual",
                             "Abre el navegador para iniciar sesión a mano (2FA, captcha).",
                             "Iniciar sesión", ("action", "login"))
        self._action_row(box, "switch_user", "Cambiar cuenta",
                         "Cierra la sesión actual y abre el navegador para entrar con otra.",
                         "Cambiar…", ("switch",))
        if "link_app" in acts:
            self._action_row(box, "phone", "Vincular app de Bing",
                             "Inicia sesión una vez: check-in y «Leer para ganar» (+30/día).",
                             "Vincular", ("action", "link_app"))

        box = self._section(row + 1, "Navegador y sistema")
        line = self._row(box, "globe", "Navegador",
                         "El que usa el bot. Edge suma puntos extra por usarlo.")
        on = T.mix(T.GOLD, T.SURFACE_2, 0.3)
        switch = ctk.CTkSegmentedButton(
            line, values=["Chrome", "Edge"],
            command=lambda value: self.close(("browser", value)),
            height=30, corner_radius=T.R_CONTROL, border_width=2,
            font=T.font("small_strong"), fg_color=T.SURFACE,
            selected_color=on if enabled else T.SURFACE_3,
            selected_hover_color=on if enabled else T.SURFACE_3,
            unselected_color=T.SURFACE, unselected_hover_color=T.SURFACE_3,
            text_color=T.TEXT, text_color_disabled=T.FAINT, dynamic_resizing=False,
            width=132)
        switch.set("Edge" if browser == "edge" else "Chrome")
        if not enabled:
            switch.configure(state="disabled")
        switch.grid(row=0, column=2, rowspan=2, sticky="e")
        if "kill" in acts:
            self._action_row(box, "power", "Detener navegador",
                             "Cierra cualquier navegador del bot que se haya quedado colgado.",
                             "Detener", ("action", "kill"))
        # Cualquier acción nueva de core.ACTIONS que este diálogo no conozca.
        for aid, action in acts.items():
            if aid not in self._KNOWN and aid not in ("run_all", "daily", "searches"):
                self._action_row(box, W.ACTION_ICONS.get(aid, "play"),
                                 core.action_text(action), action.description,
                                 "Ejecutar", ("action", aid))
        self._action_row(box, "folder", "Carpeta de logs",
                         "Los registros de cada día, por si hay que revisar qué pasó.",
                         "Abrir", ("logs",), needs_bot=False)

        box = self._section(row + 2, "Zona de peligro", danger=True)
        self._action_row(box, "delete", "Desinstalar",
                         "Elige qué borrar: tarea programada, estado, credenciales, perfil…",
                         "Desinstalar…", ("uninstall",), style="danger_outline")

        bar = self.footer(row=row + 3)
        W.make_button(bar, "Cerrar", self._cancel, style="secondary",
                      width=110).pack(side="right")
        self.show()

    def _section(self, row: int, title: str, danger: bool = False) -> ctk.CTkFrame:
        wrap = ctk.CTkFrame(self.body, fg_color="transparent")
        wrap.grid(row=row, column=0, sticky="ew", pady=(T.S4, 0))
        wrap.grid_columnconfigure(0, weight=1)
        W.caption(wrap, title, height=14,
                  color=T.tone("error") if danger else T.MUTED).grid(
            row=0, column=0, sticky="w", pady=(0, T.S1 + 2))
        box = ctk.CTkFrame(wrap, fg_color=T.SURFACE_2, corner_radius=T.R_CARD,
                           border_width=1,
                           border_color=T.mix(T.tone("error"), T.SURFACE_2, 0.3)
                           if danger else T.BORDER)
        box.grid(row=1, column=0, sticky="ew")
        box.grid_columnconfigure(0, weight=1)
        box._rows = 0
        return box

    def _row(self, box, icon: str, title: str, desc: str) -> ctk.CTkFrame:
        """Fila de un ajuste: icono, título y descripción; el control va en la
        columna 2 (lo coloca quien llama)."""
        if box._rows:
            W.separator(box, T.BORDER).grid(row=box._rows * 2 - 1, column=0, sticky="ew",
                                            padx=T.S4)
        line = ctk.CTkFrame(box, fg_color="transparent")
        line.grid(row=box._rows * 2, column=0, sticky="ew", padx=T.S4, pady=T.S2 + 1)
        line.grid_columnconfigure(1, weight=1)
        box._rows += 1
        img = self._icons.get(icon, T.TEXT_2, 18) if self._icons else None
        ctk.CTkLabel(line, text="", image=img, width=18, height=20).grid(
            row=0, column=0, rowspan=2, padx=(0, T.S3))
        W.label(line, title, font="body_strong", height=18).grid(row=0, column=1, sticky="w")
        W.label(line, desc, font="small", color=T.MUTED, height=16,
                wraplength=self.WIDTH - 2 * T.S6 - 2 * T.S4 - 30 - 150).grid(
            row=1, column=1, sticky="w")
        return line

    def _action_row(self, box, icon: str, title: str, desc: str, button: str,
                    result: tuple, *, style: str = "secondary", needs_bot: bool = True):
        line = self._row(box, icon, title, desc)
        btn = W.make_button(line, button, lambda: self.close(result), style=style,
                            width=132, height=30)
        btn.grid(row=0, column=2, rowspan=2, sticky="e", padx=(T.S3, 0))
        if needs_bot and not self._enabled:
            W.set_enabled(btn, False)


class VisibilityDialog(W.Dialog):
    """Elegir primer plano (navegador visible) o segundo plano (oculto) antes
    de una corrida manual del bot.

    `self.result` queda como False (primer plano), True (segundo plano/oculto)
    si el usuario elige, o None si cancela.
    """

    def __init__(self, master, action_label: str):
        super().__init__(master, "¿Cómo ejecutar?", width=480)
        self.header(action_label,
                    "¿Quieres ver el navegador mientras trabaja, o que vaya oculto en "
                    "segundo plano para que no te moleste?",
                    icon="play", tone="accent")
        opts = ctk.CTkFrame(self.body, fg_color="transparent")
        opts.grid(row=1, column=0, sticky="ew", pady=(T.S5, 0))
        opts.grid_columnconfigure(0, weight=1)
        W.OptionCard(opts, self._icons, "moon", "Segundo plano",
                     "Oculto; no te molesta si estás jugando.",
                     lambda: self._choose(True), badge="Recomendado").grid(
            row=0, column=0, sticky="ew")
        W.OptionCard(opts, self._icons, "monitor", "Primer plano",
                     "Verás el navegador mientras hace las tareas.",
                     lambda: self._choose(False)).grid(
            row=1, column=0, sticky="ew", pady=(T.S2, 0))
        bar = self.footer(row=2)
        W.make_button(bar, "Cancelar", self._cancel, style="secondary",
                      width=110).pack(side="right")
        self.bind("<Return>", lambda _e: self._choose(True))   # Intro = recomendado
        self.show()

    def _choose(self, hidden: bool):
        self.close(hidden)


# =========================================================================
# Ventana principal
# =========================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__(fg_color=T.BG)
        T.resolve_fonts(self)
        self.title("Jab-rewards — Panel de control")
        self._place_initial()
        self.minsize(T.MIN_W, T.MIN_H)
        try:
            ico = core.icon_path()
            if ico:
                self.iconbitmap(str(ico))
        except Exception:
            pass
        self.icons = W.Icons()

        # Estado de ejecución de subprocesos.
        self.proc: subprocess.Popen | None = None
        self.out_queue: queue.Queue[str | None] = queue.Queue()
        # Llamadas que los hilos piden ejecutar en el hilo de la UI.
        self._ui_calls: queue.Queue = queue.Queue()
        self.action_buttons: list[ctk.CTkButton] = []
        self._venv_ok = core.venv_ready()
        # Callback a ejecutar cuando termine el proceso actual (p.ej. tras el
        # auto-update, comprobar si hay que auto-actualizar el propio .exe).
        self._on_done_cb = None
        self._proc_title = ""
        self._proc_started = 0.0
        self._cancelled = False
        self._last_line = ""
        self._log_open = False
        self._bar_state = "idle"
        self._bar_detail_full = ""
        # Estado mostrado en el dashboard.
        self._last_run: dict = {}
        self._task: dict = {}
        self._task_known = False
        self._task_busy = False
        self._task_again = False
        self._refresh_ticks = 0
        self._history: list[dict] = []
        self._history_known = False
        self._pace = 0                   # ritmo mensual (history_summary)
        self._actions_on = True          # ¿acciones del bot disponibles ahora?
        self._view_day: str | None = None   # día del historial que se mira (None = hoy)
        self._day_cache: dict = {}       # 'YYYY-MM-DD' → desglose del log (o None)
        self._goal = core.read_goal()    # objetivo de compra, si hay
        self._history_busy = False
        self._ring_seen = False         # el anillo solo se anima la primera vez

        # Limpia el .old.exe que dejó una auto-actualización previa.
        core.cleanup_old_exe()

        self._build_layout()
        # Densidad adaptable: en ventanas bajas (portátiles 1366×768, 1080p al
        # 150 %) se compacta el dashboard; en las altas, el anillo crece.
        self._density: str | None = None
        self.bind("<Configure>", self._on_resize, add=True)
        self._poll_queue()      # bombea las colas de salida/UI
        self.refresh_status()   # estado inicial al arrancar
        self.after(_STATUS_REFRESH_MS, self._periodic_refresh)
        # Al cerrar la ventana, no dejar el subproceso (navegador/run.py) huérfano.
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        # Splash animado con el logo al abrir; al terminar reaparece la ventana
        # (o, si este equipo aún no está activado, la pantalla del código).
        self._activated = access.is_activated(core.STATE_DIR)
        if splash is not None:
            splash.play(self, on_done=self._after_splash,
                        title="Jab-rewards", subtitle="Panel de control")
        elif not self._activated:
            self.withdraw()
            self.after(0, self._ask_access)
        # Al abrir, comprobar/aplicar actualizaciones una vez (no bloqueante).
        # Se programa con after() para que la ventana aparezca primero.
        self.after(400, self._auto_update)

    # --- Tamaño inicial ---------------------------------------------------
    def _place_initial(self):
        """Tamaño cómodo que quepa en el área de trabajo (sin la barra de
        tareas), centrado. En 1366×768 ocupa casi toda la pantalla."""
        scale = W.window_scale(self)
        left, top = 0, 0
        aw, ah = self.winfo_screenwidth(), self.winfo_screenheight() - 48
        try:
            class _Rect(ctypes.Structure):
                _fields_ = [("l", ctypes.c_long), ("t", ctypes.c_long),
                            ("r", ctypes.c_long), ("b", ctypes.c_long)]
            r = _Rect()
            if ctypes.windll.user32.SystemParametersInfoW(0x30, 0, ctypes.byref(r), 0):
                left, top, aw, ah = r.l, r.t, r.r - r.l, r.b - r.t
        except Exception:
            pass
        title_bar = round(32 * scale)
        w = max(T.MIN_W, min(T.INIT_W, int(aw / scale) - 16))
        h = max(T.MIN_H, min(T.INIT_H, int((ah - title_bar) / scale) - 8))
        x = left + max(0, (aw - round(w * scale)) // 2)
        y = top + max(0, (ah - title_bar - round(h * scale)) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

    # --- Construcción de la UI ------------------------------------------
    def _build_layout(self):
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_rail()

        self.main = ctk.CTkFrame(self, fg_color=T.BG, corner_radius=0)
        self.main.grid(row=0, column=1, sticky="nsew")
        self.main.grid_columnconfigure(0, weight=1)
        # El alto sobrante va sobre todo arriba: al protagonista y al historial.
        self.main.grid_rowconfigure(0, weight=2)
        self.main.grid_rowconfigure(1, weight=1)
        self._build_top()
        self._build_cards()
        self._build_log_panel()
        self._build_activity_bar()

        # Si falta el venv, deshabilitar las acciones que lo usan.
        if not self._venv_ok:
            self._set_actions_enabled(False)

    def _build_rail(self):
        """Columna de identidad y dinero: logo, saldo y nivel, el objetivo, la
        calculadora y tu ritmo; abajo, el botón de Ajustes. Nada aquí cambia de
        vista, así que nada tiene aspecto de menú de navegación."""
        rail = ctk.CTkFrame(self, fg_color=T.RAIL, corner_radius=0, width=T.RAIL_W)
        rail.grid(row=0, column=0, sticky="nsw")
        rail.pack_propagate(False)
        self.rail = rail
        tkinter.Frame(self, width=1, bg=T.BORDER, bd=0, highlightthickness=0).grid(
            row=0, column=0, sticky="nse")

        brand = ctk.CTkFrame(rail, fg_color="transparent")
        brand.pack(fill="x", padx=T.S4, pady=(T.S3, 0))
        ctk.CTkLabel(brand, text="", image=W.load_logo(34), width=34, height=34).grid(
            row=0, column=0, rowspan=2, padx=(0, T.S3))
        W.label(brand, "Jab-rewards", font="brand", height=20).grid(
            row=0, column=1, sticky="sw")
        version = core.build_version()
        W.label(brand, f"Panel · v{version}" if version else "Panel de control",
                font="small", color=T.MUTED, height=18).grid(row=1, column=1, sticky="nw")

        # Saldo: la cifra que el usuario viene a mirar, siempre a la vista.
        wallet_bg = T.mix(T.GOLD, T.RAIL, 0.07)
        wallet = ctk.CTkFrame(rail, fg_color=wallet_bg, corner_radius=T.R_CARD,
                              border_width=1, border_color=T.mix(T.GOLD, T.RAIL, 0.24))
        wallet.pack(fill="x", padx=T.S3, pady=(T.S3, 0))
        wallet.grid_columnconfigure(1, weight=1)
        W.caption(wallet, "Saldo", color=T.mix(T.GOLD, wallet_bg, 0.72), height=14).grid(
            row=0, column=0, columnspan=2, sticky="w", padx=T.S4, pady=(T.S3, 0))
        self.balance = W.label(wallet, "—", font="balance", color=T.GOLD, height=34)
        self.balance.grid(row=1, column=0, sticky="w", padx=(T.S4, 0))
        self.balance_unit = W.label(wallet, "pts", font="small", color=T.MUTED, height=18)
        self.balance_unit.grid(row=1, column=1, sticky="sw", padx=(T.S1 + 1, 0), pady=(0, 5))
        level = ctk.CTkFrame(wallet, fg_color="transparent")
        level.grid(row=2, column=0, columnspan=2, sticky="w", padx=T.S4, pady=(2, T.S3))
        self.level_coin = ctk.CTkLabel(level, text="", width=16, height=18)
        W.place_grid(self.level_coin, row=0, column=0, padx=(0, T.S2))
        self.level_name = W.label(level, "", font="small_strong", color=T.TEXT, height=18)
        self.level_name.grid(row=0, column=1, sticky="w")
        self.level_sub = W.label(level, "", font="small", color=T.MUTED, height=18)
        self.level_sub.grid(row=0, column=2, sticky="w", padx=(T.S1 + 1, 0))

        self._run_action = core.ACTIONS.get("run_all")   # va en el protagonista
        self._build_goal(rail)

        # Tu ritmo: datos del historial (no son botones: sin icono ni hover).
        pace = ctk.CTkFrame(rail, fg_color="transparent")
        pace.pack(fill="x", padx=T.S5, pady=(T.S5, 0))
        pace.grid_columnconfigure(0, weight=1)
        W.caption(pace, "Tu ritmo", height=14).grid(row=0, column=0, columnspan=2,
                                                    sticky="w", pady=(0, T.S1))
        self.rail_stats: dict[str, ctk.CTkLabel] = {}
        for i, (key, title) in enumerate((("average", "Media diaria"),
                                          ("best", "Mejor día"),
                                          ("days", "Días con puntos"))):
            W.label(pace, title, font="small", color=T.MUTED, height=20).grid(
                row=i + 1, column=0, sticky="w")
            value = W.label(pace, "—", font="small_strong", color=T.FAINT, height=20,
                            anchor="e")
            value.grid(row=i + 1, column=1, sticky="e")
            self.rail_stats[key] = value

        # Ajustes: cuenta, navegador, mantenimiento y desinstalar viven en su
        # propia ventana. Es un botón (borde y fondo), no un enlace de menú.
        W.make_button(rail, "Ajustes", self._on_settings, style="secondary",
                      icon="settings", icons=self.icons, height=34).pack(
            side="bottom", fill="x", padx=T.S3, pady=(0, T.S3))

    def _build_goal(self, rail):
        """Objetivo de compra, bajo el saldo: cuánto cuesta en puntos, cuánto
        llevas y cuándo llegarías. Sin objetivo no se muestra (se fija desde la
        calculadora).
        Toda la tarjeta es clicable (abre el diálogo para editarlo)."""
        # height=1: sin objetivo el contenedor queda vacío y no debe ocupar sitio
        # (un CTkFrame vacío mide 200 px por defecto).
        holder = ctk.CTkFrame(rail, fg_color="transparent", height=1)
        holder.pack(fill="x", padx=T.S3, pady=(T.S2, 0))
        holder.grid_columnconfigure(0, weight=1)
        # Sin objetivo no se muestra nada aquí: se fija desde la calculadora
        # («Guardar como objetivo»), que está siempre a mano.
        W.make_button(rail, "Calcular un precio", lambda: self._on_goal(query=True),
                      style="tonal", icon="calculator", icons=self.icons,
                      height=34).pack(fill="x", padx=T.S3, pady=(T.S2, 0))

        self._goal_bg = T.mix(T.TEXT, T.RAIL, 0.035)
        card = ctk.CTkFrame(holder, fg_color=self._goal_bg, corner_radius=T.R_CARD,
                            border_width=1, border_color=T.BORDER, cursor="hand2")
        W.place_grid(card, row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1)
        self.goal_card = card
        pad = T.S3 + 2
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=pad, pady=(T.S2 + 2, 0))
        top.grid_columnconfigure(0, weight=1)
        W.caption(top, "Objetivo", height=14).grid(row=0, column=0, sticky="w")
        self.goal_price = W.label(top, "", font="small_strong", color=T.TEXT_2,
                                  height=14, anchor="e")
        self.goal_price.grid(row=0, column=1, sticky="e")
        self.goal_name = W.label(card, "", font="body_strong", height=18)
        self.goal_name.grid(row=1, column=0, sticky="w", padx=pad)
        self.goal_bar = ctk.CTkProgressBar(card, height=6, corner_radius=3, border_width=0,
                                           fg_color=T.SURFACE_3, progress_color=T.GOLD)
        self.goal_bar.grid(row=2, column=0, sticky="ew", padx=pad, pady=(T.S1 + 1, T.S1))
        nums = ctk.CTkFrame(card, fg_color="transparent")
        nums.grid(row=3, column=0, sticky="ew", padx=pad)
        self.goal_percent = W.label(nums, "", font="stat", height=20)
        self.goal_percent.pack(side="left")
        self.goal_total = W.label(nums, "", font="small", color=T.MUTED, height=20)
        self.goal_total.pack(side="left", padx=(T.S1 + 1, 0))
        self.goal_left = W.label(card, "", font="small", color=T.TEXT_2, height=16)
        self.goal_left.grid(row=4, column=0, sticky="w", padx=pad)
        self.goal_eta = W.label(card, "", font="small", color=T.MUTED,
                                wraplength=T.RAIL_W - 2 * T.S3 - 2 * pad)
        self.goal_eta.grid(row=5, column=0, sticky="w", padx=pad)
        self.goal_hint = W.label(card, "", font="caption", color=T.MUTED, height=13)
        self.goal_hint.grid(row=6, column=0, sticky="w", padx=pad, pady=(1, T.S2))

        def bind_all(widget):
            widget.bind("<Button-1>", lambda _e: self._on_goal(), add="+")
            widget.bind("<Enter>", lambda _e: card.configure(
                border_color=T.mix(T.GOLD, T.RAIL, 0.5)), add="+")
            widget.bind("<Leave>", lambda _e: card.configure(border_color=T.BORDER),
                        add="+")
            for child in widget.winfo_children():
                bind_all(child)
        bind_all(card)
        self._render_goal()

    def _render_goal(self):
        g = core.goal_progress(self._goal,
                               core.current_balance(self._last_run, self._history),
                               self._pace)
        W.set_visible(self.goal_card, g is not None)
        if g is None:
            return
        scale = W.window_scale(self)
        width = round((T.RAIL_W - 2 * T.S3 - 2 * (T.S3 + 2)) * scale)
        self.goal_name.configure(text=W.fit_text(g["name"], T.font("body_strong"),
                                                 width, scale))
        self.goal_price.configure(text=g["price_text"])
        self.goal_bar.set(g["fraction"])
        self.goal_bar.configure(progress_color=T.tone("ok") if g["reached"] else T.GOLD)
        self.goal_percent.configure(text=f"{g['percent']} %")
        self.goal_total.configure(text=f"de {core.format_points(g['points'])} pts")
        if g["reached"]:
            self.goal_left.configure(text=g["eta_text"], text_color=T.tone("ok"))
            self.goal_eta.configure(text="Canjea justo antes de comprar.")
        else:
            self.goal_left.configure(
                text=f"Faltan {core.format_points(g['left'])} pts", text_color=T.TEXT_2)
            self.goal_eta.configure(text=g["eta_text"])
        self.goal_hint.configure(
            text=f"XBOX · PERSONALIZADO ({core.CUSTOM_CARD_MIN}–{core.CUSTOM_CARD_MAX} €)"
            if g["card_hint"] else "")

    def _on_goal(self, query: bool = False):
        """Abre el diálogo del objetivo (o la calculadora, con query=True) y
        guarda / quita lo que se decida. Consultar no guarda nada."""
        dialog = GoalDialog(self, self._goal,
                            balance=core.current_balance(self._last_run, self._history),
                            pace=self._pace, query=query)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        if dialog.result == "clear":
            if core.clear_goal():
                self._goal = None
        else:
            # Desde la calculadora no se pisa un objetivo existente sin preguntar.
            if query and self._goal:
                old = self._goal.get("name") or "tu objetivo actual"
                if not self._ask("Reemplazar objetivo",
                                 f"Ya tienes un objetivo: {old} "
                                 f"({core.format_eur(self._goal.get('price_eur'))}).\n"
                                 "¿Lo reemplazas por este precio?",
                                 ok_text="Reemplazar"):
                    return
            saved = core.save_goal(dialog.result["name"], dialog.result["price_eur"])
            if saved is None:
                self._info("Error", "No se pudo guardar el objetivo.", kind="error")
                return
            self._goal = saved
        self._render_goal()

    def _columns(self, row):
        """Las dos columnas del área principal (3:2), iguales arriba y abajo."""
        row.grid_columnconfigure(0, weight=3, uniform="cols")
        row.grid_columnconfigure(1, minsize=T.GAP)
        row.grid_columnconfigure(2, weight=2, uniform="cols")
        row.grid_rowconfigure(0, weight=1)

    def _build_top(self):
        row = ctk.CTkFrame(self.main, fg_color="transparent")
        row.grid(row=0, column=0, sticky="nsew", padx=T.PAD, pady=(T.PAD_Y, 0))
        self._columns(row)
        self._build_hero(row)
        self._build_history(row)

    def _build_hero(self, parent):
        """El protagonista: anillo del día + veredicto + «Ejecutar ahora»."""
        hero = W.card(parent, fg_color=T.HERO, border_color=T.HERO_BORDER)
        hero.grid(row=0, column=0, sticky="nsew")
        hero.grid_columnconfigure(1, weight=1)
        hero.grid_rowconfigure(0, weight=1)
        self.hero = hero
        self.ring = W.Ring(hero, 200, bg=T.HERO)
        W.place_grid(self.ring, row=0, column=0, padx=(T.S3, 0), pady=T.S2)
        hero.bind("<Configure>", lambda _e: self.after_idle(self._fit_ring), add=True)

        box = ctk.CTkFrame(hero, fg_color="transparent")
        W.place_grid(box, row=0, column=1, sticky="ew", padx=(T.S2, T.S5), pady=T.S3)
        box.grid_columnconfigure(0, weight=1)
        self.hero_box = box
        over = ctk.CTkFrame(box, fg_color="transparent")
        over.grid(row=0, column=0, sticky="w")
        self.hero_icon = ctk.CTkLabel(over, text="", width=18, height=18)
        self.hero_icon.pack(side="left", padx=(0, T.S2))
        self.hero_date = W.caption(over, core.today_label(), height=18,
                                   color=T.mix(T.GOLD, T.HERO, 0.78))
        self.hero_date.pack(side="left")
        self.hero_title = W.label(box, "", font="hero")
        self.hero_title.grid(row=1, column=0, sticky="w", pady=(T.S1, 0))
        self.hero_sub = W.label(box, "", font="body", color=T.TEXT_2)
        self.hero_sub.grid(row=2, column=0, sticky="w", pady=(2, 0))
        W.autowrap(self.hero_title, box, 4)
        W.autowrap(self.hero_sub, box, 4)

        stats = ctk.CTkFrame(box, fg_color="transparent")
        W.place_grid(stats, row=3, column=0, sticky="w", pady=(T.S3, 0))
        self.hero_stats = stats
        self.stat_last = W.Stat(stats, "Última corrida")
        self.stat_last.grid(row=0, column=0, sticky="w")
        tkinter.Frame(stats, width=1, bg=T.mix(T.GOLD, T.HERO, 0.16), bd=0,
                      highlightthickness=0).grid(row=0, column=1, sticky="ns",
                                                 padx=T.S4, pady=2)
        self.stat_balance = W.Stat(stats, "Saldo de hoy")
        self.stat_balance.grid(row=0, column=2, sticky="w")

        cta = ctk.CTkFrame(box, fg_color="transparent")
        cta.grid(row=4, column=0, sticky="w", pady=(T.S4, 0))
        run = self._run_action
        self.run_btn = W.make_button(
            cta, core.action_text(run) if run else "Ejecutar ahora",
            (lambda: self._on_action(run)) if run else None, style="primary",
            icon="play", icons=self.icons, height=T.CONTROL_H_LG, width=182,
            font="button_lg", icon_size=18)
        self.run_btn.pack(side="left")
        # Mitad derecha del botón partido: elegir qué parte ejecutar.
        self.run_more_btn = W.make_button(cta, "", self._run_menu, style="primary",
                                          icon="chevron_down", icons=self.icons,
                                          height=T.CONTROL_H_LG, width=40, icon_size=14)
        self.run_more_btn.pack(side="left", padx=(2, 0))
        self.action_buttons += [self.run_btn, self.run_more_btn]
        self.refresh_btn = W.make_button(cta, "Actualizar", self._on_refresh,
                                         style="ghost", icon="refresh",
                                         icons=self.icons, width=116, height=T.CONTROL_H)
        self.refresh_btn.pack(side="left", padx=(T.S2, 0))

    def _build_history(self, parent):
        """Historial: lo ganado cada día, el total del mes y el ritmo."""
        c = W.card(parent)
        c.grid(row=0, column=2, sticky="nsew")
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(2, weight=1)
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.CARD_PAD, pady=(T.S4, 0))
        head.grid_columnconfigure(1, weight=1)
        W.caption(head, f"Puntos · últimos {core.HISTORY_DAYS} días", height=16).grid(
            row=0, column=0, sticky="w")
        # width=1 + sticky "ew": ocupa todo el hueco libre y nunca se recorta
        # por la izquierda al cambiar de texto con el ratón.
        self.hist_detail = W.label(head, "", font="small_strong", color=T.TEXT_2,
                                   anchor="e", height=16, width=1)
        self.hist_detail.grid(row=0, column=1, sticky="ew", padx=(T.S2, 0))

        figs = ctk.CTkFrame(c, fg_color="transparent")
        figs.grid(row=1, column=0, sticky="ew", padx=T.CARD_PAD, pady=(T.S1, 0))
        figs.grid_columnconfigure(1, weight=1)
        total = ctk.CTkFrame(figs, fg_color="transparent")
        total.grid(row=0, column=0, sticky="w")
        self.hist_total = W.label(total, "—", font="figure", height=38)
        self.hist_total.grid(row=0, column=0, sticky="w")
        self.hist_total_sub = W.label(total, "", font="small", color=T.MUTED, height=16)
        self.hist_total_sub.grid(row=1, column=0, sticky="w")
        self.stat_pace = W.Stat(figs, "Ritmo mensual")
        self.stat_pace.grid(row=0, column=2, sticky="se", padx=(0, T.S5))
        self.stat_streak = W.Stat(figs, "Racha")
        self.stat_streak.grid(row=0, column=3, sticky="se")

        self.chart = W.HistoryChart(c, on_hover=self._on_chart_hover,
                                    on_select=self._on_chart_select)
        self.chart.grid(row=2, column=0, sticky="nsew", padx=T.CARD_PAD, pady=(T.S2, 0))
        axis = ctk.CTkFrame(c, fg_color="transparent")
        axis.grid(row=3, column=0, sticky="ew", padx=T.CARD_PAD, pady=(0, T.S3))
        axis.grid_columnconfigure(0, weight=1)
        self.axis_first = W.label(axis, "", font="caption", color=T.MUTED, height=16)
        self.axis_first.grid(row=0, column=0, sticky="w")
        self.axis_last = W.label(axis, "", font="caption", color=T.MUTED, height=16)
        self.axis_last.grid(row=0, column=1, sticky="e")

    def _build_cards(self):
        row = ctk.CTkFrame(self.main, fg_color="transparent")
        row.grid(row=1, column=0, sticky="nsew", padx=T.PAD, pady=(T.GAP, 0))
        self._columns(row)
        self.cards_row = row
        self._build_today_card(row)
        right = ctk.CTkFrame(row, fg_color="transparent")
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure((0, 1), weight=1)
        self._build_app_card(right)
        self._build_task_card(right)

    def _status_card(self, parent, title: str, row: int, pady=0):
        """Tarjeta compacta de estado: medallón a la izquierda; a su derecha,
        el título con su pill y, debajo, una frase y una línea de detalle. El
        contenido va centrado en vertical. Devuelve (tarjeta, cuerpo, cabecera,
        medallón, pill, frase)."""
        c = W.card(parent)
        c.grid(row=row, column=0, sticky="nsew", pady=pady)
        c.grid_columnconfigure(1, weight=1)
        c.grid_rowconfigure((0, 2), weight=1)
        icon = ctk.CTkLabel(c, text="", width=44, height=44)
        icon.grid(row=1, column=0, sticky="n", padx=(T.CARD_PAD, T.S3), pady=(T.S3, T.S3))
        body = ctk.CTkFrame(c, fg_color="transparent")
        body.grid(row=1, column=1, sticky="ew", padx=(0, T.CARD_PAD), pady=(T.S3, T.S3))
        body.grid_columnconfigure(0, weight=1)
        head = ctk.CTkFrame(body, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew")
        head.grid_columnconfigure(0, weight=1)
        W.label(head, title, font="title", height=22).grid(row=0, column=0, sticky="w")
        pill = W.Pill(head, "", "neutral", height=22)
        pill.grid(row=0, column=1, sticky="e")
        line = W.label(body, "", font="small", color=T.TEXT_2)
        line.grid(row=1, column=0, sticky="w", pady=(1, 0))
        W.autowrap(line, body, 4)
        return c, body, head, icon, pill, line

    def _build_today_card(self, parent):
        """Actividades de hoy como fichas (una por actividad web), no como
        lista: icono propio, nombre y estado en palabras."""
        c = W.card(parent)
        c.grid(row=0, column=0, sticky="nsew")
        c.grid_columnconfigure(0, weight=1)
        c.grid_rowconfigure(1, weight=1)
        self.today_card = c
        head = ctk.CTkFrame(c, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.CARD_PAD, pady=(T.S3 + 2, 0))
        head.grid_columnconfigure(1, weight=1)
        self.today_title = W.label(head, "Actividades de hoy", font="title", height=22)
        self.today_title.grid(row=0, column=0, sticky="w")
        self.today_sub = W.label(head, "", font="small", color=T.MUTED, height=22)
        self.today_sub.grid(row=0, column=1, sticky="w", padx=(T.S3, T.S2))
        # Solo mientras se mira otro día del historial.
        self.back_btn = W.make_button(head, "Volver a hoy", self._view_today,
                                      style="tonal", icon="calendar_day",
                                      icons=self.icons, height=24, width=124,
                                      font="small_strong", icon_size=13)
        W.place_grid(self.back_btn, row=0, column=2, sticky="e", padx=(0, T.S2))
        W.set_visible(self.back_btn, False)
        self.today_pill = W.Pill(head, "", "neutral", height=22)
        self.today_pill.grid(row=0, column=3, sticky="e")

        # Estado vacío de un día sin desglose (ocupa el sitio de fichas y sellos).
        empty = ctk.CTkFrame(c, fg_color=T.SURFACE_2, corner_radius=T.R_CONTROL + 3,
                             border_width=1, border_color=T.BORDER)
        W.place_grid(empty, row=1, column=0, rowspan=2, sticky="nsew", padx=T.CARD_PAD,
                     pady=(T.S3, 0))
        empty.grid_columnconfigure(0, weight=1)
        empty.grid_rowconfigure((0, 4), weight=1)
        ctk.CTkLabel(empty, text="", width=48, height=48,
                     image=self.icons.medallion("calendar_day", "muted", 0.0, 48,
                                                T.SURFACE_2, True)).grid(row=1, column=0)
        self.empty_title = W.label(empty, "", font="body_strong", anchor="center")
        self.empty_title.grid(row=2, column=0, pady=(T.S3, 0))
        self.empty_sub = W.label(empty, "", font="small", color=T.MUTED, anchor="center",
                                 justify="center", wraplength=420)
        self.empty_sub.grid(row=3, column=0, pady=(2, 0), padx=T.S4)
        self.day_empty = empty
        W.set_visible(empty, False)

        tiles = ctk.CTkFrame(c, fg_color="transparent")
        W.place_grid(tiles, row=1, column=0, sticky="nsew", padx=T.CARD_PAD,
                     pady=(T.S3, 0))
        self.tiles_frame = tiles
        tiles.grid_rowconfigure(0, weight=1)
        self.tiles: list[W.ActivityTile] = []
        for i in range(4):
            tiles.grid_columnconfigure(i, weight=1, uniform="tiles")
            tile = W.ActivityTile(tiles, self.icons)
            tile.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else T.S2, 0))
            self.tiles.append(tile)

        # Sellos: cada racha de 7 días completada da uno; con todos, el premio.
        strip = ctk.CTkFrame(c, fg_color=T.SURFACE_2, corner_radius=T.R_CONTROL + 3,
                             border_width=1, border_color=T.BORDER, height=42)
        W.place_grid(strip, row=2, column=0, sticky="ew", padx=T.CARD_PAD,
                     pady=(T.S2, 0))
        self.stamps_strip = strip
        strip.grid_propagate(False)
        strip.grid_rowconfigure(0, weight=1)
        strip.grid_columnconfigure(3, weight=1)
        W.caption(strip, "Sellos", height=14).grid(row=0, column=0, padx=(T.S3 + 2, T.S2),
                                                   pady=(2, 0))
        self.stamps_figure = W.Figure(strip, font="stat", drop=2)
        W.place_grid(self.stamps_figure, row=0, column=1, padx=(0, T.S3))
        self.stamps_img = ctk.CTkLabel(strip, text="", width=226, height=15)
        W.place_grid(self.stamps_img, row=0, column=2)
        self.stamps_text = W.label(strip, "", font="small", color=T.TEXT_2, anchor="e",
                                   width=1)
        self.stamps_text.grid(row=0, column=3, sticky="ew", padx=(T.S3, T.S3 + 2))

        # Avisos (reintentos, promociones a mano): una línea discreta al pie.
        self.notes = ctk.CTkFrame(c, fg_color="transparent")
        W.place_grid(self.notes, row=3, column=0, sticky="ew", padx=T.CARD_PAD,
                     pady=(T.S2, 0))
        self.notes.grid_columnconfigure(1, weight=1)
        self.note_rows: list[tuple[ctk.CTkLabel, ctk.CTkLabel]] = []
        for i in range(2):
            ic = ctk.CTkLabel(self.notes, text="", width=14, height=18)
            tx = W.label(self.notes, "", font="small", color=T.MUTED, height=18)
            W.place_grid(ic, row=i, column=0, sticky="nw", padx=(2, T.S2))
            W.place_grid(tx, row=i, column=1, sticky="w")
            W.autowrap(tx, self.notes, 30)
            self.note_rows.append((ic, tx))
        ctk.CTkFrame(c, height=T.S3 + 2, width=1, fg_color="transparent").grid(
            row=4, column=0)

    def _build_app_card(self, parent):
        """App de Bing: estado en una frase y la racha como ciclo de 7 días
        (o la llamada a vincularla)."""
        c, body, _head, self.app_icon, self.app_pill, self.app_line = \
            self._status_card(parent, "App de Bing", 0)
        data = ctk.CTkFrame(body, fg_color="transparent")
        W.place_grid(data, row=2, column=0, sticky="w", pady=(T.S2, 0))
        self.app_data = data
        W.caption(data, "Leer para ganar", height=14).grid(row=0, column=0, sticky="w")
        self.app_read = W.Figure(data, font="stat", drop=2)
        self.app_read.grid(row=1, column=0, sticky="w")
        W.caption(data, "Racha", height=14).grid(row=0, column=1, sticky="w",
                                                 padx=(T.S6, 0))
        streak = ctk.CTkFrame(data, fg_color="transparent")
        streak.grid(row=1, column=1, sticky="w", padx=(T.S6, 0))
        self.app_streak_text = W.label(streak, "—", font="stat", height=21)
        self.app_streak_text.pack(side="left")
        self.app_streak_img = ctk.CTkLabel(streak, text="", width=96, height=16)
        self.app_streak_img.pack(side="left", padx=(T.S3, 0))
        link = core.ACTIONS.get("link_app")
        self.app_link_btn = W.make_button(
            body, "Vincular app de Bing",
            (lambda: self._on_action(link)) if link else None,
            style="tonal", icon="phone", icons=self.icons, height=30)
        W.place_grid(self.app_link_btn, row=3, column=0, sticky="w", pady=(T.S2 + 2, 0))
        self.action_buttons.append(self.app_link_btn)

    def _build_task_card(self, parent):
        """Tarea programada: dos frases. Sin registrar, un botón claro
        «Registrar»; registrada, las acciones se recogen en el menú ⋯."""
        c, body, head, self.task_icon, self.task_pill, self.task_line = \
            self._status_card(parent, "Tarea programada", 1, pady=(T.GAP, 0))
        self.task_more_btn = W.make_button(head, "", self._task_menu, style="ghost",
                                           icon="more", icons=self.icons, width=28,
                                           height=24)
        W.place_grid(self.task_more_btn, row=0, column=2, sticky="e", padx=(T.S1, 0))
        self.task_detail = W.label(body, "", font="small", color=T.MUTED, height=16)
        W.place_grid(self.task_detail, row=2, column=0, sticky="w", pady=(1, 0))
        W.autowrap(self.task_detail, body, 4)
        data = ctk.CTkFrame(body, fg_color="transparent")
        W.place_grid(data, row=4, column=0, sticky="w", pady=(T.S2, 0))
        self.task_data = data
        self.task_next = W.Stat(data, "Próxima")
        self.task_next.grid(row=0, column=0, sticky="w")
        self.task_last = W.Stat(data, "Última")
        self.task_last.grid(row=0, column=1, sticky="w", padx=(T.S6, 0))
        self.task_reg_btn = W.make_button(body, "Registrar tarea",
                                          lambda: self._on_task(True), style="tonal",
                                          icon="calendar", icons=self.icons, height=30)
        W.place_grid(self.task_reg_btn, row=3, column=0, sticky="w", pady=(T.S2 + 2, 0))
        self.action_buttons += [self.task_reg_btn, self.task_more_btn]

    def _popup(self, anchor, entries: list[tuple]):
        """Popover propio bajo `anchor` con [(icono, título, descripción,
        función)…]. Si ya hay uno abierto, lo cierra (el botón hace de
        interruptor)."""
        old = getattr(self, "_popover", None)
        self._popover = None
        if old is not None and old.winfo_exists():
            old.close()
            if getattr(old, "_anchor", None) is anchor:
                return
        self._popover = W.Popover(self, anchor, entries, self.icons)
        self._popover._anchor = anchor

    def _run_menu(self):
        """▾ de «Ejecutar ahora»: todo o solo una parte. Cada opción pasa por
        _on_action, así que conserva el diálogo de primer/segundo plano."""
        entries = []
        for aid, icon, title, desc in (
                ("run_all", "play", "Todo", "Daily set + búsquedas"),
                ("daily", "checklist", "Solo daily set", "Solo las actividades"),
                ("searches", "search", "Solo búsquedas", "Búsquedas de PC e imagen")):
            action = core.ACTIONS.get(aid)
            if action is not None:
                entries.append((icon, title, desc, lambda a=action: self._on_action(a)))
        self._popup(self.run_btn, entries)

    def _on_settings(self):
        """Abre Ajustes y lanza lo que se elija (con sus confirmaciones)."""
        dialog = SettingsDialog(self, enabled=self._actions_on,
                                browser=core.get_browser())
        self.wait_window(dialog)
        result = dialog.result
        if not result:
            return
        kind = result[0]
        if kind == "action":
            self._on_action(core.ACTIONS.get(result[1]))
        elif kind == "switch":
            self._on_switch_account()
        elif kind == "logs":
            self._open_logs()
        elif kind == "uninstall":
            self._on_uninstall()
        elif kind == "browser":
            self._on_browser_change(result[1])

    def _task_menu(self):
        """Menú ⋯ de la tarea: volver a registrarla o quitarla."""
        self._popup(self.task_more_btn, [
            ("calendar", "Volver a registrar", "Reprograma la ejecución diaria",
             lambda: self._on_task(True)),
            ("remove", "Quitar la tarea", "El bot dejará de ejecutarse solo",
             lambda: self._on_task(False))])

    def _build_log_panel(self):
        """Registro en vivo. Plegado por defecto; al abrirlo tapa solo la fila
        de detalle: el anillo y el historial siguen a la vista."""
        panel = W.card(self.main, fg_color=T.LOG_BG)
        W.place_grid(panel, row=1, column=0, sticky="nsew", padx=T.PAD, pady=(T.GAP, 0))
        panel.grid_columnconfigure(0, weight=1)
        panel.grid_rowconfigure(1, weight=1)
        head = ctk.CTkFrame(panel, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=T.CARD_PAD, pady=(T.S3, 0))
        head.grid_columnconfigure(0, weight=1)
        W.label(head, "Registro de actividad", font="title").grid(row=0, column=0, sticky="w")
        W.make_button(head, "Limpiar", self._clear_log, style="ghost", icon="clear",
                      icons=self.icons, height=28, width=92).grid(row=0, column=1, sticky="e")
        self.log = ctk.CTkTextbox(
            panel, font=T.font("mono"), fg_color=T.LOG_BG, text_color=T.TEXT_2,
            wrap="word", state="disabled", corner_radius=0, border_width=0,
            border_spacing=0, activate_scrollbars=True, height=80,
            scrollbar_button_color=T.SURFACE_3, scrollbar_button_hover_color=T.BORDER_STRONG)
        self.log.grid(row=1, column=0, sticky="nsew", padx=(T.S3, T.S2), pady=(T.S2, T.S3))
        for tag, color in (("error", T.tone("error")), ("warn", T.tone("warn")),
                           ("header", T.GOLD), ("cmd", T.FAINT),
                           ("ok", T.tone("ok"))):
            self.log.tag_config(tag, foreground=color)
        self.log_panel = panel
        W.set_visible(panel, False)

    def _build_activity_bar(self):
        bar = W.card(self.main, height=T.BAR_H)
        bar.grid(row=2, column=0, sticky="ew", padx=T.PAD, pady=(T.GAP, T.PAD_Y))
        bar.grid_propagate(False)
        bar.grid_rowconfigure(0, weight=1)
        bar.grid_columnconfigure(2, weight=1)
        self.bar = bar
        self.bar_icon = ctk.CTkLabel(bar, text="", width=24, height=24)
        W.place_grid(self.bar_icon, row=0, column=0, padx=(T.S4, T.S3))
        self.bar_spinner = ctk.CTkProgressBar(
            bar, mode="indeterminate", width=44, height=4, corner_radius=2,
            fg_color=T.SURFACE_3, progress_color=T.GOLD, indeterminate_speed=0.8)
        W.place_grid(self.bar_spinner, row=0, column=0, padx=(T.S4, T.S3))
        W.set_visible(self.bar_spinner, False)
        self.bar_title = W.label(bar, "", font="body_strong")
        self.bar_title.grid(row=0, column=1, sticky="w")
        self.bar_detail = W.label(bar, "", font="mono_small", color=T.MUTED, width=1)
        self.bar_detail.grid(row=0, column=2, sticky="ew", padx=(T.S3, T.S3))
        self.bar_time = W.label(bar, "", font="small_strong", color=T.TEXT_2)
        self.bar_time.grid(row=0, column=3, padx=(0, T.S3))
        # Botón cancelar (oculto hasta que haya un proceso en marcha).
        self.cancel_btn = W.make_button(bar, "Cancelar", self._cancel_proc,
                                        style="danger_outline", icon="cancel",
                                        icons=self.icons, height=30, width=108)
        W.place_grid(self.cancel_btn, row=0, column=4, padx=(0, T.S2))
        W.set_visible(self.cancel_btn, False)
        self.log_toggle = W.make_button(bar, "Ver registro", self._toggle_log,
                                        style="ghost", icon="chevron_up",
                                        icons=self.icons, height=30, width=132)
        self.log_toggle.grid(row=0, column=5, padx=(0, T.S3))
        bar.bind("<Configure>", lambda _e: self._fit_bar_detail(), add=True)
        self._set_bar("idle", "Sin procesos en marcha",
                      "Lo que lances aparecerá aquí")

    # --- Densidad adaptable -------------------------------------------------
    def _on_resize(self, event=None):
        if event is not None and event.widget is not self:
            return
        h = self.winfo_height() / W.window_scale(self)
        density = "compact" if h < T.COMPACT_H else "normal"
        if density != self._density:
            self._apply_density(density)

    def _apply_density(self, density: str):
        """compact (portátiles): menos aire vertical entre bloques."""
        self._density = density
        compact = density == "compact"
        W.regrid(self.hero_stats, pady=(T.S2 if compact else T.S3, 0))

    def _fit_ring(self):
        """El anillo ocupa el alto del protagonista (entre RING_MIN y RING_MAX)
        sin quitarle sitio al texto. Va en pasos de 4 px y siempre por debajo
        del alto de la tarjeta, así que no realimenta el layout."""
        try:
            scale = W.window_scale(self)
            h = self.hero.winfo_height() / scale
            w = self.hero.winfo_width() / scale
        except Exception:
            return
        if h < 50:
            return
        size = min(h - 2 * T.S5, w - T.HERO_TEXT_MIN, T.RING_MAX)
        size = int(max(T.RING_MIN, size)) // 4 * 4
        self.ring.set_size(size)
        # En ventanas anchas el texto no se estira sin fin: el conjunto anillo
        # + texto se centra en la tarjeta (solo cambia el margen horizontal).
        spare = w - size - T.S3 - T.S2 - T.S5 - T.HERO_TEXT_MAX
        pad = int(max(0, spare) / 2) // 4 * 4
        W.regrid(self.ring, padx=(T.S3 + pad, 0))
        W.regrid(self.hero_box, padx=(T.S2, T.S5 + pad))

    # --- Estado ----------------------------------------------------------
    def refresh_status(self, query_task: bool = True):
        """Relee last_run.json (al momento), el historial de los logs y la
        Scheduled Task (estos dos en hilos, para no congelar la UI) y refresca
        el dashboard."""
        self._last_run = core.read_last_run()
        self._render_status()
        if not self._history_busy:
            self._history_busy = True
            threading.Thread(target=self._history_worker, daemon=True).start()
        if not query_task:
            return
        if self._task_busy:
            self._task_again = True
            return
        self._task_busy = True
        threading.Thread(target=self._task_worker, daemon=True).start()

    def _history_worker(self):
        """(hilo) Lee los saldos de los logs y pide a la UI que los pinte."""
        try:
            balances = core.read_log_balances()
        except Exception:
            balances = {}
        self._ui_calls.put(lambda: self._on_history(balances))

    def _on_history(self, balances: dict):
        self._history_busy = False
        self._history = core.points_history(balances)
        self._history_known = True
        self._render_history()
        self._render_goal()
        self._render_ring()

    def _task_worker(self):
        """(hilo) Consulta la tarea y pide a la UI que la pinte."""
        try:
            task = self._query_task()
        except Exception:
            task = {}
        self._ui_calls.put(lambda: self._on_task_result(task))

    def _on_task_result(self, task: dict):
        self._task_busy = False
        self._task = task or {}
        self._task_known = True
        self._render_status()
        if self._task_again:
            self._task_again = False
            self.refresh_status()

    def _periodic_refresh(self):
        """Mantiene el dashboard al día si se deja abierto (la tarea programada
        corre por su cuenta). No hace nada mientras hay un proceso en marcha."""
        if self.proc is None:
            self._refresh_ticks += 1
            self.hero_date.configure(text=core.today_label().upper())
            self.refresh_status(query_task=self._refresh_ticks % _TASK_REFRESH_EVERY == 0)
        self.after(_STATUS_REFRESH_MS, self._periodic_refresh)

    def _render_status(self):
        lr = self._last_run
        today = date.today().isoformat()
        now = datetime.now()
        task = self._task if self._task_known else None

        # Protagonista: veredicto del día.
        s = core.day_summary(lr, task, venv_ok=self._venv_ok, today=today, now=now)
        tone = s["tone"]
        self.hero_icon.configure(image=self.icons.badge(
            _HERO_ICONS.get(s["icon"], "info"), T.tone(tone),
            T.tint(tone, T.HERO, 0.2), 18, 0.5))
        self.hero_title.configure(text=s["title"])
        self.hero_sub.configure(text=s["subtitle"])
        W.set_visible(self.hero_stats, bool(lr))   # recién instalado: sin cifras vacías

        # Cifras: saldo y nivel en la columna de marca; el resto, junto al anillo.
        kpi = {k["key"]: k for k in core.kpi_tiles(lr, today, now)}
        has_points = kpi["points"]["value"] != "—"
        self.balance.configure(text=kpi["points"]["value"],
                               text_color=T.GOLD if has_points else T.FAINT)
        self.balance_unit.configure(text="pts" if has_points else "")
        lvl = kpi["level"]
        known = lvl["value"] != "—"
        coin = W.coin_image(16, lvl["tone"] if lvl["tone"] in ("gold", "silver")
                            else "neutral") if known else None
        W.set_visible(self.level_coin, coin is not None)
        if coin is not None:
            self.level_coin.configure(image=coin)
        self.level_name.configure(text=f"Nivel {lvl['value']}" if known else lvl["sub"],
                                  text_color=T.TEXT if known else T.MUTED)
        self.level_sub.configure(text=f"· {lvl['sub'].split(' ', 1)[-1]}" if known else "")
        last = kpi["last"]
        self.stat_last.set(last["value"], last["sub"],
                           T.TEXT if last["value"] != "—" else T.MUTED)
        gained = kpi["gained"]
        if gained["value"] != "—":
            self.stat_balance.set(gained["sub"])
        else:
            self.stat_balance.set("—", gained["sub"], T.MUTED)
        self._render_ring()
        self._render_goal()

        self._render_detail()

        # Tarea programada: dos frases; «Registrar» solo si no lo está.
        if not self._task_known:
            self.task_pill.set("Comprobando…", "neutral")
            self.task_icon.configure(image=self.icons.medallion("clock", "muted", 0.0, 44,
                                                                T.SURFACE, True))
            self.task_line.configure(text="Consultando el Programador de tareas…")
            W.set_visible(self.task_detail, False)
            W.set_visible(self.task_data, False)
            W.set_visible(self.task_reg_btn, False)
            W.set_visible(self.task_more_btn, False)
        else:
            th = core.task_headline(self._task, now)
            missing = th["state"] == "missing"
            self.task_pill.set(th["label"], th["tone"])
            self.task_icon.configure(image=self.icons.medallion(
                "clock", "warn" if th["tone"] == "warn" else "accent",
                1.0 if th["state"] == "running" else 0.0, 44, T.SURFACE))
            self.task_line.configure(text=th["headline"])
            self.task_detail.configure(text=th["detail"])
            W.set_visible(self.task_detail, bool(th["detail"]))
            W.set_visible(self.task_data, not missing)
            self.task_next.set(th["next"] or "—",
                               color=T.TEXT if th["next"] else T.FAINT)
            self.task_last.set(th["last"] or "—", th["result"],
                               T.TEXT if th["last"] else T.FAINT)
            W.set_visible(self.task_reg_btn, missing)
            W.set_visible(self.task_more_btn, not missing)

    def _render_detail(self):
        """Fila de detalle (actividades, sellos y app de Bing) del día que se
        esté mirando: hoy, o el pulsado en el historial. El de otro día sale de
        su log con la misma forma que last_run, así que se pinta igual; si ese
        día no guardó desglose, se dice con un estado vacío."""
        real_today = date.today()
        view = self._view_day
        viewing = view is not None
        if viewing:
            loaded = view in self._day_cache
            lr = self._day_cache.get(view) or {}
            today, browser, linked = view, lr.get("browser"), bool(lr.get("app"))
        else:
            loaded, lr = True, self._last_run
            today, browser, linked = real_today.isoformat(), None, None
        self.today_title.configure(text=core.day_title(today, real_today))
        W.set_visible(self.back_btn, viewing)
        empty = viewing and not lr
        W.set_visible(self.tiles_frame, not empty)
        W.set_visible(self.stamps_strip, not empty)
        W.set_visible(self.day_empty, empty)
        if empty:
            entry = next((d for d in self._history if d["date"] == view), None)
            title, sub = core.day_empty_text(entry) if loaded else (
                "Leyendo el registro de ese día…", "")
            self.empty_title.configure(text=title)
            self.empty_sub.configure(text=sub)
            self.today_sub.configure(text="")
            self.today_pill.set("Sin desglose" if loaded else "Cargando…", "neutral")
            W.set_visible(self.notes, False)
            self.app_pill.set("Sin datos", "neutral")
            self.app_icon.configure(image=self.icons.medallion("phone", "muted", 0.0, 44,
                                                               T.SURFACE, True))
            self.app_line.configure(text=title, text_color=T.MUTED)
            W.set_visible(self.app_data, False)
            W.set_visible(self.app_link_btn, False)
            return

        # Actividades de hoy: fichas con el estado en palabras.
        streaks = core.activity_streaks(lr)
        tiles = core.activity_tiles(
            core.today_checklist(lr, today=today, browser=browser, linked=linked),
            streaks)
        for widget, tile in zip(self.tiles, tiles):
            widget.set(tile)
        stamps = core.stamps_summary(lr, past=viewing)
        # Sin dato de sellos NO se pintan huecos vacíos (se leerían como
        # "0 sellos"): solo una línea que dice que ese dato no existe.
        known = stamps["known"]
        W.set_visible(self.stamps_figure, known)
        W.set_visible(self.stamps_img, known)
        if known:
            self.stamps_figure.set(stamps["done"], stamps["total"])
            self.stamps_img.configure(image=W.stamps_image(
                stamps["done"], stamps["total"], T.SURFACE_2))
        self.stamps_text.configure(
            text=stamps["text"] if known else stamps["text"].split(": ", 1)[-1].capitalize(),
            text_color=T.TEXT_2 if known else T.MUTED, anchor="e" if known else "w")
        self.today_pill.set(*core.activities_verdict(tiles))
        self.today_sub.configure(text=core.checklist_subtitle(lr, today))
        notes = core.checklist_notes(lr, today)
        W.set_visible(self.notes, bool(notes))    # vacío, un CTkFrame mide 200 px
        for i, (ic, tx) in enumerate(self.note_rows):
            if i < len(notes):
                ntone, text = notes[i]
                warn = ntone == "warn"
                ic.configure(image=self.icons.get(
                    "warning" if warn else "info",
                    T.tone("warn") if warn else T.MUTED, 13))
                tx.configure(text=text, text_color=T.TEXT_2 if warn else T.MUTED)
                W.set_visible(ic, True)
                W.set_visible(tx, True)
            else:
                W.set_visible(ic, False)
                W.set_visible(tx, False)

        # App de Bing: una frase y la racha (o la llamada a vincularla).
        st = core.app_status(lr, linked=linked, today=today,
                             log_app=None if viewing else core.today_app_summary())
        state = st["state"]
        # "Hecho hoy" / "Pendiente hoy" no valen para un día pasado.
        self.app_pill.set(st["label"].replace(" hoy", "") if viewing else st["label"],
                          T.APP_TONES.get(state, "neutral"))
        tone, fraction, dim = {
            "done": ("accent", 1.0, False), "partial": ("accent", 0.5, False),
            "relink": ("error", 0.0, False), "pending": ("accent", 0.0, False),
        }.get(state, ("muted", 0.0, True))
        self.app_icon.configure(image=self.icons.medallion(
            "phone", tone, fraction, 44, T.SURFACE, dim, state == "done"))
        self.app_line.configure(
            text=core.app_headline(st),
            text_color=T.tone("error") if state == "relink" else T.TEXT_2)
        W.set_visible(self.app_data, state in ("done", "partial", "pending"))
        read = st.get("read")
        self.app_read.set(read[0] if read else None, read[1] if read else None)
        # Racha de 7 días de la app: la de la verificación; si falta (corrida
        # antigua), el día de racha que da el propio check-in.
        pair = streaks.get("app")
        if pair is None:
            cycle = core.streak_cycle(st.get("streak_day"))
            pair = (cycle["position"], core.STREAK_CYCLE) if cycle else None
        # Sin dato de racha: se dice, sin segmentos vacíos.
        self.app_streak_text.configure(
            text=f"Día {pair[0]} de {pair[1]}" if pair else "Sin dato",
            text_color=T.TEXT if pair else T.MUTED,
            font=T.font("stat" if pair else "small"))
        self.app_streak_img.configure(
            image=W.streak_image(pair[0], pair[1]) if pair else None)
        if state in ("unlinked", "relink"):
            W.restyle(self.app_link_btn,
                      text="Volver a vincular" if state == "relink" else "Vincular app de Bing")
        # Vincular es cosa de hoy: mirando otro día no se ofrece.
        W.set_visible(self.app_link_btn, not viewing and state in ("unlinked", "relink"))

    def _on_chart_select(self, index: int):
        """Pulsar una barra del historial: ver el desglose de ese día. Pulsar
        hoy, o la misma barra otra vez, vuelve a hoy."""
        h = self._history
        if not h or not 0 <= index < len(h):
            return
        day = h[index]["date"]
        if day == date.today().isoformat() or day == self._view_day:
            self._view_today()
            return
        self._view_day = day
        self.chart.set_selected(index)
        if day not in self._day_cache:
            threading.Thread(target=self._day_worker, args=(day,), daemon=True).start()
        self._render_detail()
        self._on_chart_hover(None)

    def _view_today(self):
        self._view_day = None
        self.chart.set_selected(None)
        self._render_detail()
        self._on_chart_hover(None)

    def _day_worker(self, day: str):
        """(hilo) Lee el desglose de un día de su log y pide a la UI pintarlo."""
        try:
            data = core.read_day_detail(day)
        except Exception:
            data = None
        self._ui_calls.put(lambda: self._on_day(day, data))

    def _on_day(self, day: str, data):
        self._day_cache[day] = data
        if self._view_day == day:
            self._render_detail()

    def _render_ring(self):
        """El anillo: un tramo por actividad aplicable y, en el centro, los
        puntos ganados hoy (el mayor entre la última sesión y lo que suman los
        logs del día, que también cuentan sesiones anteriores)."""
        lr = self._last_run
        today = date.today().isoformat()
        items = [i for i in core.today_checklist(lr, today=today) if i["state"] != "na"]
        fractions = tuple(1.0 if i["state"] == "ok" else float(i.get("progress") or 0.0)
                          for i in items)
        done = sum(1 for i in items if i["state"] == "ok")
        ran = core.ran_today(lr, today)
        value = None
        pa, pb = lr.get("points_after"), lr.get("points_before")
        if ran and type(pa) is int and type(pb) is int:
            value = pa - pb
        if self._history and self._history[-1]["date"] == today:
            logged = self._history[-1]["gained"]
            if logged is not None and (ran or logged > 0):
                value = max(value or 0, logged)
        if not lr:
            sub = "sin datos todavía"
        elif not ran:
            sub = "sin corrida hoy"
        else:
            sub = f"{done} de {len(items)} hechas"
        animate = not self._ring_seen and (value is not None or any(fractions))
        if animate:
            self._ring_seen = True
        self.ring.show(fractions, value, sub, "GANADOS HOY" if value is not None else "",
                       fmt=core.format_delta, animate=animate)

    def _render_history(self):
        h = self._history
        summary = core.history_summary(h)
        if summary["has_data"]:
            self.hist_total.configure(text=core.format_points(summary["month_total"]),
                                      text_color=T.TEXT)
            self.hist_total_sub.configure(text=f"pts en {summary['month_label']}")
            self.stat_pace.set("≈ " + core.format_points(summary["pace"]))
            best = summary["best"]
            texts = {"average": f"{core.format_points(summary['average'])} pts",
                     "best": (f"{core.format_delta(best['gained'])} · "
                              f"{core.short_date(best['date'])}") if best else "—",
                     "days": f"{summary['days']} de {len(h)}"}
            for key, lbl in self.rail_stats.items():
                lbl.configure(text=texts[key], text_color=T.TEXT)
            self._pace = summary["pace"]
            n = summary["streak"]
            self.stat_streak.set(f"{n} día" if n == 1 else f"{n} días",
                                 color=T.TEXT if n else T.MUTED)
        else:
            self.hist_total.configure(text="—", text_color=T.FAINT)
            self.hist_total_sub.configure(text="Sin datos todavía")
            self.stat_pace.set("—", color=T.FAINT)
            for lbl in self.rail_stats.values():
                lbl.configure(text="—", text_color=T.FAINT)
            self._pace = 0
            self.stat_streak.set("—", color=T.FAINT)
        self.chart.set(
            tuple((d["gained"], d["bonus"],
                   core.format_delta(d["gained"]) if d["bonus"] else "") for d in h),
            empty=("Aquí verás tus puntos, día a día",
                   "Se rellena solo a partir de la primera corrida"))
        self.axis_first.configure(
            text=core.short_date(h[0]["date"]).upper() if h and summary["has_data"] else "")
        self.axis_last.configure(text="HOY" if summary["has_data"] else "")
        self.chart.set_selected(self._selected_index())
        self._on_chart_hover(None)

    def _selected_index(self) -> int | None:
        """Posición en el historial del día que se está mirando (None = hoy)."""
        return next((i for i, d in enumerate(self._history)
                     if d["date"] == self._view_day), None)

    def _on_chart_hover(self, index: int | None):
        """Día bajo el ratón en el historial; en reposo, el día elegido (o hoy)."""
        h = self._history
        if not h or not any(d["gained"] is not None for d in h):
            self.hist_detail.configure(text="")
            return
        hot = index is not None and 0 <= index < len(h)
        chosen = self._selected_index()
        entry = h[index] if hot else h[chosen] if chosen is not None else h[-1]
        self.hist_detail.configure(
            text=core.history_day_text(entry),
            text_color=T.GOLD_HI if hot else T.TEXT if chosen is not None else T.TEXT_2)

    def _on_refresh(self):
        self.refresh_status()
        W.restyle(self.refresh_btn, text="Actualizado", icon="check")
        self.after(1400, lambda: W.restyle(self.refresh_btn, text="Actualizar",
                                           icon="refresh"))

    def _on_browser_change(self, value: str):
        """Persiste MSR_BROWSER al cambiar el selector Chrome/Edge."""
        browser = "edge" if value == "Edge" else "chrome"
        # Sin cambio real (o re-selección del mismo): no hacer nada.
        if browser == core.get_browser():
            return
        # No pisar una corrida en curso (Ajustes ya lo bloquea; por si acaso).
        if self.proc is not None:
            self._info("Ocupado", "Hay un proceso en marcha. Cámbialo cuando termine.")
            return
        # Actualizar el entorno del propio panel: las corridas manuales de esta
        # sesión ya usarán el nuevo navegador (build_env_with_browser lo hereda).
        os.environ["MSR_BROWSER"] = browser
        self._info("Navegador cambiado",
                   f"El bot usará {value} a partir de ahora.\n\n"
                   "Ojo: puede que aún no tengas la sesión iniciada en ese navegador. "
                   "Usa «Login manual» o «Cambiar cuenta» para iniciarla.")
        # Persistir en HKCU\Environment (futuras corridas + tarea programada).
        self._launch(core.build_browser_env_command(browser),
                     title=f"Navegador → {value}")
        self._render_status()

    def _auto_update(self):
        """Al arrancar: comprueba/aplica actualizaciones (git pull) una vez.

        Reutiliza _launch (hilo + cola), así que no congela la UI. Degrada con
        gracia vía core.build_update_command (sin red / no es repo / sin versión
        nueva no falla). Si ya hay un proceso en marcha, no hace nada.
        """
        if self.proc is None and self._venv_ok:
            # Al terminar el git pull, comprobar si hay un .exe más nuevo.
            self._launch(core.build_update_command(),
                         title="Buscar actualizaciones",
                         on_done=self._maybe_self_update)

    def _query_task(self) -> dict:
        """Consulta el estado de la Scheduled Task (salida pequeña). Se llama
        desde un hilo: no toca Tk."""
        try:
            out = subprocess.run(
                core.build_task_query_command(),
                capture_output=True, text=True,
                encoding="utf-8", errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW,
                timeout=15,
            )
            return core.parse_task_query(out.stdout)
        except Exception:
            return {}

    # --- Diálogos sencillos ------------------------------------------------
    def _ask(self, title: str, message: str, ok_text: str = "Aceptar",
             danger: bool = False, icon: str | None = None) -> bool:
        dialog = W.MessageDialog(self, title, message, kind="confirm",
                                 ok_text=ok_text, danger=danger, icon=icon)
        self.wait_window(dialog)
        return dialog.result is True

    def _info(self, title: str, message: str, kind: str = "info"):
        dialog = W.MessageDialog(self, title, message, kind=kind)
        self.wait_window(dialog)

    # --- Handlers de acciones -------------------------------------------
    def _on_action(self, action: core.Action | None):
        if action is None:
            return
        text = core.action_text(action)
        if action.confirm:
            if not self._ask(f"¿{text}?", action.description, ok_text=text):
                return
        hidden = False
        if action.prompt_visibility:
            dialog = VisibilityDialog(self, text)
            self.wait_window(dialog)
            if dialog.result is None:
                return  # canceló
            hidden = dialog.result
        self._launch(core.build_run_command(action.id, hidden=hidden),
                     title=text, env=core.build_env_with_browser())

    def _on_switch_account(self):
        # Ya no se piden ni se guardan credenciales: switch_account.py --browser
        # borra la sesión y abre el navegador visible para el login a mano.
        if not self._ask(
                "Cambiar cuenta",
                "Se cerrará la sesión actual y se abrirá el navegador para que "
                "inicies sesión con la cuenta nueva (incluido el 2FA). ¿Continuar?",
                ok_text="Cambiar cuenta", icon="switch_user"):
            return
        # Forzamos MSR_BROWSER para que el login abra el navegador elegido.
        self._launch(core.build_switch_command(), title="Cambiar cuenta",
                     env=core.build_env_with_browser())

    def _on_task(self, install: bool):
        title = "Registrar tarea" if install else "Quitar tarea"
        self._launch(core.build_task_command(install), title=title)

    def _on_uninstall(self):
        dialog = UninstallDialog(self)
        self.wait_window(dialog)
        if dialog.result is None:
            return
        opciones = dialog.result
        if not opciones:
            self._info("Nada seleccionado",
                       "No marcaste ninguna opción, así que no se borrará nada.")
            return
        # Confirmación final listando exactamente lo que se va a borrar.
        etiquetas = {key: label for key, label, _ in core.UNINSTALL_OPTIONS}
        listado = "\n".join(f"•  {etiquetas.get(o, o)}" for o in opciones)
        if not self._ask("Confirmar desinstalación",
                         "Se va a eliminar de forma permanente:\n\n"
                         f"{listado}\n\n¿Continuar?",
                         ok_text="Eliminar", danger=True):
            return
        self._launch(core.build_uninstall_command(opciones), title="Desinstalar")

    def _open_logs(self):
        try:
            core.LOG_DIR.mkdir(parents=True, exist_ok=True)
            os.startfile(str(core.LOG_DIR))  # type: ignore[attr-defined]
        except Exception as exc:
            self._info("Error", f"No se pudo abrir la carpeta:\n{exc}", kind="error")

    # --- Ejecución de subprocesos ---------------------------------------
    def _launch(self, cmd: list[str], title: str, stdin_data: str | None = None,
                on_done=None, env: dict[str, str] | None = None):
        """Lanza un subproceso en segundo plano y vuelca su salida al log.

        `on_done` (opcional) se llama en el hilo de UI cuando el proceso termina.
        `env` (opcional) reemplaza el entorno del subproceso; se usa para forzar
        MSR_BROWSER en las corridas del bot sin depender del broadcast del
        registro (que no toca el os.environ ya heredado por procesos vivos).
        """
        if self.proc is not None:
            self._info("Ocupado", "Ya hay un proceso en marcha. Espera o cancélalo.")
            return

        self._on_done_cb = on_done
        self._set_actions_enabled(False)
        self._append_lines([f"\n$ {' '.join(cmd)}\n", f"--- {title} ---\n"])

        try:
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE if stdin_data is not None else None,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(core.REWARDS_DIR),
                creationflags=subprocess.CREATE_NO_WINDOW,
                bufsize=1,
                env=env,
            )
        except Exception as exc:
            self._append_lines([f"[ERROR] No se pudo lanzar el proceso: {exc}\n"])
            self.proc = None
            self._set_actions_enabled(True)
            self._set_bar("error", f"{title} · no se pudo lanzar", str(exc))
            self.refresh_status()
            self._info("Error", f"No se pudo lanzar el proceso:\n{exc}", kind="error")
            return

        self._start_running(title)
        # Hilo lector: vuelca stdin (si lo hay) y luego lee línea a línea.
        threading.Thread(target=self._reader, args=(self.proc, stdin_data),
                         daemon=True).start()

    def _reader(self, proc: subprocess.Popen, stdin_data: str | None):
        """Hilo daemon: escribe stdin y empuja la salida a la cola (no toca Tk)."""
        try:
            if stdin_data is not None and proc.stdin is not None:
                try:
                    proc.stdin.write(stdin_data)
                    proc.stdin.flush()
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.stdout is not None:
                for line in proc.stdout:
                    self.out_queue.put(line)
            proc.wait()
        except Exception as exc:
            self.out_queue.put(f"[ERROR lector] {exc}\n")
        finally:
            # Centinela: avisa a la UI de que el proceso terminó.
            self.out_queue.put(None)

    def _poll_queue(self):
        """Vacía las colas en el hilo de la UI cada 100 ms (en bloque)."""
        lines: list[str] = []
        done = False
        try:
            while len(lines) < 500:
                item = self.out_queue.get_nowait()
                if item is None:
                    done = True
                    break
                lines.append(item)
        except queue.Empty:
            pass
        if lines:
            self._append_lines(lines)
        if done:
            self._on_proc_done()
        try:
            while True:
                fn = self._ui_calls.get_nowait()
                try:
                    fn()
                except Exception:
                    pass
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    def _on_proc_done(self):
        """Se ejecuta en la UI cuando el centinela llega: limpia y refresca."""
        code = self.proc.returncode if self.proc is not None else "?"
        self._append_lines([f"--- proceso finalizado (código {code}) ---\n"])
        self.proc = None
        self._stop_running(code)
        self._set_actions_enabled(True)
        self.refresh_status()
        cb, self._on_done_cb = self._on_done_cb, None
        if cb is not None:
            try:
                cb()
            except Exception:
                pass

    # --- Barra de actividad ------------------------------------------------
    def _start_running(self, title: str):
        self._proc_title = title
        self._proc_started = time.monotonic()
        self._cancelled = False
        self._last_line = ""
        self._set_bar("running", title, "Iniciando…")
        W.restyle(self.run_btn, text="En marcha…")
        W.set_visible(self.cancel_btn, True)
        self._tick_elapsed()

    def _stop_running(self, code):
        W.set_visible(self.cancel_btn, False)
        W.restyle(self.run_btn, text=core.action_text(self._run_action)
                  if self._run_action else "Ejecutar ahora")
        title = self._proc_title or "Proceso"
        elapsed = core.format_elapsed(time.monotonic() - self._proc_started)
        if self._cancelled:
            self._set_bar("warn", f"{title} · cancelado", self._last_line, elapsed)
        elif code == 0:
            self._set_bar("ok", f"{title} · terminado", self._last_line, elapsed)
        else:
            self._set_bar("error", f"{title} · falló (código {code})",
                          self._last_line, elapsed)

    def _tick_elapsed(self):
        """Cronómetro de la barra mientras hay algo en marcha (1 vez/s)."""
        if self._bar_state != "running":
            return
        self.bar_time.configure(
            text=core.format_elapsed(time.monotonic() - self._proc_started))
        self.after(1000, self._tick_elapsed)

    def _set_bar(self, state: str, title: str, detail: str = "", time_text: str = ""):
        """Pinta la barra inferior: idle | running | ok | warn | error."""
        self._bar_state = state
        running = state == "running"
        self.bar.configure(
            fg_color=T.mix(T.GOLD, T.SURFACE, 0.08) if running else T.SURFACE,
            border_color=T.mix(T.GOLD, T.SURFACE, 0.45) if running else T.BORDER)
        self.bar_spinner.configure(
            fg_color=T.mix(T.GOLD, T.SURFACE, 0.2) if running else T.SURFACE_3)
        if running:
            W.set_visible(self.bar_icon, False)
            W.set_visible(self.bar_spinner, True)
            self.bar_spinner.start()
        else:
            try:
                self.bar_spinner.stop()
            except Exception:
                pass
            W.set_visible(self.bar_spinner, False)
            glyph, tone = {"ok": ("check", "ok"), "warn": ("warning", "warn"),
                           "error": ("cancel", "error")}.get(state, ("dot", "muted"))
            size = 0.32 if glyph == "dot" else 0.5
            self.bar_icon.configure(image=self.icons.badge(
                glyph, T.tone(tone), T.tint(tone, T.SURFACE, 0.18), 24, size))
            W.set_visible(self.bar_icon, True)
        self.bar_title.configure(text=title)
        self._bar_detail_full = detail or ""
        # Las líneas del log van en monoespaciada; la pista en reposo, normal.
        self._bar_detail_font = "small" if state == "idle" else "mono_small"
        self.bar_detail.configure(font=T.font(self._bar_detail_font))
        self.bar_time.configure(text=time_text)
        self._fit_bar_detail()

    def _fit_bar_detail(self):
        """Recorta la última línea del log al hueco disponible (con '…')."""
        text = getattr(self, "_bar_detail_full", "")
        scale = W.window_scale(self)
        try:
            # Ancho que grid le da a la columna del detalle (menos su padding).
            avail = self.bar.grid_bbox(2, 0)[2] - round((2 * T.S3 + 4) * scale)
        except Exception:
            avail = 400
        font = T.font(getattr(self, "_bar_detail_font", "mono_small"))
        self.bar_detail.configure(text=W.fit_text(text, font, avail, scale))

    # --- Registro -----------------------------------------------------------
    def _append_lines(self, lines: list[str]):
        """Añade líneas al log, coloreadas por tipo; auto-scroll solo si ya
        estabas abajo del todo. Siempre desde el hilo de la UI."""
        if not lines:
            return
        try:
            at_bottom = self.log.yview()[1] >= 0.999
        except Exception:
            at_bottom = True
        self.log.configure(state="normal")
        for line in lines:
            kind = core.log_line_kind(line)
            self.log.insert("end", line, kind if kind != "normal" else None)
        end_line = int(self.log.index("end-1c").split(".")[0])
        if end_line > _LOG_MAX_LINES:
            self.log.delete("1.0", f"{end_line - _LOG_MAX_LINES + 500}.0")
        if at_bottom:
            self.log.see("end")
        self.log.configure(state="disabled")
        # Última línea útil para la barra de actividad.
        for line in reversed(lines):
            text = line.strip()
            if text and not text.startswith(("$ ", "---")):
                self._last_line = core.clean_log_line(text)
                if self._bar_state == "running":
                    self._bar_detail_full = self._last_line
                    self._fit_bar_detail()
                break

    def _append_log(self, text: str):
        """Compatibilidad: añade texto al log (solo desde el hilo de UI)."""
        self._append_lines([text])

    def _clear_log(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _toggle_log(self, open_: bool | None = None):
        """Abre/cierra el registro sobre la fila de detalle. Las tarjetas
        siguen debajo (solo se tapan), así el anillo y el historial no se
        mueven ni cambian de tamaño."""
        self._log_open = (not self._log_open) if open_ is None else bool(open_)
        if self._log_open:
            W.set_visible(self.log_panel, True)
            self.log_panel.lift()
            self.log.see("end")
            W.restyle(self.log_toggle, text="Ocultar registro", icon="chevron_down")
        else:
            W.set_visible(self.log_panel, False)
            W.restyle(self.log_toggle, text="Ver registro", icon="chevron_up")
        self.after_idle(self._fit_bar_detail)

    # --- Auto-actualización del propio .exe ------------------------------
    def _maybe_self_update(self):
        """Tras el git pull: si hay un .exe más nuevo, descárgalo y reinicia."""
        if not core.self_update_available():
            return
        self._append_lines([
            f"\n--- Nueva versión del panel disponible "
            f"(v{core.available_version()}); descargando… ---\n"])
        self._set_actions_enabled(False)
        self._proc_title = "Actualizando el panel"
        self._proc_started = time.monotonic()
        self._set_bar("running", "Actualizando el panel", "Descargando la nueva versión…")
        self._tick_elapsed()
        threading.Thread(target=self._do_self_update, daemon=True).start()

    def _do_self_update(self):
        """(hilo) Descarga el nuevo .exe, lo intercambia y relanza. No toca Tk:
        pide a la UI lo que haga falta a través de las colas."""
        new = core.current_exe().with_name("MsRewardsPanel.new.exe")
        try:
            core.download_panel_exe(new)
            core.swap_exe(new)
        except Exception as exc:
            self.out_queue.put(f"[WARN] auto-actualización fallida: {exc}\n")
            self._ui_calls.put(self._self_update_failed)
            return
        self.out_queue.put("--- Panel actualizado. Reiniciando… ---\n")
        self._ui_calls.put(lambda: self.after(400, self._relaunch_and_exit))

    def _self_update_failed(self):
        self._set_bar("warn", "No se pudo actualizar el panel", self._last_line)
        self._set_actions_enabled(True)

    def _relaunch_and_exit(self):
        """Lanza el .exe nuevo (ya en su sitio) y cierra este proceso."""
        try:
            subprocess.Popen([str(core.current_exe())],
                             cwd=str(core.current_exe().parent),
                             creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def _cancel_proc(self):
        """Termina el proceso en marcha (proc.terminate)."""
        if self.proc is not None:
            try:
                self.proc.terminate()
                self._cancelled = True
                self._append_lines(["--- cancelando… ---\n"])
                self.bar_title.configure(text=f"{self._proc_title} · cancelando…")
            except Exception as exc:
                self._append_lines([f"[ERROR] al cancelar: {exc}\n"])

    def _ask_access(self):
        """Equipo sin activar: pide el código; sin él, el panel no se abre."""
        access_ui.ask_code(self, on_ok=self._on_access_ok, on_cancel=self._on_close)

    def _on_access_ok(self):
        try:
            access.activate(core.STATE_DIR)
        except Exception as exc:
            from tkinter import messagebox  # noqa: PLC0415
            messagebox.showerror("Jab-rewards", f"No se pudo guardar la activación:\n{exc}")
            self._on_close()
            return
        self._activated = True
        self._after_splash()

    def _after_splash(self):
        """Reaparece la ventana principal cuando el splash termina."""
        if not getattr(self, "_activated", True):
            self._ask_access()
            return
        self.ring.replay()        # la animación de apertura, ya con la ventana visible
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def _on_close(self):
        """Cierra la ventana terminando antes cualquier subproceso vivo."""
        if self.proc is not None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.destroy()

    # --- Helpers de UI ---------------------------------------------------
    def _set_actions_enabled(self, enabled: bool):
        """Habilita/deshabilita los botones de acción (respeta falta de venv)."""
        if not self._venv_ok:
            enabled = False  # sin venv las acciones siguen bloqueadas
        for b in self.action_buttons:
            W.set_enabled(b, enabled)
        # Ajustes lo consulta al abrirse: con un proceso en marcha (o sin venv)
        # sus acciones y el selector de navegador salen deshabilitados.
        self._actions_on = enabled


if __name__ == "__main__":
    App().mainloop()
