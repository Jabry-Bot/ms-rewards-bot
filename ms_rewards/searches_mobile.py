"""
Búsquedas móviles emulando Pixel 8 sobre el mismo Chrome de escritorio.

Bing decide si una búsqueda es "móvil" por User-Agent, Client Hints
(Sec-CH-UA-Mobile, Sec-CH-UA-Platform), viewport, touch, y navigator.platform.
Inyectamos los 4 vía CDP en una pestaña nueva del mismo perfil — la sesión
del usuario se mantiene porque las cookies pertenecen al contexto, no a la
pestaña.

Estrategia para minimizar detección:
  - Gap humano (5-15 min) entre desktop y móvil en run.py, no aquí.
  - Mismo set de delays humanizados que desktop.
  - Queries de pool específico para móvil (cerca de mí, voice-like).
  - Restauramos la emulación al terminar (clearDeviceMetricsOverride).
"""
from __future__ import annotations

import asyncio
import logging
import random
import urllib.parse

from patchright.async_api import BrowserContext, CDPSession, Page

import config
import selectors
from humanize import human_scroll, maybe_long_pause, safe_goto, sleep_jitter
from queries import generate_mobile_queries

log = logging.getLogger("searches.mobile")

# Pixel 8 — UA Chrome estable, Client Hints completos, viewport realista.
MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
)
MOBILE_VIEWPORT_W = 412
MOBILE_VIEWPORT_H = 915
MOBILE_DPR = 2.625

# Cliente Hints / UA-CH. Tienen que ir coherentes con MOBILE_UA — si Bing
# detecta UA con "Mobile" pero Sec-CH-UA-Mobile=?0, la búsqueda se cuenta
# como desktop.
_UA_METADATA = {
    "brands": [
        {"brand": "Chromium", "version": "130"},
        {"brand": "Google Chrome", "version": "130"},
        {"brand": "Not?A_Brand", "version": "99"},
    ],
    "fullVersion": "130.0.0.0",
    "platform": "Android",
    "platformVersion": "14",
    "architecture": "",
    "model": "Pixel 8",
    "mobile": True,
}


async def _enable_mobile_emulation(page: Page) -> CDPSession:
    """Activa emulación móvil completa sobre la pestaña indicada."""
    client = await page.context.new_cdp_session(page)
    await client.send(
        "Emulation.setUserAgentOverride",
        {
            "userAgent": MOBILE_UA,
            "acceptLanguage": "es-ES,es;q=0.9",
            "platform": "Linux armv8l",
            "userAgentMetadata": _UA_METADATA,
        },
    )
    await client.send(
        "Emulation.setDeviceMetricsOverride",
        {
            "width": MOBILE_VIEWPORT_W,
            "height": MOBILE_VIEWPORT_H,
            "deviceScaleFactor": MOBILE_DPR,
            "mobile": True,
            "screenOrientation": {"type": "portraitPrimary", "angle": 0},
        },
    )
    await client.send(
        "Emulation.setTouchEmulationEnabled",
        {"enabled": True, "maxTouchPoints": 5},
    )
    return client


async def _disable_mobile_emulation(client: CDPSession) -> None:
    for cmd, params in (
        ("Emulation.clearDeviceMetricsOverride", {}),
        ("Emulation.setUserAgentOverride", {"userAgent": ""}),
        ("Emulation.setTouchEmulationEnabled", {"enabled": False}),
    ):
        try:
            await client.send(cmd, params)
        except Exception:
            pass
    try:
        await client.detach()
    except Exception:
        pass


async def _verify_mobile(page: Page) -> bool:
    """Confirma que el navegador parece móvil desde JS."""
    try:
        info = await page.evaluate(
            """() => ({
                ua: navigator.userAgent,
                mobile: navigator.userAgentData ? navigator.userAgentData.mobile : null,
                touch: navigator.maxTouchPoints,
                width: window.innerWidth,
            })"""
        )
        log.info(
            "emulación móvil: width=%s touch=%s mobile_hint=%s ua~%s",
            info.get("width"), info.get("touch"), info.get("mobile"),
            (info.get("ua") or "")[:50],
        )
        is_ok = (
            "Mobile" in (info.get("ua") or "")
            and (info.get("touch") or 0) > 0
            and (info.get("width") or 0) < 600
        )
        return is_ok
    except Exception as exc:
        log.warning("no se pudo verificar emulación móvil: %s", exc)
        return False


async def _do_one_search(page: Page, query: str, idx: int, total: int) -> bool:
    url = "https://www.bing.com/search?" + urllib.parse.urlencode(
        {"q": query, "form": "QBLH"}
    )
    log.info("[%d/%d] busqueda movil: %r", idx, total, query)
    if not await safe_goto(page, url, attempts=3, timeout=22_000):
        log.error("busqueda movil %r no cargo", query)
        return False
    await sleep_jitter(*config.DELAYS["read_serp"])
    # En móvil, scroll es vertical y más amplio que desktop
    await human_scroll(page, random.randint(300, 900))
    await sleep_jitter(1.0, 3.0)
    # Click en resultado menos frecuente en móvil (suele bastar el snippet)
    if random.random() < 0.20:
        try:
            link = await page.query_selector(selectors.get("bing.organic_result"))
            if link:
                await link.click()
                await sleep_jitter(*config.DELAYS["on_result"])
                await human_scroll(page, random.randint(400, 900))
                try:
                    await page.go_back(wait_until="domcontentloaded", timeout=15_000)
                except Exception:
                    pass
                await sleep_jitter(1.5, 3.0)
        except Exception as exc:
            log.debug("click resultado móvil falló: %s", exc)
    return True


async def run_mobile_searches(
    context: BrowserContext, count: int | None = None
) -> int:
    count = count or config.MOBILE_SEARCH_COUNT
    queries = generate_mobile_queries(count)

    page = await context.new_page()
    client = await _enable_mobile_emulation(page)
    try:
        # Carga inicial bing.com en modo móvil
        if not await safe_goto(page, "https://www.bing.com/", attempts=6, timeout=20_000):
            log.warning("primera carga móvil falló")
            return 0
        await sleep_jitter(1.5, 3.5)
        await _verify_mobile(page)

        # Banner de cookies (en móvil el botón puede tener id distinto, pero
        # el selector compuesto cubre las variantes habituales).
        try:
            btn = await page.wait_for_selector(
                selectors.get("bing.cookies_accept"), timeout=2500
            )
            if btn:
                await btn.click()
                await sleep_jitter(0.6, 1.4)
        except Exception:
            pass

        done = 0
        for i, q in enumerate(queries, start=1):
            try:
                if await _do_one_search(page, q, i, count):
                    done += 1
            except Exception as exc:
                log.error("búsqueda móvil %d falló: %s", i, exc)

            if i == count:
                break
            low, high = config.DELAYS["between_searches"]
            wait = random.uniform(low, high)
            log.debug("móvil: durmiendo %.1fs", wait)
            await asyncio.sleep(wait)
            await maybe_long_pause(
                config.DELAYS["long_pause_prob"],
                *config.DELAYS["long_pause"],
            )

        log.info("búsquedas móviles completadas: %d/%d", done, count)
        return done
    finally:
        await _disable_mobile_emulation(client)
        try:
            await page.close()
        except Exception:
            pass
