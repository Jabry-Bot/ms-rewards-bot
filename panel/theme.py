"""
Design tokens del panel: paleta, tipografía, espaciados, radios y medidas.

Es el ÚNICO sitio donde se definen colores, fuentes y tamaños; art.py,
widgets.py y app.py solo los consumen.

Identidad Bing: base oscura neutra, azul de Bing como único acento de marca
y de acción, cian/turquesa de su logo en degradados e información. El oro solo
aparece en la moneda del nivel Oro. Verde = hecho, naranja = falta algo, rojo =
peligro; ninguno de los tres se usa como decoración.
"""
from __future__ import annotations

import os
from pathlib import Path

# --- Paleta --------------------------------------------------------------
# Colores de Bing: base oscura neutra (Fluent) y el azul de Bing como acento
# de marca y de acción, con el cian/turquesa de su logo en los degradados.
# Los nombres GOLD* se conservan por compatibilidad: hoy son el azul de marca.
BG = "#16181d"            # fondo de la ventana
RAIL = "#111317"          # columna lateral
SURFACE = "#1d2027"       # tarjetas
HERO = "#20242c"          # tarjeta protagonista
SURFACE_2 = "#272b34"     # controles dentro de tarjetas, botones secundarios
SURFACE_3 = "#313641"     # hover de secundarios, pistas de progreso
HOVER_NAV = "#1c1f26"     # hover de los elementos de la columna lateral
BORDER = "#2a2e37"        # borde de tarjetas
BORDER_STRONG = "#3a3f4b"  # borde de controles
LOG_BG = "#0f1114"        # fondo del registro

TEXT = "#eef1f6"
TEXT_2 = "#b6bdc9"        # texto secundario
MUTED = "#8e96a4"         # pistas, etiquetas pequeñas
FAINT = "#5d6470"         # deshabilitado / decorativo

GOLD = "#4aa8ff"          # azul de Bing: marca (texto, anillo, barras de hoy)
GOLD_HI = "#8fdcff"       # cian del logo de Bing (brillos, degradados)
GOLD_DEEP = "#1f66e0"     # azul profundo (sombra de los degradados)
GOLD_DIM = "#2b5288"      # azul apagado: barras normales del historial
ON_GOLD = "#ffffff"       # texto sobre el azul de acción
RIBBON = "#38d3c6"        # turquesa de Bing: secundario, solo información
SILVER = "#cdd2d8"
BRONZE = "#c08a5a"
MEDAL_GOLD = "#f0b840"    # oro de verdad, solo para el nivel Oro (moneda)
MEDAL_GOLD_HI = "#ffdc85"
MEDAL_GOLD_DEEP = "#c4871c"

ACCENT = "#1a6fe0"        # relleno del botón principal (blanco encima: 5:1)
ACCENT_HOVER = "#2a7cf0"
ACCENT_DISABLED = "#1f2a3c"
ACCENT_TEXT = GOLD
ON_ACCENT = ON_GOLD
DANGER = "#b3261e"        # botón destructivo (blanco encima: 6.4:1)
DANGER_HOVER = "#8f1d17"
ON_DANGER = "#ffffff"

# Tonos semánticos: core.py devuelve estas claves y aquí se traducen a color.
TONES: dict[str, str] = {
    "ok": "#62d394",
    "warn": "#ff9f4a",
    "error": "#f47a6e",
    "info": RIBBON,
    "neutral": TEXT_2,
    "muted": MUTED,
    "gold": MEDAL_GOLD,
    "accent": GOLD,
    "silver": SILVER,
}

# Estado de la app de Bing (core.app_status()["state"]) → tono.
APP_TONES = {"unlinked": "neutral", "disabled": "neutral", "relink": "error",
             "pending": "info", "done": "ok", "partial": "warn"}


def rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def mix(fg: str, bg: str, amount: float) -> str:
    """Color sólido = fg al `amount` (0..1) sobre bg. Tk no tiene alfa."""
    a, b = rgb(fg), rgb(bg)
    return "#%02x%02x%02x" % tuple(round(x * amount + y * (1 - amount))
                                   for x, y in zip(a, b))


def tone(name: str) -> str:
    """Color de texto/icono para un tono semántico."""
    return TONES.get(name, TEXT_2)


def tint(name: str, base: str = SURFACE, amount: float = 0.16) -> str:
    """Fondo tintado (pills, insignias) para un tono sobre `base`."""
    return mix(tone(name), base, amount)


HERO_BORDER = mix(GOLD, HERO, 0.26)

# --- Espaciado (escala de 4 px) y radios ---------------------------------
S1, S2, S3, S4, S5, S6 = 4, 8, 12, 16, 20, 24
PAD = S5                  # margen lateral del área principal
PAD_Y = S4                # margen superior/inferior del área principal
GAP = S3                  # separación entre tarjetas
CARD_PAD = 18             # relleno interior de las tarjetas

