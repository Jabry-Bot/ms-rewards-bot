"""
Componentes reutilizables del panel (customtkinter + Pillow).

Todo el estilo sale de panel/theme.py y los gráficos de panel/art.py. Aquí
viven los ladrillos de la interfaz: iconos (glifos de Segoe Fluent Icons
renderizados con Pillow), botones con estilos y estado deshabilitado cuidado,
tarjetas, pills, filas del checklist, el anillo del día, el historial de
barras y la base común de los diálogos modales.
"""
from __future__ import annotations

import os
import tkinter
from pathlib import Path
from typing import Callable

import customtkinter as ctk

try:
    from panel import art, core, theme as T
except ImportError:  # .exe / ejecución suelta
    import art
    import core
    import theme as T

try:
    from PIL import Image, ImageDraw, ImageFont
except Exception:  # pragma: no cover - Pillow viene con customtkinter
    Image = ImageDraw = ImageFont = None


# --- Iconos ----------------------------------------------------------------
# Códigos de Segoe Fluent Icons (Win 11), compatibles con Segoe MDL2 Assets
# (Win 10). Si no hay ninguna de las dos fuentes, los botones van sin icono.
GLYPHS: dict[str, str] = {
    "play": "\ue768", "refresh": "\ue72c", "search": "\ue721",
    "checklist": "\ue9d5", "key": "\ue8d7", "phone": "\ue8ea", "stop": "\ue71a",
    "switch_user": "\ue748", "folder": "\ue838", "delete": "\ue74d",
    "calendar": "\ue787", "calendar_day": "\ue8bf", "globe": "\ue774",
    "check": "\ue73e", "cancel": "\ue711", "warning": "\ue7ba", "error": "\ue783",
    "info": "\ue946", "clock": "\ue823", "remove": "\ue738",
    "chevron_up": "\ue70e", "chevron_down": "\ue70d", "photo": "\ue91b",
    "timer": "\ue916", "book": "\ue82d", "monitor": "\ue7f4", "moon": "\ue708",
    "power": "\ue7e8",
    "clear": "\ue894", "dot": "\uea3b", "more": "\ue712", "flag": "\ue7c1", "calculator": "\ue8ef", "settings": "\ue713",
}

# Icono de cada acción de core.ACTIONS.
ACTION_ICONS = {"run_all": "play", "daily": "checklist", "searches": "search",
                "login": "key", "link_app": "phone", "kill": "power"}

# Icono de cada fila del checklist "Hoy" y de la tarjeta de la app.
CHECK_ICONS = {"pc_search": "search", "daily_set": "checklist", "visual": "photo",
               "edge": "timer", "app": "phone", "checkin": "calendar_day",
               "read": "book"}

# Estado de fila → (glifo de la insignia, tono).
STATE_BADGES = {"ok": ("check", "ok"), "todo": ("dot", "warn"),
                "pending": ("clock", "muted"), "na": ("remove", "muted"),
                "unknown": ("remove", "muted")}


def _icon_font_path() -> str | None:
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
        p = fonts / name
        if p.exists():
            return str(p)
    return None


class Icons:
    """Fábrica (con caché) de iconos CTkImage a partir de glifos.

    Se renderizan a 2× y CTkImage los reescala según el DPI; así se ven
    nítidos al 100 %, 125 % y 150 %.
    """

    def __init__(self):
        self._path = _icon_font_path() if Image is not None else None
        self._cache: dict[tuple, ctk.CTkImage] = {}
        self._fonts: dict[int, object] = {}

    def _font(self, px: int):
        if px not in self._fonts:
            self._fonts[px] = ImageFont.truetype(self._path, px)
        return self._fonts[px]

    def get(self, name: str, color: str, size: int = 16) -> ctk.CTkImage | None:
        """Glifo `name` en `color`, cuadrado de `size` px lógicos."""
        if not self._path or name not in GLYPHS:
            return None
        key = ("g", name, color, size)
        if key not in self._cache:
            try:
                px = size * 2
                img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
                ImageDraw.Draw(img).text((px / 2, px / 2), GLYPHS[name],
                                         font=self._font(px), fill=color, anchor="mm")
                self._cache[key] = ctk.CTkImage(img, img, size=(size, size))
            except Exception:
                return None
        return self._cache[key]

    def medallion(self, name: str, tone: str, fraction: float = 0.0, size: int = 44,
                  base: str = T.SURFACE, dim: bool = False,
                  done: bool = False) -> ctk.CTkImage | None:
        """Icono `name` en un disco con arco de progreso. Con tono "accent"
        va en el estilo de marca (degradado cian→azul con halo); `done` añade
        la insignia verde pequeña de "hecho"."""
        if not art.available:
            return None
        key = ("m", name, tone, round(float(fraction), 3), size, base, dim, done)
        if key not in self._cache:
            try:
                img = art.medallion(size * 2, GLYPHS.get(name, ""), self._path,
                                    T.tone(tone), fraction=fraction, dim=dim, bg=base,
                                    brand=tone == "accent",
                                    check=GLYPHS["check"] if done else "")
                self._cache[key] = ctk.CTkImage(img, img, size=(size, size))
            except Exception:
                return None
        return self._cache[key]

    def badge(self, name: str, fg: str, bg: str, size: int = 22,
              glyph: float = 0.5) -> ctk.CTkImage | None:
        """Círculo relleno `bg` con el glifo `name` en `fg` (insignia de estado)."""
        if Image is None:
            return None
        key = ("b", name, fg, bg, size, glyph)
        if key not in self._cache:
            try:
                ss = 4                                   # supermuestreo del círculo
                big = size * 2 * ss
                img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
                ImageDraw.Draw(img).ellipse((0, 0, big - 1, big - 1), fill=bg)
                img = img.resize((size * 2, size * 2), Image.LANCZOS)
                if self._path and name in GLYPHS:
                    px = size * 2
                    ImageDraw.Draw(img).text(
                        (px / 2, px / 2), GLYPHS[name],
                        font=self._font(max(8, round(px * glyph))), fill=fg, anchor="mm")
                self._cache[key] = ctk.CTkImage(img, img, size=(size, size))
            except Exception:
                return None
        return self._cache[key]


