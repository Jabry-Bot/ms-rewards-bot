"""
Gráficos del panel dibujados con Pillow en tiempo de ejecución (sin recursos
externos): el anillo del día, el historial de barras y la moneda del nivel.

Solo Pillow + design tokens; nada de Tk aquí (widgets.py los envuelve en
CTkImage). Las formas se dibujan a SS× y se reducen con LANCZOS (antialias);
el texto se pinta después, ya al tamaño final en píxeles físicos, para que
salga nítido. Todo lo que no cambia se cachea.
"""
from __future__ import annotations

import math
from typing import Callable

try:
    from panel import theme as T
except ImportError:  # .exe / ejecución suelta
    import theme as T

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except Exception:  # pragma: no cover - Pillow viene con customtkinter
    Image = ImageDraw = ImageFilter = ImageFont = ImageOps = None

SS = 3                                   # supermuestreo de las formas
available = Image is not None

_fonts: dict[tuple[str, int], object] = {}
_cache: dict[tuple, object] = {}
_CACHE_MAX = 96


def _remember(key: tuple, make: Callable[[], object]):
    if key not in _cache:
        if len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)))
        _cache[key] = make()
    return _cache[key]


def font(kind: str, px: int):
    """Fuente de Pillow (Segoe UI Variable con su peso/tamaño óptico)."""
    px = max(6, int(px))
    key = (kind, px)
    if key not in _fonts:
        chosen = None
        for path, axes in T.pil_font_candidates(kind):
            try:
                chosen = ImageFont.truetype(path, px)
                if axes:
                    chosen.set_variation_by_axes(list(axes))
                break
            except Exception:
                chosen = None
        if chosen is None:
            try:
                chosen = ImageFont.load_default(px)
            except Exception:
                chosen = ImageFont.load_default()
        _fonts[key] = chosen
    return _fonts[key]


def _gradient(size: tuple[int, int], top: str, mid: str, bottom: str,
              angle: float = 0.0):
    """Degradado lineal de `top` a `bottom` (girado `angle` grados)."""
    def make():
        g = Image.linear_gradient("L")               # 256×256, negro arriba
        if angle:
            g = g.rotate(angle, resample=Image.BICUBIC).crop((52, 52, 204, 204))
        g = g.resize(size, Image.BICUBIC)
        return ImageOps.colorize(g, black=top, white=bottom, mid=mid)
    return _remember(("grad", size, top, mid, bottom, angle), make)


# --- Anillo del día ---------------------------------------------------------
def _arc(draw, cx: float, cy: float, r: float, width: int, start: float,
         end: float) -> None:
    """Arco grueso con extremos redondeados (ángulos en grados, 0 = arriba,
    sentido horario)."""
    half = width / 2
    outer = r + half
    if end - start >= 359.9:
        draw.ellipse((cx - outer, cy - outer, cx + outer, cy + outer),
                     outline=255, width=width)
        return
    if end > start:
        draw.arc((cx - outer, cy - outer, cx + outer, cy + outer),
                 start - 90, end - 90, fill=255, width=width)
    for a in (start, end):
        x = cx + r * math.sin(math.radians(a))
        y = cy - r * math.cos(math.radians(a))
        draw.ellipse((x - half, y - half, x + half, y + half), fill=255)


def ring(px: int, fractions: tuple[float, ...], value: str = "", sub: str = "",
         top: str = "", *, bg: str = T.HERO, value_color: str = T.TEXT):
    """
    Anillo segmentado del día: un tramo por actividad, relleno en oro según su
    progreso (0..1), con halo dorado y la cifra del día en el centro.
    `px` es el lado del lienzo en píxeles físicos. Devuelve una imagen RGB
    sobre `bg` (Tk no tiene alfa: el fondo va cocido en la imagen).
    """
    fractions = tuple(max(0.0, min(1.0, float(f))) for f in fractions)
    key = ("ring", px, tuple(round(f, 3) for f in fractions), value, sub, top, bg,
           value_color)
    return _remember(key, lambda: _ring(px, fractions, value, sub, top, bg, value_color))


