"""
Pool de queries realistas para las búsquedas de Bing.

Mezclamos categorías (noticias, recetas, deportes, tecnología, viajes, cultura,
preguntas casuales) para que el patrón de búsqueda parezca el de una persona
normal. Cada query también puede sufrir variaciones (typo, abreviación, añadir
"qué es") para evitar repetición exacta entre días.
"""
from __future__ import annotations

import random
from datetime import date

_BASE_QUERIES: list[str] = [
    # Tiempo y cotidiano
    "tiempo madrid mañana",
    "tiempo en barcelona",
    "previsión lluvia esta semana",
    "polen madrid hoy",
    "calidad del aire valencia",
    # Recetas y comida
    "receta lentejas tradicional",
    "tortilla de patata jugosa",
    "como hacer pan en casa",
    "ideas cena rapida saludable",
    "tarta de queso al horno",
    "marinado para pollo",
    "ensaladilla rusa receta",
    "merienda saludable niños",
    # Tecnología
    "diferencia ssd y hdd",
    "como liberar espacio en windows 11",
    "mejor antivirus gratis 2026",
    "atajos de teclado windows",
    "qué es chatgpt",
    "como hacer copia de seguridad iphone",
    "iphone 17 review",
    # Deporte
    "resultado real madrid hoy",
    "liga española clasificacion",
    "champions league cuartos",
    "moto gp calendario 2026",
    "tour de francia recorrido",
    "alcaraz proximo partido",
    # Noticias / cultura
    "noticias hoy españa",
    "ultima hora europa",
    "premios goya ganadores",
    "estrenos cine este finde",
    "series para ver en netflix",
    "libro mas vendido 2026",
    "festivales musica verano",
    # Viajes
    "que ver en lisboa fin de semana",
    "vuelos baratos a roma",
    "playas mas bonitas mallorca",
    "ruta por asturias en coche",
    "que ver en sevilla 2 dias",
    "mejor epoca viajar a japon",
    # Salud y bienestar
    "ejercicios para dolor espalda",
    "cuanto dormir adulto",
    "vitamina d alimentos",
    "remedios caseros para resfriado",
    "que es la melatonina",
    "yoga para principiantes",
    # Hogar y bricolaje
    "como quitar manchas de vino",
    "ideas para pintar salon",
    "plantas resistentes interior",
    "limpiar microondas vinagre",
    "como cambiar enchufe",
    # Curiosidades / educativas
    "cuantos planetas tiene el sistema solar",
    "que es la inteligencia artificial",
    "biografia frida kahlo",
    "guerra civil española resumen",
    "cuanto pesa la luna",
    "porque el cielo es azul",
    "diferencia mar y oceano",
    # Compras / consumo
    "ofertas amazon hoy",
    "comparar tarifas movil",
    "mejor robot aspirador 2026",
    "como reclamar a una aerolinea",
    "donde comprar entradas baratas",
    # Trabajo / dinero
    "como hacer la declaracion de la renta",
    "que es el ibex 35",
    "interes hipoteca media",
    "trabajos remotos desde casa",
    "como pedir cita en hacienda",
]

_PREFIXES = ["", "", "", "", "que es ", "como ", "donde ", "cual es "]
_SUFFIXES = ["", "", "", "", " 2026", " explicado", " paso a paso", " gratis"]


def _maybe_variant(q: str) -> str:
    """Aplica una variación leve a la query — distinto cada día."""
    # 25% prob de añadir prefijo
    if random.random() < 0.25 and not any(q.startswith(p) for p in _PREFIXES if p):
        q = random.choice([p for p in _PREFIXES if p]) + q
    # 20% prob de sufijo
    if random.random() < 0.20:
        q = q + random.choice([s for s in _SUFFIXES if s])
    return q.strip()


def generate_queries(n: int, *, seed: int | None = None) -> list[str]:
    """
    Devuelve N queries únicas. Si N > pool, repite con variaciones distintas.
    """
    rng = random.Random(seed if seed is not None else date.today().toordinal())
    pool = _BASE_QUERIES.copy()
    rng.shuffle(pool)
    out: list[str] = []
    i = 0
    seen: set[str] = set()
    while len(out) < n:
        base = pool[i % len(pool)]
        # Cuando damos otra vuelta al pool, forzamos variación
        force_variant = i >= len(pool)
        q = _maybe_variant(base) if (force_variant or random.random() < 0.35) else base
        if q in seen:
            q = _maybe_variant(base + " " + str(rng.randint(2020, 2026)))
        seen.add(q)
        out.append(q)
        i += 1
    return out


# Queries más típicas de móvil: locales, "cerca de mí", consultas cortas
# tipo voice-search, conversiones rápidas.
_MOBILE_QUERIES: list[str] = [
    "farmacia 24h cerca",
    "gasolineras baratas cerca",
    "supermercado abierto ahora",
    "hora en tokio",
    "cuanto son 50 euros en dolares",
    "convertir libras a euros",
    "tiempo mañana",
    "metro madrid horario",
    "atasco m30",
    "restaurante japones cerca",
    "cines cerca de mi",
    "cuanto tarda un huevo cocido",
    "que canal echa el partido hoy",
    "como hacer captura iphone",
    "mejor app para correr",
    "donde votar",
    "horario farmacia guardia",
    "wifi gratis cerca",
    "como llegar al aeropuerto",
    "spotify offline",
    "instagram no funciona",
    "calendario laboral 2026",
    "horoscopo de hoy",
    "loteria primitiva resultado",
    "euromillones resultado",
    "renfe estado tren",
    "uber estimacion precio",
    "bizum como funciona",
    "iban a swift",
    "test antigeno donde",
]


def generate_mobile_queries(n: int, *, seed: int | None = None) -> list[str]:
    """
    Pool específico de queries 'mobile': locales, cortas, voice-search.
    Mezcla con _BASE_QUERIES (40%) para variedad. Usa seed distinto al
    desktop por defecto para no repetir queries entre las dos pasadas.
    """
    rng = random.Random(seed if seed is not None else (date.today().toordinal() + 991))
    mobile_pool = _MOBILE_QUERIES.copy()
    base_pool = _BASE_QUERIES.copy()
    rng.shuffle(mobile_pool)
    rng.shuffle(base_pool)
    out: list[str] = []
    seen: set[str] = set()
    mi = bi = 0
    while len(out) < n:
        # ~60% del pool móvil, ~40% del pool desktop
        if rng.random() < 0.6 and mi < len(mobile_pool):
            q = mobile_pool[mi]
            mi += 1
        else:
            q = base_pool[bi % len(base_pool)]
            bi += 1
            if bi > len(base_pool) and rng.random() < 0.5:
                q = _maybe_variant(q)
        if q in seen:
            q = _maybe_variant(q + " " + str(rng.randint(2020, 2026)))
        seen.add(q)
        out.append(q)
    return out