def load_logo(size: int) -> ctk.CTkImage | None:
    """Logo de la app (assets/logo.png) como CTkImage cuadrado de `size` px."""
    path = core.asset_path("logo.png")
    if path is None or Image is None:
        return None
    try:
        img = Image.open(path).convert("RGBA")
        img = img.resize((size * 2, size * 2), Image.LANCZOS)
        return ctk.CTkImage(img, img, size=(size, size))
    except Exception:
        return None


def coin_image(size: int, metal: str) -> ctk.CTkImage | None:
    """Moneda del nivel (art.coin) como CTkImage de `size` px lógicos."""
    if not art.available:
        return None
    try:
        img = art.coin(size * 2, metal)
        return ctk.CTkImage(img, img, size=(size, size))
    except Exception:
        return None


# --- Botones -----------------------------------------------------------------
# Estilo → colores normal / deshabilitado. "ghost" y "nav" son transparentes
# (heredan el fondo del contenedor); "nav" va alineado a la izquierda.
BUTTON_STYLES: dict[str, dict] = {
    # El oro macizo se reserva para LA acción («Ejecutar ahora» y el botón
    # que confirma un diálogo). "tonal" es su versión contenida.
    "primary": dict(fg=T.ACCENT, hover=T.ACCENT_HOVER, text=T.ON_ACCENT, border=None,
                    fg_off=T.ACCENT_DISABLED, text_off=T.FAINT, icon=T.ON_GOLD),
    "tonal": dict(fg=T.mix(T.GOLD, T.SURFACE, 0.12), hover=T.mix(T.GOLD, T.SURFACE, 0.22),
                  text=T.GOLD, border=T.mix(T.GOLD, T.SURFACE, 0.36),
                  fg_off=T.SURFACE_2, text_off=T.FAINT, icon=T.GOLD),
    "secondary": dict(fg=T.SURFACE_2, hover=T.SURFACE_3, text=T.TEXT,
                      border=T.BORDER_STRONG, fg_off=T.SURFACE_2, text_off=T.FAINT,
                      icon=T.TEXT_2),
    "ghost": dict(fg="transparent", hover=T.SURFACE_2, text=T.TEXT_2, border=None,
                  fg_off="transparent", text_off=T.FAINT, icon=T.TEXT_2),
    "nav": dict(fg="transparent", hover=T.HOVER_NAV, text=T.TEXT_2, border=None,
                fg_off="transparent", text_off=T.FAINT, icon=T.MUTED,
                text_hover=T.TEXT, icon_hover=T.GOLD),
    "danger": dict(fg=T.DANGER, hover=T.DANGER_HOVER, text=T.ON_DANGER, border=None,
                   fg_off=T.mix(T.DANGER, T.SURFACE, 0.25), text_off=T.FAINT,
                   icon=T.ON_DANGER),
    # Desinstalar: apagado hasta que pasas el ratón por encima.
    "quiet_danger": dict(fg="transparent", hover=T.tint("error", T.RAIL, 0.12),
                         text=T.MUTED, border=None, fg_off="transparent",
                         text_off=T.FAINT, icon=T.MUTED,
                         text_hover=T.tone("error"), icon_hover=T.tone("error")),
    "danger_outline": dict(fg="transparent", hover=T.tint("error", T.SURFACE, 0.16),
                           text=T.tone("error"), border=T.mix(T.tone("error"), T.SURFACE, 0.45),
                           fg_off="transparent", text_off=T.FAINT, icon=T.tone("error")),
}


def make_button(parent, text: str, command: Callable | None = None, *,
                style: str = "secondary", icon: str | None = None,
                icons: Icons | None = None, height: int = T.CONTROL_H,
                width: int = 0, font: str = "button", anchor: str = "center",
                icon_size: int = 16) -> ctk.CTkButton:
    """Botón con estilo del sistema de diseño. Guarda su estilo para poder
    deshabilitarlo/rehabilitarlo con set_enabled() sin perder colores."""
    btn = ctk.CTkButton(parent, text=text, command=command, height=height,
                        width=width or 0, corner_radius=T.R_CONTROL,
                        font=T.font(font), anchor=anchor, compound="left",
                        border_spacing=T.S1 if anchor == "w" else 2)
    btn._msr = {"style": style, "icon": icon, "icons": icons, "size": icon_size,
                "enabled": True, "hot": False}
    _apply_style(btn)
    btn.bind("<Enter>", lambda _e, b=btn: _hover(b, True), add="+")
    btn.bind("<Leave>", lambda _e, b=btn: _hover(b, False), add="+")
    return btn


