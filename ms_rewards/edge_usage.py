"""
Racha "Usa Microsoft Edge N minutos al día".

El dashboard muestra una tarjeta "Edge Minutos: X/30": una racha diaria que se
completa navegando con Edge (la mide el propio Edge, por tiempo de uso activo).
No hay nada que "clicar" para completarla — hay que ACUMULAR minutos de Edge.

Este módulo lee cuántos minutos faltan (desde la tarjeta del dashboard, legible
en cualquier navegador) y, SOLO si el bot corre con Edge (MSR_BROWSER=edge),
mantiene Edge navegando páginas ligeras el tiempo restante.

INCERTIDUMBRE: que Microsoft cuente el tiempo de un Edge conducido por patchright
depende de su telemetría de "uso activo"; conviene verificar en real que el
contador sube. Con Chrome se omite (esta racha requiere Edge).
"""
from __future__ import annotations

import logging
import random
import time

from patchright.async_api import BrowserContext, Page

import config
from humanize import human_scroll, safe_goto, sleep_jitter

log = logging.getLogger("edge_usage")

REWARDS_URL = "https://rewards.bing.com/dashboard"

# Páginas ligeras para navegar mientras se acumulan minutos de Edge.
_BROWSE_URLS = [
    "https://www.msn.com/es-es",
    "https://www.bing.com/",
    "https://www.bing.com/news",
    "https://www.msn.com/es-es/deportes",
    "https://www.bing.com/maps",
]

# Lee "Edge Minutos: X/30" de la tarjeta del dashboard.
_READ_JS = r"""
() => {
  for (const el of document.querySelectorAll('button, div, span, p, a')) {
    const t = (el.innerText || '').trim();
    const m = t.match(/Edge\s*Minutos?\s*:?\s*(\d+)\s*\/\s*(\d+)/i);
    if (m) return {cur: parseInt(m[1], 10), max: parseInt(m[2], 10)};
  }
  return null;
}
"""


async def _read_edge_minutes(page: Page):
    try:
        return await page.evaluate(_READ_JS)
    except Exception:
        return None


async def _browse_active(page: Page, minutes: float) -> None:
    """Navega páginas ligeras con scroll durante ~`minutes` minutos, en primer plano."""
    target_s = minutes * 60.0
    start = time.monotonic()
    while time.monotonic() - start < target_s:
        url = random.choice(_BROWSE_URLS)
        try:
            await safe_goto(page, url, attempts=2, timeout=20_000)
        except Exception:
            pass
        try:
            await page.bring_to_front()
        except Exception:
            pass
        # Bloque de "lectura" con scrolls, ~40-70s por página.
        block_end = time.monotonic() + random.uniform(40.0, 70.0)
        while time.monotonic() < block_end and time.monotonic() - start < target_s:
            try:
                await human_scroll(page, random.randint(300, 900))
            except Exception:
                pass
            await sleep_jitter(8.0, 13.0)
        elapsed_min = (time.monotonic() - start) / 60.0
        log.info("racha Edge: %.1f/%.1f min navegados", elapsed_min, minutes)


async def run_edge_minutes(context: BrowserContext) -> bool:
    """
    Completa la racha de minutos de Edge navegando el tiempo que falte. Solo con
    MSR_BROWSER=edge. Devuelve True si la racha quedó completa (o ya lo estaba).
    """
    if config.BROWSER != "edge":
        log.info("racha de Edge: omitida (navegador actual: %s; requiere Edge)",
                 config.BROWSER)
        return False

    page = context.pages[0] if context.pages else await context.new_page()
    if not await safe_goto(page, REWARDS_URL, attempts=4, timeout=25_000):
        log.warning("racha de Edge: no se pudo cargar el dashboard")
        return False
    await sleep_jitter(2.0, 4.0)

    info = await _read_edge_minutes(page)
    if not info:
        log.info("racha de Edge: no se encontró la tarjeta de minutos; se omite")
        return False
    cur, mx = info["cur"], info["max"]
    log.info("racha Edge minutos: %d/%d", cur, mx)
    if cur >= mx:
        log.info("racha de Edge ya completa hoy")
        return True

    remaining = mx - cur
    minutes = min(remaining + 1, int(config.EDGE_USAGE_MAX_MIN))  # +1 de margen
    log.info("navegando en Edge ~%d min para completar la racha (faltan %d)",
             minutes, remaining)
    await _browse_active(page, minutes)

    # Volver al dashboard y releer para confirmar.
    try:
        await safe_goto(page, REWARDS_URL, attempts=3, timeout=20_000)
        await sleep_jitter(2.0, 4.0)
        info2 = await _read_edge_minutes(page)
        if info2:
            log.info("racha Edge minutos tras navegar: %d/%d", info2["cur"], info2["max"])
            return info2["cur"] >= info2["max"]
    except Exception:
        pass
    return True