def _ring(px, fractions, value, sub, top, bg, value_color):
    s = px * SS
    c = s / 2
    width = max(3, round(s * 0.058))
    r = s / 2 - s * 0.085 - width / 2
    track = Image.new("L", (s, s), 0)
    fill = Image.new("L", (s, s), 0)
    dt, df = ImageDraw.Draw(track), ImageDraw.Draw(fill)
    n = len(fractions)
    if n <= 1:
        _arc(dt, c, c, r, width, 0, 360)
        f = fractions[0] if n else 0.0
        if f >= 0.999:
            _arc(df, c, c, r, width, 0, 360)
        elif f > 0:
            _arc(df, c, c, r, width, 0, 360 * f)
    else:
        span = 360 / n
        cap = math.degrees((width / 2) / r)          # lo que ocupa cada extremo redondo
        inset = cap + 2.2                            # + medio hueco visible
        for i, f in enumerate(fractions):
            a0, a1 = i * span + inset, (i + 1) * span - inset
            _arc(dt, c, c, r, width, a0, a1)
            if f > 0:
                _arc(df, c, c, r, width, a0, a0 + (a1 - a0) * f)

    img = Image.new("RGB", (s, s), bg)
    if any(fractions):
        # Halo: el propio relleno desenfocado, en oro tenue.
        halo = fill.filter(ImageFilter.GaussianBlur(s * 0.032)).point(
            lambda v: int(v * 0.36))
        img.paste(Image.new("RGB", (s, s), T.GOLD), mask=halo)
    img.paste(Image.new("RGB", (s, s), T.mix(T.GOLD, bg, 0.13)), mask=track)
    img.paste(_gradient((s, s), T.GOLD_HI, T.GOLD, T.GOLD_DEEP, angle=35), mask=fill)
    img = img.resize((px, px), Image.LANCZOS)

    d = ImageDraw.Draw(img)
    k = px / 200
    mid = px / 2
    if value:
        size = round(44 * k)
        f = font("display_semibold", size)
        while size > 12 and d.textlength(value, font=f) > px * 0.56:
            size -= 2
            f = font("display_semibold", size)
        d.text((mid, mid - 2 * k), value, font=f, fill=value_color, anchor="mm")
    if top:
        d.text((mid, mid - 36 * k), top, font=font("text_semibold", round(10.5 * k)),
               fill=T.MUTED, anchor="mm")
    if sub:
        d.text((mid, mid + 34 * k), sub, font=font("text", round(12.5 * k)),
               fill=T.TEXT_2, anchor="mm")
    return img


def sweep(fractions: tuple[float, ...], t: float) -> tuple[float, ...]:
    """Progreso de la animación de apertura: llena los tramos uno tras otro
    hasta la fracción `t` (0..1) del total."""
    left = sum(fractions) * max(0.0, min(1.0, t))
    out = []
    for f in fractions:
        take = min(f, left)
        out.append(take)
        left -= take
    return tuple(out)


# --- Historial de barras -----------------------------------------------------
_SKELETON = (0.34, 0.5, 0.42, 0.62, 0.38, 0.3, 0.55, 0.46, 0.7, 0.4, 0.36, 0.52,
             0.44, 0.9, 0.48, 0.33, 0.58, 0.41, 0.5, 0.37, 0.64, 0.45, 0.39, 0.56,
             0.47, 0.35, 0.6, 0.43, 0.8, 0.49, 0.53)


def bar_slot(x: float, width: float, count: int) -> int | None:
    """Índice de la barra bajo la coordenada x (o None si cae fuera)."""
    if count <= 0 or width <= 0 or not 0 <= x < width:
        return None
    return min(count - 1, int(x / (width / count)))


def bars(w: int, h: int, history: tuple[tuple, ...], *, scale: float = 1.0,
         hover: int | None = None, bg: str = T.SURFACE,
         empty: tuple[str, str] = ("", ""), selected: int | None = None):
    """
    Historial de puntos por día. `history` es una tupla de
    (ganado | None, bonus, etiqueta_del_pico) del día más antiguo a hoy.
    Barras en oro apagado; los días con bonus, en oro brillante con su cifra
    encima; hoy, en oro pleno y con un punto bajo la línea base; los días sin
    corrida, un punto tenue. Línea discontinua = media diaria. La altura va
    en escala comprimida para que los picos no aplasten al resto.
    `selected` marca un día elegido: franja clara detrás y punto bajo la base.
    `w`×`h` en píxeles físicos; `scale` = escalado de la ventana.
    """
    key = ("bars", w, h, history, round(scale, 3), hover, bg, empty, selected)
    return _remember(key, lambda: _bars(w, h, history, scale, hover, bg, empty,
                                        selected))