def _hover(btn: ctk.CTkButton, hot: bool) -> None:
    """Texto/icono que se encienden al pasar el ratón (estilos con *_hover)."""
    meta = btn._msr
    if meta["hot"] == hot:
        return
    meta["hot"] = hot
    if "text_hover" in BUTTON_STYLES[meta["style"]] and meta["enabled"]:
        _apply_style(btn)


def _apply_style(btn: ctk.CTkButton) -> None:
    meta = btn._msr
    st = BUTTON_STYLES[meta["style"]]
    on = meta["enabled"]
    hot = on and meta.get("hot") and "text_hover" in st
    kw = dict(fg_color=st["fg"] if on else st["fg_off"], hover_color=st["hover"],
              text_color=st["text_hover"] if hot else st["text"],
              text_color_disabled=st["text_off"],
              border_width=1 if st["border"] else 0,
              border_color=st["border"] or T.BORDER,
              state="normal" if on else "disabled")
    icons = meta["icons"]
    if icons is not None and meta["icon"]:
        color = st["icon_hover"] if hot else st["icon"] if on else st["text_off"]
        img = icons.get(meta["icon"], color, meta["size"])
        if img is not None:
            kw["image"] = img
    btn.configure(**kw)


def set_enabled(btn: ctk.CTkButton, enabled: bool) -> None:
    """Habilita/deshabilita un botón creado con make_button (colores incluidos)."""
    meta = getattr(btn, "_msr", None)
    if meta is None:
        btn.configure(state="normal" if enabled else "disabled")
        return
    if meta["enabled"] != enabled:
        meta["enabled"] = enabled
        _apply_style(btn)


def restyle(btn: ctk.CTkButton, style: str | None = None, text: str | None = None,
            icon: str | None = None) -> None:
    """Cambia estilo/texto/icono de un botón respetando su estado actual."""
    meta = btn._msr
    changed = False
    if style is not None and style != meta["style"]:
        meta["style"] = style
        changed = True
    if icon is not None and icon != meta["icon"]:
        meta["icon"] = icon
        changed = True
    if text is not None and text != btn.cget("text"):
        btn.configure(text=text)
    if changed:
        _apply_style(btn)


# --- Contenedores y piezas ---------------------------------------------------
def card(parent, **kw) -> ctk.CTkFrame:
    """Tarjeta: superficie elevada con borde sutil y esquinas redondeadas."""
    opts = dict(fg_color=T.SURFACE, corner_radius=T.R_CARD, border_width=1,
                border_color=T.BORDER)
    opts.update(kw)
    return ctk.CTkFrame(parent, **opts)


def label(parent, text: str = "", *, font: str = "body", color: str = T.TEXT,
          **kw) -> ctk.CTkLabel:
    opts = dict(text=text, font=T.font(font), text_color=color, anchor="w",
                justify="left")
    opts.update(kw)
    return ctk.CTkLabel(parent, **opts)


def caption(parent, text: str, color: str = T.MUTED, **kw) -> ctk.CTkLabel:
    """Etiqueta de sección en versalitas (overline)."""
    return label(parent, text.upper(), font="caption", color=color, **kw)


def place_grid(widget, **kw):
    """grid() que recuerda sus opciones para poder ocultar/mostrar el widget
    con set_visible(). (customtkinter vuelve a colocar los widgets ocultos con
    grid_remove() cuando cambia el escalado DPI; con grid_forget no.)"""
    widget._msr_grid = kw
    widget.grid(**kw)
    return widget


def set_visible(widget, visible: bool) -> None:
    """Muestra/oculta un widget colocado con place_grid()."""
    managed = bool(widget.winfo_manager())
    if visible and not managed:
        widget.grid(**getattr(widget, "_msr_grid", {}))
    elif not visible and managed:
        widget.grid_forget()


def regrid(widget, **changes) -> None:
    """Cambia opciones de grid (p. ej. pady) de un widget de place_grid(),
    esté visible u oculto."""
    kw = {**getattr(widget, "_msr_grid", {}), **changes}
    if kw == getattr(widget, "_msr_grid", None):
        return
    widget._msr_grid = kw
    if widget.winfo_manager():
        widget.grid(**kw)


def window_scale(widget) -> float:
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:
        return 1.0


def autowrap(lbl: ctk.CTkLabel, container, margin: int) -> None:
    """Ajusta el wraplength de `lbl` al ancho de `container` menos `margin`
    (px lógicos) cada vez que cambia de tamaño: textos que nunca se cortan."""
    state = {"w": None}

    def _on_configure(_e=None):
        scale = window_scale(container)
        w = max(80, int(container.winfo_width() / scale) - margin)
        if w != state["w"]:
            state["w"] = w
            lbl.configure(wraplength=w)

    container.bind("<Configure>", _on_configure, add=True)


def separator(parent, color: str = T.BORDER_STRONG) -> ctk.CTkFrame:
    """Línea de 1 px. (Un CTkFrame de 1 px de alto no se dibuja: se usa un
    Frame de Tk dentro de un contenedor de customtkinter, que sí escala el
    padding al colocarlo con grid/pack.)"""
    holder = ctk.CTkFrame(parent, fg_color="transparent", corner_radius=0, height=1)
    tkinter.Frame(holder, height=1, bg=color, bd=0, highlightthickness=0).pack(fill="x")
    return holder


class Pill(ctk.CTkLabel):
    """Etiqueta de estado redondeada con fondo tintado del tono."""

    def __init__(self, master, text: str = "", tone: str = "neutral",
                 base: str = T.SURFACE, height: int = 24, font: str = "small_strong"):
        super().__init__(master, text=text, height=height, corner_radius=height // 2,
                         font=T.font(font), padx=T.S1)
        self._base = base
        self.set(text, tone)

    def set(self, text: str, tone: str) -> None:
        self.configure(text=f" {text} ", fg_color=T.tint(tone, self._base, 0.18),
                       text_color=T.tone(tone))