R_CARD = 14
R_CONTROL = 9
R_SMALL = 6

# --- Medidas -------------------------------------------------------------
RAIL_W = 236
CONTROL_H = 32            # botones normales
CONTROL_H_LG = 44         # botón principal
NAV_H = 27                # elementos de la columna de marca
BAR_H = 50                # barra de actividad inferior
MIN_W, MIN_H = 1080, 672  # minsize: cabe en 1280×720 lógicos con barra de tareas
COMPACT_H = 720           # por debajo de este alto, modo compacto (menos aire)
INIT_W, INIT_H = 1320, 820
RING_MIN, RING_MAX = 168, 288   # lado del anillo: se adapta al alto del protagonista
HERO_TEXT_MIN = 390       # ancho que el anillo deja siempre al texto de al lado
HERO_TEXT_MAX = 470       # más allá, el conjunto anillo + texto se centra

# --- Tipografía ----------------------------------------------------------
# Familias por orden de preferencia (Windows 11 → Windows 10). Los nombres
# "Semib"/"Semibold" son las instancias de la fuente variable que expone GDI.
_FAMILIES: dict[str, tuple[str, ...]] = {
    "display": ("Segoe UI Variable Display", "Segoe UI"),
    "display_semibold": ("Segoe UI Variable Display Semib",
                         "Segoe UI Variable Display Semibold", "Segoe UI Semibold"),
    "text": ("Segoe UI Variable Text", "Segoe UI"),
    "text_semibold": ("Segoe UI Variable Text Semibold", "Segoe UI Semibold"),
    "mono": ("Cascadia Mono", "Consolas", "Courier New"),
}

# Escala tipográfica: nombre → (familia lógica, tamaño en px). La jerarquía va
# por tamaño Y peso: cifras en Display seminegrita, texto en Text.
TYPE_SCALE: dict[str, tuple[str, int]] = {
    "figure": ("display_semibold", 32),      # cifra de una tarjeta (total del mes)
    "balance": ("display_semibold", 28),     # saldo en la columna de marca
    "hero": ("display_semibold", 25),        # veredicto del día
    "dialog_title": ("display_semibold", 19),
    "brand": ("display_semibold", 16),
    "tile_figure": ("display_semibold", 24),  # cifra de una ficha de actividad
    "stat": ("display_semibold", 15),        # cifras pequeñas (ritmo, racha…)
    "title": ("text_semibold", 14),
    "body": ("text", 13),
    "body_strong": ("text_semibold", 13),
    "small": ("text", 12),
    "small_strong": ("text_semibold", 12),
    "caption": ("text_semibold", 10),        # versalitas (overline)
    "button": ("text_semibold", 13),
    "button_lg": ("text_semibold", 15),
    "mono": ("mono", 12),
    "mono_small": ("mono", 11),
}

_resolved: dict[str, tuple[str, str]] = {}   # familia lógica → (familia real, peso)


def resolve_fonts(root) -> None:
    """Elige las familias instaladas (necesita un root de Tk ya creado)."""
    try:
        import tkinter.font as tkfont
        installed = set(tkfont.families(root))
    except Exception:
        installed = set()
    for key, candidates in _FAMILIES.items():
        family = next((f for f in candidates if f in installed), None)
        if family is not None:
            _resolved[key] = (family, "normal")
        elif key.endswith("semibold"):
            _resolved[key] = ("Segoe UI", "bold")       # sin semibold: negrita
        else:
            _resolved[key] = (candidates[-1], "normal")


def font(name: str) -> tuple:
    """Tupla de fuente para customtkinter: (familia, tamaño_px, peso)."""
    logical, size = TYPE_SCALE[name]
    family, weight = _resolved.get(logical) or (_FAMILIES[logical][-1], "normal")
    return (family, size, weight)


def mono_family() -> str:
    return (_resolved.get("mono") or (_FAMILIES["mono"][-1], ""))[0]


# --- Fuentes para lo que se dibuja con Pillow (art.py) ---------------------
# (archivo, ejes [peso, tamaño óptico] de la fuente variable o None).
_PIL_FONTS: dict[str, tuple[tuple[str, tuple[float, float] | None], ...]] = {
    "display_semibold": (("SegUIVar.ttf", (600, 36)), ("seguisb.ttf", None),
                         ("segoeuib.ttf", None)),
    "text_semibold": (("SegUIVar.ttf", (600, 10.5)), ("seguisb.ttf", None),
                      ("segoeuib.ttf", None)),
    "text": (("SegUIVar.ttf", (400, 10.5)), ("segoeui.ttf", None)),
}


def pil_font_candidates(kind: str) -> list[tuple[str, tuple[float, float] | None]]:
    """Rutas de fuente (las que existan) para dibujar texto con Pillow."""
    fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    return [(str(fonts / name), axes) for name, axes in _PIL_FONTS.get(kind, ())
            if (fonts / name).exists()]