def _bars(w, h, history, scale, hover, bg, empty, selected=None):
    n = len(history)
    values = [g for g, _b, _l in history if g is not None]
    has_data = bool(values)
    if not has_data:
        history = tuple((None, False, "") for _ in range(n or len(_SKELETON)))
        n = len(history)
    top = round(19 * scale)
    bottom = round(8 * scale)
    plot = max(8, h - top - bottom)
    slot = w / n
    bw = max(2.0, min(round(slot * 0.62), 26 * scale))
    vmax = max(values, default=0) or 1

    def height(g: float) -> float:
        # Escala comprimida (potencia 0,62): un día de bonus de +1.000 no deja
        # los días normales reducidos a una raya.
        return plot * SS * (max(0.0, g) / vmax) ** 0.62
    W, H = w * SS, h * SS
    base_y = (top + plot) * SS

    img = Image.new("RGB", (W, H), bg)
    d = ImageDraw.Draw(img)
    bonus_mask = Image.new("L", (W, H), 0)
    db = ImageDraw.Draw(bonus_mask)
    radius = min(3 * scale, bw / 2) * SS
    labels: list[tuple[float, float, str]] = []

    if has_data and selected is not None and 0 <= selected < n:
        x0 = selected * slot * SS
        d.rounded_rectangle((x0, 0, x0 + slot * SS, base_y), radius=3 * scale * SS,
                            fill=T.mix(T.TEXT, bg, 0.1))
        rr = 2 * scale * SS
        cx, cy = x0 + slot * SS / 2, base_y + (bottom * SS) / 2 + SS
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=T.TEXT)
    if has_data:
        avg = sum(values) / len(values)
        y = base_y - height(avg)
        dash = round(4 * scale) * SS
        x = 0
        while x < W:
            d.line((x, y, min(W, x + dash), y), fill=T.BORDER_STRONG, width=SS)
            x += dash * 2
    d.line((0, base_y, W, base_y), fill=T.BORDER_STRONG if has_data else T.BORDER,
           width=SS)

    for i, (g, bonus, text) in enumerate(history):
        cx = (i + 0.5) * slot * SS
        x0, x1 = cx - bw * SS / 2, cx + bw * SS / 2
        today = i == n - 1
        if not has_data:
            bh = plot * SS * 0.46 * _SKELETON[i % len(_SKELETON)]
            d.rounded_rectangle((x0, base_y - bh, x1, base_y), radius=radius,
                                fill=T.mix(T.SURFACE_2, bg, 0.75),
                                corners=(True, True, False, False))
            continue
        if g is None:
            rr = 1.5 * scale * SS
            d.ellipse((cx - rr, base_y - 5 * scale * SS - rr, cx + rr,
                       base_y - 5 * scale * SS + rr),
                      fill=T.TEXT_2 if hover == i else T.FAINT)
        else:
            bh = max(2 * scale * SS, height(g))
            box = (x0, base_y - bh, x1, base_y)
            if bonus:
                db.rounded_rectangle(box, radius=radius, fill=255,
                                     corners=(True, True, False, False))
                labels.append((cx / SS, (base_y - bh) / SS, text))
            else:
                color = T.GOLD if today else T.GOLD_DIM
                if hover == i:
                    color = T.mix(T.GOLD_HI, color, 0.55)
                d.rounded_rectangle(box, radius=radius, fill=color,
                                    corners=(True, True, False, False))
        if today:
            rr = 2 * scale * SS
            cy = base_y + (bottom * SS) / 2 + SS
            d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=T.GOLD)
    if labels:
        hi = T.TEXT if hover is not None and history[hover][1] else T.GOLD_HI
        img.paste(_gradient((W, H), hi, T.GOLD_HI, T.GOLD), mask=bonus_mask)
    img = img.resize((w, h), Image.LANCZOS)

    d = ImageDraw.Draw(img)
    if not has_data:
        title, sub = empty
        y = top + plot * 0.2
        if title:
            d.text((w / 2, y), title, font=font("text_semibold", round(13 * scale)),
                   fill=T.TEXT_2, anchor="mm")
        if sub:
            d.text((w / 2, y + 20 * scale), sub, font=font("text", round(12 * scale)),
                   fill=T.MUTED, anchor="mm")
        return img
    f = font("text_semibold", round(10.5 * scale))
    # Si dos cifras se pisan, gana la del pico más alto (y más arriba = menor y).
    taken: list[tuple[float, float]] = []
    gap = 4 * scale
    for cx, y, text in sorted(labels, key=lambda lb: lb[1]):
        if not text:
            continue
        tw = d.textlength(text, font=f)
        x = max(tw / 2, min(w - tw / 2, cx))
        left, right = x - tw / 2, x + tw / 2
        if any(left < r + gap and right > l - gap for l, r in taken):
            continue
        d.text((x, y - 4 * scale), text, font=f, fill=T.GOLD_HI, anchor="ms")
        taken.append((left, right))
    return img