class Stat(ctk.CTkFrame):
    """Dato pequeño: etiqueta en versalitas, cifra en Display y una coletilla."""

    def __init__(self, master, title: str, font: str = "stat"):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        caption(self, title, height=14).grid(row=0, column=0, columnspan=2, sticky="w")
        self.value = label(self, "—", font=font, height=22)
        self.value.grid(row=1, column=0, sticky="w")
        self.extra = label(self, "", font="small", color=T.MUTED, height=22)
        self.extra.grid(row=1, column=1, sticky="w", padx=(T.S2, 0))

    def set(self, value: str, extra: str = "", color: str = T.TEXT) -> None:
        self.value.configure(text=value, text_color=color)
        self.extra.configure(text=extra)


# Estado de una actividad → (tono, aro, apagado) de su medallón.
_MEDAL = {"ok": ("accent", 1.0, False, True), "pending": ("muted", 0.0, True, False),
          "na": ("muted", 0.0, True, False), "unknown": ("muted", 0.0, True, False)}


def medal_spec(state: str, progress=None) -> tuple[str, float, bool, bool]:
    """(tono, fracción del aro, apagado, insignia de hecho) del medallón de
    un estado. Las actividades van siempre en el azul de marca; el verde se
    queda en la insignia pequeña de "hecho" y el naranja, en el texto."""
    if state == "todo":
        return "accent", max(0.0, min(1.0, float(progress or 0.0))), False, False
    return _MEDAL.get(state, _MEDAL["unknown"])


class Figure(ctk.CTkFrame):
    """Cifra tratada: el valor en Display, el total en tono secundario y la
    unidad pequeña ("120 / 120 PTS"), alineados por abajo."""

    def __init__(self, master, font: str = "tile_figure", drop: int = 4):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        size = T.TYPE_SCALE[font][1]
        self.value = label(self, "—", font=font, height=size + 6)
        self.value.grid(row=0, column=0, sticky="sw")
        self.total = label(self, "", font="body", color=T.MUTED, height=18)
        self.total.grid(row=0, column=1, sticky="sw", padx=(T.S1, 0), pady=(0, drop))
        self.unit = label(self, "", font="caption", color=T.MUTED, height=14)
        self.unit.grid(row=0, column=2, sticky="sw", padx=(T.S1, 0), pady=(0, drop + 2))

    def set(self, value, total=None, unit: str = "", color: str = T.TEXT) -> None:
        known = value is not None
        self.value.configure(text=str(value) if known else "—",
                             text_color=color if known else T.FAINT)
        self.total.configure(text=f"/ {total}" if known and total is not None else "")
        self.unit.configure(text=unit.upper() if known else "")


class ActivityTile(ctk.CTkFrame):
    """Ficha de una actividad del día, compuesta en eje central: medallón de
    marca con su icono y el aro de progreso (con una insignia verde pequeña
    cuando está hecha), la cifra como protagonista, el nombre y, debajo, su
    racha de 7 días en segmentos con el día por el que va."""

    ICON = 48
    STREAK_W, STREAK_H = 86, 5

    def __init__(self, master, icons: Icons):
        self._bg = T.SURFACE_2
        super().__init__(master, fg_color=self._bg, corner_radius=T.R_CONTROL + 3,
                         border_width=1, border_color=T.BORDER, height=158, width=60)
        self._icons = icons
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure((0, 6), weight=1)       # contenido centrado en vertical
        self.icon = ctk.CTkLabel(self, text="", width=self.ICON, height=self.ICON)
        self.icon.grid(row=1, column=0)
        self.figure = Figure(self)
        self.figure.grid(row=2, column=0, pady=(T.S1 + 2, 0))
        self.name = label(self, "", font="small_strong", color=T.TEXT_2, height=16,
                          anchor="center")
        self.name.grid(row=3, column=0, padx=T.S2)
        self.streak = ctk.CTkLabel(self, text="", width=self.STREAK_W, height=self.STREAK_H)
        place_grid(self.streak, row=4, column=0, pady=(T.S2 + 1, 0))
        self.status = label(self, "", font="caption", color=T.MUTED, height=14,
                            anchor="center")
        self.status.grid(row=5, column=0, padx=T.S1, pady=(T.S1, 0))

    def set(self, tile: dict) -> None:
        state = tile.get("state", "unknown")
        tone, fraction, dim, done = medal_spec(state, tile.get("progress"))
        self.icon.configure(image=self._icons.medallion(
            CHECK_ICONS.get(tile.get("key"), "check"), tone, fraction, self.ICON,
            self._bg, dim, done))
        self.figure.set(tile.get("done"), tile.get("total"), tile.get("unit") or "")
        self.name.configure(text=tile.get("label", ""))
        # Sin dato de racha (días anteriores a que el bot la guardara) no se
        # pintan segmentos vacíos: parecerían una racha perdida.
        pair = tile.get("streak")
        set_visible(self.streak, bool(pair))
        if pair:
            self.streak.configure(image=streak_image(
                pair[0], pair[1], self._bg, self.STREAK_W, self.STREAK_H))
        # Lo que falta manda sobre la racha; si no falta nada, se lee la racha.
        if state == "todo":
            text, color = tile.get("status", ""), T.tone("warn")
        elif pair:
            text, color = tile.get("streak_text", ""), T.MUTED
        elif state == "ok":
            text, color = "Hecho · racha sin dato", T.MUTED
        else:
            text, color = tile.get("status", ""), T.MUTED
        self.status.configure(text=text.upper(), text_color=color)
        self.configure(border_color=T.mix(T.tone("warn"), self._bg, 0.4)
                       if state == "todo" else T.BORDER)


