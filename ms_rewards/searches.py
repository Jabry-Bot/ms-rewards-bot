"""
Rutina de búsquedas en Bing para obtener los puntos diarios.

Diseño:
  - Una pestaña reusada para todas las búsquedas (más natural que abrir/cerrar)
  - Cada búsqueda va vía la URL `bing.com/search?q=...&form=QBLH` para parecer
    una búsqueda de la barra de direcciones (Bing premia más esa fuente).
  - Lectura aleatoria de la SERP, scroll variable
  - A veces se hace click en un resultado y se vuelve atrás
  - Pausas entre búsquedas con probabilidad de "AFK" larga
"""
from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse

from patchright.async_api import BrowserContext, Page

import config
import selectors
from humanize import (
    bezier_mouse_move,
    human_read,
    human_scroll,
    maybe_long_pause,
    random_mouse_drift,
    safe_goto,
    sleep_jitter,
)
from queries import generate_queries

log = logging.getLogger("searches")


async def _ensure_bing_loaded(page: Page) -> None:
    """Primera carga de Bing — acepta cookies si aparece el banner."""
    # Esta es la primera navegación tras abrir Chrome: usamos reintentos
    # generosos porque el resolver de red puede no estar listo todavía.
    if not await safe_goto(page, "https://www.bing.com/", attempts=6, timeout=20_000):
        log.warning("primera carga de bing falló tras varios intentos")
        return
    # Banner de cookies (EU) — selector compuesto desde selectors.json
    try:
        btn = await page.wait_for_selector(
            selectors.get("bing.cookies_accept"), timeout=2500
        )
        if btn:
            await btn.click()
            log.info("cookies aceptadas")
            await sleep_jitter(0.6, 1.4)
    except Exception:
        pass
    await human_read(page, 2.0, 5.0)


async def _do_one_search(page: Page, query: str, idx: int, total: int) -> None:
    """
    Ejecuta una búsqueda. Hacemos navigate directo a /search?q=...&form=QBLH
    porque el form QBLH (omnibox/address-bar) cuenta para los puntos igual que
    una escrita en el cuadro, y es más fiable que pelearse con el DOM de Bing.
    """
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "form": "QBLH"}
    )
    log.info("[%d/%d] búsqueda: %r", idx, total, query)

    # Pequeño "warm-up" de ratón antes de cada navegación
    if random.random() < 0.6:
        await random_mouse_drift(page, moves=random.randint(1, 2))

    if not await safe_goto(page, url, attempts=3, timeout=22_000):
        log.error("búsqueda %r no se pudo cargar", query)
        return

    # Leer la SERP
    await sleep_jitter(*config.DELAYS["read_serp"])
    await human_scroll(page, random.randint(200, 700))
    await sleep_jitter(1.0, 3.0)

    # Con cierta probabilidad, hacer click en un resultado orgánico y volver
    if random.random() < config.DELAYS["click_result_prob"]:
        try:
            # Bing renderiza resultados orgánicos como <li class="b_algo"><h2><a>
            link = await page.query_selector(selectors.get("bing.organic_result"))
            if link:
                vp = page.viewport_size or {"width": 1280, "height": 720}
                box = await link.bounding_box()
                if box:
                    await bezier_mouse_move(
                        page,
                        random.randint(80, vp["width"] - 80),
                        random.randint(80, vp["height"] // 3),
                        int(box["x"] + box["width"] / 2),
                        int(box["y"] + box["height"] / 2),
                        steps=random.randint(18, 28),
                    )
                    await sleep_jitter(0.3, 0.9)
                # No queremos cambiar de pestaña; Ctrl+click abriría una nueva,
                # y un click normal navega — mejor click normal y luego volver.
                await link.click()
                await sleep_jitter(*config.DELAYS["on_result"])
                await human_scroll(page, random.randint(300, 900))
                await sleep_jitter(2.0, 4.5)
                # Volver atrás
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass
                await sleep_jitter(1.5, 3.0)
        except Exception as exc:
            log.debug("click en resultado falló (ignorando): %s", exc)


async def run_searches(context: BrowserContext, count: int | None = None) -> int:
    """Ejecuta `count` búsquedas. Devuelve el número que se completó."""
    count = count or config.SEARCH_COUNT
    queries = generate_queries(count)

    # Reutilizamos una pestaña si ya hay alguna abierta (la del rewards)
    pages = context.pages
    if pages:
        page = pages[0]
    else:
        page = await context.new_page()

    await _ensure_bing_loaded(page)

    done = 0
    for i, q in enumerate(queries, start=1):
        try:
            await _do_one_search(page, q, i, count)
            done += 1
        except Exception as exc:
            log.error("búsqueda %d falló: %s", i, exc)

        if i == count:
            break

        # Pausa entre búsquedas
        low, high = config.DELAYS["between_searches"]
        wait = random.uniform(low, high)
        log.debug("durmiendo %.1fs antes de la siguiente", wait)
        await asyncio.sleep(wait)

        # Pausa larga ocasional
        await maybe_long_pause(
            config.DELAYS["long_pause_prob"],
            *config.DELAYS["long_pause"],
        )

    log.info("búsquedas completadas: %d/%d", done, count)
    return done