# --- Moneda del nivel --------------------------------------------------------
_METALS = {
    "gold": (T.MEDAL_GOLD_HI, T.MEDAL_GOLD, T.MEDAL_GOLD_DEEP),
    "silver": ("#f4f6f8", T.SILVER, "#878e97"),
    "neutral": ("#e6bd97", T.BRONZE, "#7d5533"),
}


def coin(px: int, metal: str = "gold"):
    """Medalla pequeña (disco metálico con canto) para el nivel. RGBA."""
    def make():
        s = px * 4
        hi, mid, lo = _METALS.get(metal, _METALS["neutral"])
        disc = Image.new("L", (s, s), 0)
        ImageDraw.Draw(disc).ellipse((0, 0, s - 1, s - 1), fill=255)
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        img.paste(_gradient((s, s), hi, mid, lo, angle=35), mask=disc)
        d = ImageDraw.Draw(img)
        inset = s * 0.2
        d.ellipse((inset, inset, s - inset, s - inset), outline=lo,
                  width=max(1, round(s * 0.06)))
        return img.resize((px, px), Image.LANCZOS)
    return _remember(("coin", px, metal), make)


# --- Medallón de actividad, rachas y sellos ---------------------------------
def medallion(px: int, glyph: str, font_path: str | None, color: str, *,
              fraction: float = 0.0, dim: bool = False, bg: str = T.SURFACE,
              brand: bool = False, check: str = ""):
    """
    Icono de una actividad dentro de un disco, rodeado por un arco de progreso
    fino (`fraction` 0..1; 1 = aro completo). RGB sobre `bg`.
      · brand: estilo de marca — icono y arco en degradado cian→azul de Bing,
        con un halo suave bajo el icono; la pista del aro se ve siempre.
      · check: glifo de "hecho"; si se pasa, añade una insignia verde pequeña
        abajo a la derecha (el único verde del medallón).
      · dim: apagado (pendiente / no aplica). Sin `brand`, todo va en `color`.
    """
    fraction = max(0.0, min(1.0, float(fraction)))
    key = ("medal", px, glyph, font_path, color, round(fraction, 3), dim, bg, brand, check)

    def make():
        s = px * SS
        c = s / 2
        width = max(SS, round(s * 0.05))
        r = s / 2 - width / 2 - s * 0.03     # margen: el aro no toca el borde del lienzo
        base = T.GOLD if brand else color
        img = Image.new("RGB", (s, s), bg)
        d = ImageDraw.Draw(img)
        inner = r - width / 2 - s * 0.065
        d.ellipse((c - inner, c - inner, c + inner, c + inner),
                  fill=T.mix(base, bg, 0.07 if dim else 0.14))
        gold = _gradient((s, s), T.GOLD_HI, T.GOLD, T.GOLD_DEEP, angle=35)
        if fraction > 0 or (brand and not dim):
            track = Image.new("L", (s, s), 0)
            _arc(ImageDraw.Draw(track), c, c, r, width, 0, 360)
            img.paste(Image.new("RGB", (s, s), T.mix(base, bg, 0.2)), mask=track)
        if fraction > 0:
            arc = Image.new("L", (s, s), 0)
            _arc(ImageDraw.Draw(arc), c, c, r, width, 0, 360 * fraction)
            img.paste(gold if brand else Image.new("RGB", (s, s), color), mask=arc)
        if glyph and font_path:
            try:
                f = ImageFont.truetype(font_path, round(s * 0.36))
                mask = Image.new("L", (s, s), 0)
                ImageDraw.Draw(mask).text((c, c), glyph, font=f, anchor="mm", fill=255)
                if brand and not dim:
                    halo = mask.filter(ImageFilter.GaussianBlur(s * 0.06)).point(
                        lambda v: int(v * 0.55))
                    img.paste(Image.new("RGB", (s, s), T.GOLD), mask=halo)
                    img.paste(_gradient((s, s), "#ffffff", T.GOLD_HI, T.GOLD), mask=mask)
                else:
                    img.paste(Image.new("RGB", (s, s),
                                        T.mix(base, bg, 0.6) if dim else color), mask=mask)
            except Exception:
                pass
        if check and font_path:
            try:
                # La insignia va pegada a la esquina pero DENTRO del lienzo:
                # su cerco (rb + 2·SS) termina a SS del borde, así que no se
                # corta a ningún escalado.
                rb = s * 0.15
                bx = by = s - rb - 3 * SS - s * 0.02
                d = ImageDraw.Draw(img)
                d.ellipse((bx - rb - 2 * SS, by - rb - 2 * SS, bx + rb + 2 * SS,
                           by + rb + 2 * SS), fill=bg)
                d.ellipse((bx - rb, by - rb, bx + rb, by + rb), fill=T.tone("ok"))
                d.text((bx, by), check, font=ImageFont.truetype(font_path, round(rb * 1.25)),
                       anchor="mm", fill=T.mix("#000000", T.tone("ok"), 0.75))
            except Exception:
                pass
        return img.resize((px, px), Image.LANCZOS)
    return _remember(key, make)