def streak_image(day: int, total: int = 7, base: str = T.SURFACE, width: int = 96,
                 height: int = 6) -> ctk.CTkImage | None:
    """Racha en segmentos (art.segments) como CTkImage."""
    if not art.available:
        return None
    try:
        img = art.segments(width * 2, height * 2, day, total, bg=base)
        return ctk.CTkImage(img, img, size=(width, height))
    except Exception:
        return None


def stamps_image(done: int | None, total: int, base: str = T.SURFACE,
                 width: int = 226, height: int = 15) -> ctk.CTkImage | None:
    """Cartilla de sellos (art.stamps) como CTkImage."""
    if not art.available:
        return None
    try:
        img = art.stamps(width * 2, height * 2, done, total, bg=base)
        return ctk.CTkImage(img, img, size=(width, height))
    except Exception:
        return None


class Ring(ctk.CTkLabel):
    """Protagonista del panel: anillo segmentado del día (art.ring) con la
    cifra en el centro. Al abrir se llena y la cifra cuenta hacia arriba."""

    _STEPS = 16
    _STEP_MS = 40                      # 16 × 40 ms ≈ 0,65 s

    def __init__(self, master, size: int, bg: str = T.HERO):
        super().__init__(master, text="", width=size, height=size)
        self._size = size
        self._bg = bg
        self._shown: tuple | None = None
        self._fmt: Callable[[int], str] = str
        self._animating = False
        self._run = 0                   # invalida animaciones anteriores
        self._px = 0                    # píxeles físicos de lo último pintado
        # Si cambia el escalado (DPI detectado tarde, otro monitor), repintar
        # al nuevo tamaño físico en vez de dejar que se reescale borroso.
        self.bind("<Configure>", lambda _e: self._check_px(), add="+")

    def _check_px(self) -> None:
        if (self._shown is not None and not self._animating and self._px
                and self._px != max(48, round(self._size * window_scale(self)))):
            self._paint(*self._final())

    def set_size(self, size: int) -> None:
        if size != self._size:
            self._size = size
            self.configure(width=size, height=size)
            if self._shown is not None and not self._animating:
                self._paint(*self._final())

    def show(self, fractions: tuple[float, ...], value: int | None, sub: str,
             top: str = "", *, fmt: Callable[[int], str] = str, empty: str = "—",
             animate: bool = False) -> None:
        """`value` es la cifra (None → `empty`); `fmt` la formatea. Si llega
        un dato nuevo a media animación, esta sigue hacia el nuevo destino."""
        shown = (tuple(fractions), value, sub, top, empty)
        if shown == self._shown:
            return
        self._shown = shown
        self._fmt = fmt
        if self._animating:
            return
        if animate:
            self.replay()
        else:
            self._paint(*self._final())

    def replay(self) -> None:
        """(Re)lanza la animación de apertura hacia lo que se muestra ahora."""
        if self._shown is None:
            return
        fractions, value = self._shown[0], self._shown[1]
        self._run += 1
        if art.available and (value or any(fractions)):
            self._animating = True
            self._step(self._run, 1)
        else:
            self._animating = False
            self._paint(*self._final())

    def _final(self) -> tuple:
        fractions, value, sub, top, empty = self._shown
        return fractions, (empty if value is None else self._fmt(value)), sub, top

    def _step(self, run: int, i: int) -> None:
        if run != self._run or not self.winfo_exists():
            return
        if i >= self._STEPS:
            self._animating = False
            self._paint(*self._final())
            return
        fractions, value, sub, top, empty = self._shown
        t = 1 - (1 - i / self._STEPS) ** 3              # ease-out
        text = empty if value is None else self._fmt(round(value * t))
        self._paint(art.sweep(fractions, t), text, sub, top)
        self.after(self._STEP_MS, lambda: self._step(run, i + 1))

    def _paint(self, fractions, text, sub, top) -> None:
        if not art.available:
            self.configure(text=text, font=T.font("figure"))
            return
        try:
            px = self._px = max(48, round(self._size * window_scale(self)))
            img = art.ring(px, tuple(fractions), text, sub, top, bg=self._bg,
                           value_color=T.TEXT if any(ch.isdigit() for ch in text)
                           else T.FAINT)
            self._img = ctk.CTkImage(img, img, size=(self._size, self._size))
            self.configure(image=self._img)
        except Exception:
            pass


