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
    # --- Ampliación del pool (más diversidad de temas) ---
    # Tiempo / naturaleza
    "previsión meteorológica fin de semana",
    "cuando empieza el verano",
    "fases de la luna este mes",
    "temperatura del mar hoy",
    "cuando cambia la hora",
    # Recetas y comida
    "bizcocho esponjoso casero",
    "salsa carbonara autentica",
    "croquetas de jamon caseras",
    "gazpacho andaluz receta",
    "pollo al horno con patatas",
    "arroz caldoso de marisco",
    "postres faciles sin horno",
    "pan de centeno casero",
    "menu semanal saludable",
    "tortitas americanas receta",
    # Tecnología
    "como acelerar el movil android",
    "mejor portatil calidad precio",
    "diferencia wifi 5 y wifi 6",
    "como recuperar fotos borradas",
    "que es la computacion cuantica",
    "comparativa iphone y android",
    "como proteger mi cuenta de google",
    "mejores auriculares inalambricos",
    "que es una vpn y para que sirve",
    "como bloquear llamadas spam",
    # Deporte
    "calendario liga 2026",
    "quien gano el balon de oro",
    "nba resultados ayer",
    "mundial de atletismo fechas",
    "formula 1 proxima carrera",
    "clasificacion mundial tenis",
    "horario maraton de valencia",
    # Noticias / cultura
    "estrenos disney plus este mes",
    "premios oscar nominados",
    "mejores libros de 2026",
    "exposiciones madrid este mes",
    "conciertos barcelona proximos",
    "que ver en hbo max",
    "documentales recomendados netflix",
    # Viajes
    "que ver en oporto en 3 dias",
    "mejores playas de cadiz",
    "ruta del cares asturias",
    "vuelos baratos a paris",
    "que llevar a un viaje a noruega",
    "pueblos bonitos de españa",
    "visitar la alhambra entradas",
    "mejor epoca para viajar a tailandia",
    # Salud y bienestar
    "beneficios de caminar a diario",
    "cuanta agua beber al dia",
    "alimentos ricos en hierro",
    "ejercicios para fortalecer rodillas",
    "como mejorar el sueño",
    "que es el ayuno intermitente",
    "estiramientos antes de correr",
    "propiedades del jengibre",
    # Hogar y bricolaje
    "como eliminar la humedad de casa",
    "trucos para limpiar cristales",
    "como montar una estanteria",
    "plantas que purifican el aire",
    "como ahorrar en la factura de la luz",
    "quitar olor a humedad de la ropa",
    "como desatascar un fregadero",
    # Curiosidades / educativas
    "por que bostezamos",
    "cuanto mide la torre eiffel",
    "como se forman los arcoiris",
    "que es el agujero negro",
    "biografia de leonardo da vinci",
    "cuantos huesos tiene el cuerpo humano",
    "por que el mar es salado",
    "que es el efecto invernadero",
    "historia de la antigua roma",
    # Compras / consumo
    "mejores moviles calidad precio 2026",
    "comparar seguros de coche",
    "ofertas en electrodomesticos",
    "como devolver un pedido online",
    "mejor television 4k del momento",
    "donde comprar zapatillas baratas",
    # Trabajo / dinero / trámites
    "como hacer un curriculum",
    "que es el plan de pensiones",
    "como darse de alta autonomo",
    "subsidio por desempleo requisitos",
    "como calcular el finiquito",
    "que es la inflacion explicado",
    "ayudas para autonomos 2026",
]

_PREFIXES = ["", "", "", "", "que es ", "como ", "donde ", "cual es "]
_SUFFIXES = ["", "", "", "", " 2026", " explicado", " paso a paso", " gratis"]


_NONEMPTY_PREFIXES = [p for p in _PREFIXES if p]
_NONEMPTY_SUFFIXES = [s for s in _SUFFIXES if s]


def _maybe_variant(
    q: str,
    rng: random.Random,
    *,
    prob_prefix: float = 0.28,
    prob_suffix: float = 0.55,
) -> str:
    """
    Aplica una variación a la query usando el `rng` recibido (con seed),
    no el `random` global — así la generación es determinista por día pero
    varía de un día a otro. Antes usaba `random` global, lo que hacía que
    las variantes apenas se aprovecharan y el pool efectivo se redujera a
    las bases "peladas".
    """
    if rng.random() < prob_prefix and not any(q.startswith(p) for p in _NONEMPTY_PREFIXES):
        q = rng.choice(_NONEMPTY_PREFIXES) + q
    if rng.random() < prob_suffix:
        q = q + rng.choice(_NONEMPTY_SUFFIXES)
    return q.strip()


def generate_queries(n: int, *, seed: int | None = None) -> list[str]:
    """
    Devuelve N queries únicas. Aplica variantes de forma agresiva (≈85%)
    para aprovechar las ~3000+ combinaciones posibles en vez de rotar solo
    sobre las bases, reduciendo drásticamente la repetición entre días.
    """
    rng = random.Random(seed if seed is not None else date.today().toordinal())
    pool = _BASE_QUERIES.copy()
    rng.shuffle(pool)
    out: list[str] = []
    i = 0
    seen: set[str] = set()
    while len(out) < n:
        base = pool[i % len(pool)]
        # Variante con alta probabilidad; forzada al dar otra vuelta al pool.
        force_variant = i >= len(pool)
        q = _maybe_variant(base, rng) if (force_variant or rng.random() < 0.85) else base
        if q in seen:
            # Buscar una variante distinta antes de recurrir al fallback de año
            for _ in range(8):
                cand = _maybe_variant(base, rng, prob_prefix=0.7, prob_suffix=0.7)
                if cand not in seen:
                    q = cand
                    break
            else:
                q = _maybe_variant(base + " " + str(rng.randint(2020, 2026)), rng)
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
                q = _maybe_variant(q, rng)
        if q in seen:
            for _ in range(8):
                cand = _maybe_variant(q, rng, prob_prefix=0.7, prob_suffix=0.7)
                if cand not in seen:
                    q = cand
                    break
            else:
                q = _maybe_variant(q + " " + str(rng.randint(2020, 2026)), rng)
        seen.add(q)
        out.append(q)
    return out