def segments(w: int, h: int, filled: int, total: int = 7, *, bg: str = T.SURFACE):
    """Racha como `total` segmentos: los días hechos en azul, el de hoy (el
    último) en cian brillante y los que faltan, apagados."""
    total = max(1, int(total))
    filled = max(0, min(int(filled), total))
    key = ("segments", w, h, filled, total, bg)

    def make():
        W_, H_ = w * SS, h * SS
        img = Image.new("RGB", (W_, H_), bg)
        d = ImageDraw.Draw(img)
        gap = max(SS, round(H_ * 0.55))
        seg = (W_ - gap * (total - 1)) / total
        for i in range(total):
            x0 = i * (seg + gap)
            if i + 1 == filled:
                color = T.GOLD_HI
            elif i < filled:
                color = T.mix(T.GOLD, bg, 0.78)
            else:
                color = T.mix(T.SURFACE_3, bg, 0.9) if bg != T.SURFACE_3 else T.BORDER_STRONG
            d.rounded_rectangle((x0, 0, x0 + seg, H_ - 1), radius=H_ / 2, fill=color)
        return img.resize((w, h), Image.LANCZOS)
    return _remember(key, make)


def stamps(w: int, h: int, done: int | None, total: int = 12, *, bg: str = T.SURFACE):
    """Cartilla de sellos: `total` huecos redondos en fila; los conseguidos,
    discos en degradado azul con su cerco interior; los que faltan, solo el
    contorno. `done` None = sin datos (todos los huecos, más tenues)."""
    total = max(1, int(total))
    known = done is not None
    done = max(0, min(int(done or 0), total))
    key = ("stamps", w, h, done if known else None, total, bg)

    def make():
        W_, H_ = w * SS, h * SS
        img = Image.new("RGB", (W_, H_), bg)
        d = ImageDraw.Draw(img)
        dia = min(H_, (W_ + 0.0) / (total + (total - 1) * 0.32))
        gap = (W_ - dia * total) / max(1, total - 1)
        line = max(SS, round(dia * 0.09))
        mask = Image.new("L", (W_, H_), 0)
        dm = ImageDraw.Draw(mask)
        y0 = (H_ - dia) / 2
        boxes = [(i * (dia + gap), y0, i * (dia + gap) + dia, y0 + dia)
                 for i in range(total)]
        for i, box in enumerate(boxes):
            if i < done:
                dm.ellipse(box, fill=255)
            else:
                d.ellipse(box, outline=T.mix(T.MUTED, bg, 0.6 if known else 0.32), width=line)
        if done:
            img.paste(_gradient((W_, H_), T.GOLD_HI, T.GOLD, T.GOLD_DEEP), mask=mask)
            d = ImageDraw.Draw(img)
            for box in boxes[:done]:
                k = dia * 0.24
                d.ellipse((box[0] + k, box[1] + k, box[2] - k, box[3] - k),
                          outline=T.mix("#ffffff", T.GOLD, 0.55), width=line)
        return img.resize((w, h), Image.LANCZOS)
    return _remember(key, make)