class HistoryChart(ctk.CTkFrame):
    """Historial de puntos (art.bars) que se redibuja al tamaño exacto que le
    da el layout. Al pasar el ratón resalta el día y avisa con on_hover(i);
    al pulsar una barra avisa con on_select(i). set_selected(i) deja un día
    marcado."""

    def __init__(self, master, bg: str = T.SURFACE,
                 on_hover: Callable[[int | None], None] | None = None,
                 min_height: int = 64,
                 on_select: Callable[[int], None] | None = None):
        super().__init__(master, fg_color="transparent", corner_radius=0,
                         height=min_height, width=120)
        self.grid_propagate(False)
        self.pack_propagate(False)
        self._bg = bg
        self._on_hover = on_hover
        self._data: tuple = ()
        self._empty = ("", "")
        self._hover: int | None = None
        self._selected: int | None = None
        self._on_select = on_select
        self._drawn: tuple | None = None
        self._pending = False
        self._img_label = ctk.CTkLabel(self, text="", width=1, height=1)
        self._img_label.place(x=0, y=0)
        self.bind("<Configure>", lambda _e: self._schedule(), add="+")
        self._img_label.bind("<Motion>", self._on_motion, add="+")
        self._img_label.bind("<Leave>", lambda _e: self._set_hover(None), add="+")
        self._img_label.bind("<Button-1>", self._on_click, add="+")
        if on_select is not None:
            self._img_label.configure(cursor="hand2")

    def set_selected(self, index: int | None) -> None:
        if index != self._selected:
            self._selected = index
            self._schedule()

    def _on_click(self, event) -> None:
        index = art.bar_slot(event.x, self.winfo_width(), len(self._data))
        if index is not None and self._on_select is not None:
            self._on_select(index)

    def set(self, data: tuple, empty: tuple[str, str] = ("", "")) -> None:
        """`data`: tupla de (ganado | None, bonus, etiqueta) por día."""
        self._data, self._empty = tuple(data), empty
        self._hover = None
        self._schedule()

    def _schedule(self) -> None:
        if not self._pending:
            self._pending = True
            self.after_idle(self._render)

    def _render(self) -> None:
        self._pending = False
        if not art.available or not self.winfo_exists():
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40 or h < 24:
            return
        scale = window_scale(self)
        key = (w, h, self._data, self._hover, self._empty, scale, self._selected)
        if key == self._drawn:
            return
        self._drawn = key
        try:
            img = art.bars(w, h, self._data, scale=scale, hover=self._hover,
                           bg=self._bg, empty=self._empty, selected=self._selected)
            self._img = ctk.CTkImage(img, img, size=(w / scale, h / scale))
            self._img_label.configure(image=self._img)
        except Exception:
            pass

    def _on_motion(self, event) -> None:
        self._set_hover(art.bar_slot(event.x, self.winfo_width(), len(self._data)))

    def _set_hover(self, index: int | None) -> None:
        if index == self._hover:
            return
        self._hover = index
        self._render()
        if self._on_hover is not None:
            self._on_hover(index)


_measure_fonts: dict[tuple, object] = {}


def fit_text(text: str, font_tuple: tuple, max_px: int, scale: float = 1.0) -> str:
    """Recorta `text` con '…' para que quepa en `max_px` píxeles físicos."""
    key = (font_tuple[0], round(abs(int(font_tuple[1])) * scale))
    try:
        f = _measure_fonts.get(key)
        if f is None:
            import tkinter.font as tkfont
            f = _measure_fonts[key] = tkfont.Font(family=key[0], size=-key[1])
    except Exception:
        return text
    if max_px <= 0:
        return ""
    if f.measure(text) <= max_px:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + "…") <= max_px:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo].rstrip() + "…"


# --- Popover ----------------------------------------------------------------
class Popover(tkinter.Toplevel):
    """Menú desplegable propio (no el nativo de Tk): una tarjeta redondeada,
    sin bordes de ventana, con una fila por opción (icono, título y una línea
    de descripción) y hover marcado.

    `entries`: [(icono, título, descripción, función)…]. Se abre bajo `anchor`
    sin salirse de la ventana principal y se cierra al elegir, con Escape o al
    perder el foco (clic fuera, cambiar de ventana). No usa topmost ni
    focus_force: solo pide el foco dentro de nuestra propia aplicación.
    """

    _KEY = "#010203"            # color "transparente" para las esquinas redondeadas

    def __init__(self, master, anchor, entries: list[tuple], icons: Icons | None = None,
                 width: int = 286):
        super().__init__(master)
        self.withdraw()
        self.overrideredirect(True)
        self._closed = False
        self._master = master
        try:
            self.configure(bg=self._KEY)
            self.attributes("-transparentcolor", self._KEY)
            corner_bg = self._KEY
        except tkinter.TclError:
            corner_bg = T.SURFACE_2
            self.configure(bg=corner_bg)
        try:                    # mismo escalado que la ventana principal
            st = ctk.ScalingTracker
            st.window_dpi_scaling_dict[self] = st.window_dpi_scaling_dict[
                st.get_window_root_of_widget(master)]
        except Exception:
            pass
        card = ctk.CTkFrame(self, fg_color=T.SURFACE_2, bg_color=corner_bg,
                            corner_radius=T.R_CARD, border_width=1,
                            border_color=T.BORDER_STRONG)
        card.pack(fill="both", expand=True)
        ctk.CTkFrame(card, width=width, height=1, fg_color="transparent").pack(
            padx=T.S1 + 2, pady=(T.S1 + 1, 0))
        for i, (icon, title, desc, command) in enumerate(entries):
            row = ctk.CTkFrame(card, fg_color="transparent", corner_radius=T.R_CONTROL,
                               cursor="hand2")
            row.pack(fill="x", padx=T.S1 + 2,
                     pady=(0, T.S1 + 2 if i == len(entries) - 1 else 2))
            row.grid_columnconfigure(1, weight=1)
            img = icons.get(icon, T.GOLD, 18) if icons else None
            ctk.CTkLabel(row, text="", image=img, width=18, height=20).grid(
                row=0, column=0, rowspan=2, padx=(T.S3, T.S3), pady=T.S2)
            label(row, title, font="body_strong", height=18).grid(
                row=0, column=1, sticky="sw", pady=(T.S2 - 1, 0))
            label(row, desc, font="small", color=T.MUTED, height=16).grid(
                row=1, column=1, sticky="nw", pady=(0, T.S2 - 1), padx=(0, T.S3))
            self._bind_row(row, row, command)
        self.bind("<Escape>", lambda _e: self.close())
        self.bind("<FocusOut>", lambda _e: self.after(60, self._check_focus))
        self._place(anchor)
        self.deiconify()
        self.lift()
        self.focus_set()

    def _bind_row(self, row, widget, command) -> None:
        widget.bind("<Enter>", lambda _e: row.configure(fg_color=T.SURFACE_3), add="+")
        widget.bind("<Leave>", lambda _e: self._leave(row), add="+")
        widget.bind("<Button-1>", lambda _e: self._choose(command), add="+")
        for child in widget.winfo_children():
            self._bind_row(row, child, command)

    def _leave(self, row) -> None:
        # <Leave> también llega al pasar de la fila a uno de sus hijos.
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except Exception:
            w = None
        while w is not None:
            if w is row:
                return
            w = getattr(w, "master", None)
        row.configure(fg_color="transparent")

    def _place(self, anchor) -> None:
        """Bajo el ancla; si no cabe en la ventana principal, se ajusta a su
        borde derecho o se abre hacia arriba."""
        self.update_idletasks()
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        m = self._master.winfo_toplevel()
        gap = round(4 * window_scale(self._master))
        x = anchor.winfo_rootx()
        y = anchor.winfo_rooty() + anchor.winfo_height() + gap
        right = m.winfo_rootx() + m.winfo_width() - gap
        bottom = m.winfo_rooty() + m.winfo_height() - gap
        x = max(m.winfo_rootx() + gap, min(x, right - w))
        if y + h > bottom:
            y = max(m.winfo_rooty() + gap, anchor.winfo_rooty() - h - gap)
        self.geometry(f"+{x}+{y}")

    def _check_focus(self) -> None:
        if self._closed:
            return
        try:
            focus = self.focus_displayof()
        except Exception:
            focus = None
        if focus is None or focus.winfo_toplevel() is not self:
            self.close()

    def _choose(self, command) -> None:
        self.close()
        if command is not None:
            self._master.after(10, command)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self.destroy()
            except Exception:
                pass


# --- Diálogos -------------------------------------------------------------
class Dialog(ctk.CTkToplevel):
    """Base de los diálogos modales con el mismo lenguaje visual que el panel.

    Se construye oculta, se centra sobre la ventana principal y luego se
    muestra y captura la entrada (grab). `self.result` es None si se cancela.
    No usa topmost ni focus_force: no roba el foco a otras aplicaciones.
    """

    def __init__(self, master, title: str, width: int = 460):
        super().__init__(master, fg_color=T.SURFACE)
        self.withdraw()
        self.title(title)
        # resizable() de customtkinter vuelve a ocultar y mostrar la ventana
        # para repintar la barra de título (parpadeo); la barra oscura ya se
        # aplicó al crearla, así que usamos el de Tk directamente.
        tkinter.Wm.wm_resizable(self, False, False)
        self.result = None
        self._width = width
        self._icons: Icons | None = getattr(master, "icons", None)
        self._sync_dpi_with(master)
        try:
            ico = core.icon_path()
            if ico:
                self.iconbitmap(str(ico))
        except Exception:
            pass
        self.body = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self.body.pack(fill="both", expand=True, padx=T.S6, pady=(T.S6, T.S5))
        self.body.grid_columnconfigure(0, weight=1)
        # Fija el ancho del diálogo; el alto lo calcula Tk según el contenido.
        ctk.CTkFrame(self.body, width=width - 2 * T.S6, height=1,
                     fg_color="transparent").grid(row=99, column=0)
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        self.bind("<Escape>", lambda _e: self._cancel())

    # -- piezas comunes --
    def header(self, title: str, message: str = "", icon: str = "info",
               tone: str = "info", row: int = 0) -> None:
        head = ctk.CTkFrame(self.body, fg_color="transparent")
        head.grid(row=row, column=0, sticky="ew")
        head.grid_columnconfigure(1, weight=1)
        img = self._icons.badge(icon, T.tone(tone), T.tint(tone, T.SURFACE, 0.18), 40, 0.45) \
            if self._icons else None
        ctk.CTkLabel(head, text="", image=img, width=40, height=40).grid(
            row=0, column=0, rowspan=2, sticky="nw", padx=(0, T.S4))
        label(head, title, font="dialog_title").grid(row=0, column=1, sticky="w")
        if message:
            label(head, message, font="body", color=T.TEXT_2,
                  wraplength=self._width - 2 * T.S6 - 56).grid(
                row=1, column=1, sticky="w", pady=(T.S1, 0))

    def footer(self, row: int) -> ctk.CTkFrame:
        bar = ctk.CTkFrame(self.body, fg_color="transparent")
        bar.grid(row=row, column=0, sticky="ew", pady=(T.S6, 0))
        return bar

    # -- ciclo de vida --
    def _sync_dpi_with(self, master) -> None:
        """Con monitores de distinto escalado, customtkinter calcula el DPI de
        un Toplevel nuevo en su posición por defecto (monitor principal) y lo
        reescala al mostrarlo, con un tamaño forzado que puede cortar el
        contenido. Lo colocamos ya sobre la ventana principal y fijamos su
        escalado ANTES de crear los widgets."""
        try:
            st = ctk.ScalingTracker
            # Mismo escalado que la ventana principal (el diálogo se abre encima).
            # Solo se anota: los widgets que se creen después ya lo usan, y el
            # bucle de customtkinter no verá un "cambio de DPI" que reescalar.
            st.window_dpi_scaling_dict[self] = st.window_dpi_scaling_dict[
                st.get_window_root_of_widget(master)]
        except Exception:
            pass

    def show(self) -> None:
        """Centra sobre la ventana principal (tamaño natural del contenido) y
        muestra el diálogo como modal."""
        # Sin tamaño forzado: que Tk lo ajuste al contenido.
        try:
            tkinter.Wm.wm_minsize(self, 1, 1)
            tkinter.Wm.wm_maxsize(self, 30000, 30000)
            tkinter.Wm.wm_geometry(self, "")
        except Exception:
            pass
        self.update_idletasks()
        m = self.master
        try:
            w, h = self.winfo_reqwidth(), self.winfo_reqheight()   # px físicos
            mx, my = m.winfo_rootx(), m.winfo_rooty()
            mw, mh = m.winfo_width(), m.winfo_height()
            x = mx + (mw - w) // 2
            y = my + max(0, (mh - h) // 3)
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass
        try:
            self.transient(m)
        except Exception:
            pass
        self.deiconify()
        self._grab()

    def _grab(self, tries: int = 0) -> None:
        try:
            self.grab_set()
        except tkinter.TclError:
            if tries < 20:
                self.after(30, lambda: self._grab(tries + 1))

    def close(self, result) -> None:
        self.result = result
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _cancel(self) -> None:
        self.close(None)


class MessageDialog(Dialog):
    """Aviso / confirmación con el estilo del panel (sustituye a messagebox).

    kind: "info" | "warn" | "error" | "confirm". En confirm, `result` es True
    si acepta y False/None si no; en el resto, True al cerrar con Aceptar.
    """

    _ICON = {"info": ("info", "info"), "warn": ("warning", "warn"),
             "error": ("error", "error"), "confirm": ("info", "accent")}

    def __init__(self, master, title: str, message: str, *, kind: str = "info",
                 ok_text: str = "Aceptar", cancel_text: str = "Cancelar",
                 danger: bool = False, tone: str | None = None, icon: str | None = None):
        super().__init__(master, title, width=460)
        ic, tn = self._ICON.get(kind, self._ICON["info"])
        if danger:
            ic, tn = "warning", "error"
        self.header(title, message, icon=icon or ic, tone=tone or tn)
        bar = self.footer(row=1)
        ok = make_button(bar, ok_text, lambda: self.close(True),
                         style="danger" if danger else "primary", width=120)
        ok.pack(side="right")
        if kind == "confirm":
            make_button(bar, cancel_text, lambda: self.close(False),
                        style="secondary", width=110).pack(side="right", padx=(0, T.S2))
        self.bind("<Return>", lambda _e: self.close(True))
        self.show()
        ok.focus_set()


class OptionCard(ctk.CTkFrame):
    """Opción grande clicable (icono + título + descripción) para elegir modo."""

    def __init__(self, master, icons: Icons | None, icon: str, title: str, desc: str,
                 command: Callable, badge: str = ""):
        super().__init__(master, fg_color=T.SURFACE_2, corner_radius=T.R_CONTROL,
                         border_width=1, border_color=T.BORDER_STRONG, cursor="hand2")
        self._command = command
        self.grid_columnconfigure(1, weight=1)
        img = icons.badge(icon, T.GOLD, T.tint("accent", T.SURFACE_2, 0.16), 36, 0.46) \
            if icons else None
        ctk.CTkLabel(self, text="", image=img, width=36, height=36).grid(
            row=0, column=0, rowspan=2, padx=(T.S4, T.S3), pady=T.S3)
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=1, sticky="sw", pady=(T.S3, 0))
        label(head, title, font="body_strong").pack(side="left")
        if badge:
            Pill(head, badge, "accent", base=T.SURFACE_2, height=20,
                 font="caption").pack(side="left", padx=(T.S2, 0))
        label(self, desc, font="small", color=T.TEXT_2).grid(
            row=1, column=1, sticky="nw", pady=(0, T.S3), padx=(0, T.S4))
        self._bind_all(self)

    def _bind_all(self, widget) -> None:
        for w in (widget, *widget.winfo_children()):
            w.bind("<Enter>", self._hover_on, add=True)
            w.bind("<Leave>", self._hover_off, add=True)
            w.bind("<Button-1>", self._click, add=True)
            if w is not widget:
                self._bind_all(w)

    def _hover_on(self, _e=None):
        if not getattr(self, "_hot", False):
            self._hot = True
            self.configure(border_color=T.GOLD, fg_color=T.mix(T.GOLD, T.SURFACE_2, 0.06))

    def _hover_off(self, _e=None):
        # Al pasar del marco a un hijo llega <Leave>: solo apagar si el ratón
        # ha salido de verdad de la tarjeta (evita parpadeos).
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except Exception:
            w = None
        while w is not None:
            if w is self:
                return
            w = getattr(w, "master", None)
        self._hot = False
        self.configure(border_color=T.BORDER_STRONG, fg_color=T.SURFACE_2)

    def _click(self, _e=None):
        self._command()
