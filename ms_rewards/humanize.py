"""Utilidades para que el bot se mueva, escriba y lea como un humano."""
from __future__ import annotations

import asyncio
import logging
import math
import random
from typing import Iterable

from patchright.async_api import Page

log = logging.getLogger("humanize")


# Errores de red transitorios típicos justo después de un arranque en frío de
# Chrome: el servicio de red todavía no ha levantado el resolver y la primera
# navegación devuelve ERR_NAME_NOT_RESOLVED aunque el DNS del sistema esté bien.
_TRANSIENT_NET = (
    "ERR_NAME_NOT_RESOLVED",
    "ERR_NETWORK_CHANGED",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_CONNECTION_RESET",
    "ERR_CONNECTION_CLOSED",
    "ERR_TIMED_OUT",
    "ERR_ABORTED",
)


async def safe_goto(
    page: Page,
    url: str,
    *,
    attempts: int = 4,
    wait_until: str = "domcontentloaded",
    timeout: int = 22_000,
) -> bool:
    """
    Navega a `url` reintentando ante fallos de red transitorios.

    Devuelve True si la carga tuvo éxito, False si se agotaron los intentos.
    Pensado sobre todo para la PRIMERA navegación tras abrir Chrome, que a
    veces falla con ERR_NAME_NOT_RESOLVED hasta que el servicio de red está
    completamente listo.
    """
    last: Exception | None = None
    for i in range(attempts):
        try:
            await page.goto(url, wait_until=wait_until, timeout=timeout)
            if i:
                log.info("navegación a %s OK tras %d reintento/s", url, i)
            return True
        except Exception as exc:  # noqa: BLE001 - queremos clasificar el mensaje
            last = exc
            msg = str(exc)
            transient = any(code in msg for code in _TRANSIENT_NET)
            if not transient and i >= 1:
                # Error no transitorio (p.ej. timeout de DOM en página pesada):
                # un reintento basta, no insistas las 4 veces.
                break
            backoff = random.uniform(2.5, 4.5) * (i + 1)
            log.warning(
                "navegación a %s falló (intento %d/%d): %s — reintento en %.1fs",
                url, i + 1, attempts, msg.splitlines()[0][:120], backoff,
            )
            await asyncio.sleep(backoff)
    log.error("navegación a %s agotó %d intentos: %s", url, attempts, last)
    return False


async def sleep_jitter(low: float, high: float) -> None:
    """Sleep aleatorio uniforme — el ladrillo de todos los timeouts."""
    await asyncio.sleep(random.uniform(low, high))


async def maybe_long_pause(prob: float, low: float, high: float) -> bool:
    """Con probabilidad `prob`, hace una pausa larga (simula AFK)."""
    if random.random() < prob:
        secs = random.uniform(low, high)
        log.info("pausa larga %.1fs (simulando AFK)", secs)
        await asyncio.sleep(secs)
        return True
    return False


async def bezier_mouse_move(
    page: Page, x0: int, y0: int, x1: int, y1: int, steps: int = 22
) -> None:
    """Curva cuadrática Bézier — evita líneas perfectamente rectas."""
    cx = random.randint(min(x0, x1), max(x0, x1) + 1)
    cy = random.randint(min(y0, y1) - 40, max(y0, y1) + 40)
    for i in range(1, steps + 1):
        t = i / steps
        # Easing: empieza lento, acelera, frena al final
        eased = 0.5 - math.cos(t * math.pi) / 2
        x = int((1 - eased) ** 2 * x0 + 2 * (1 - eased) * eased * cx + eased ** 2 * x1)
        y = int((1 - eased) ** 2 * y0 + 2 * (1 - eased) * eased * cy + eased ** 2 * y1)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.006, 0.020))


async def human_scroll(page: Page, total_px: int) -> None:
    """Scroll con perfil de aceleración natural."""
    if total_px == 0:
        return
    weights = [1, 2, 3, 4, 4, 3, 2, 1]
    if total_px < 0:
        weights = list(reversed(weights))
    total_w = sum(weights)
    for w in weights:
        delta = int(total_px * w / total_w)
        if delta == 0:
            continue
        try:
            await page.mouse.wheel(0, delta)
        except Exception:
            return
        await asyncio.sleep(random.uniform(0.05, 0.18))


async def human_read(page: Page, low: float = 3.0, high: float = 9.0) -> None:
    """Lee la página: pausa + algún scroll suave + deriva del ratón."""
    await sleep_jitter(low * 0.4, low * 0.8)
    vp = page.viewport_size or {"width": 1280, "height": 720}
    w, h = vp["width"], vp["height"]
    # 1-3 mini-scrolls con pausa entre ellos
    for _ in range(random.randint(1, 3)):
        await human_scroll(page, random.randint(180, 520))
        await sleep_jitter(0.8, 2.2)
    # deriva del ratón
    try:
        await bezier_mouse_move(
            page,
            random.randint(80, w - 80),
            random.randint(80, h - 80),
            random.randint(80, w - 80),
            random.randint(80, h - 80),
            steps=random.randint(14, 26),
        )
    except Exception:
        pass
    await sleep_jitter(low * 0.4, high * 0.4)


async def human_type(page: Page, selector: str, text: str) -> None:
    """Escribe carácter a carácter con delays realistas y typos ocasionales."""
    el = await page.wait_for_selector(selector, timeout=8000)
    if el is None:
        raise RuntimeError(f"selector no encontrado: {selector}")
    await el.click()
    await sleep_jitter(0.15, 0.45)
    # 5% prob de typo al inicio
    if random.random() < 0.05 and len(text) > 4:
        typo_idx = random.randint(1, min(4, len(text) - 1))
        wrong = random.choice("abcdefghijklmnopqrstuvwxyz")
        await page.keyboard.type(text[:typo_idx] + wrong, delay=random.randint(60, 140))
        await sleep_jitter(0.2, 0.6)
        await page.keyboard.press("Backspace")
        await sleep_jitter(0.1, 0.35)
        await page.keyboard.type(text[typo_idx:], delay=random.randint(70, 150))
        return
    # Tipeo normal con velocidad variable
    for ch in text:
        await page.keyboard.type(ch)
        delay = random.uniform(0.055, 0.16)
        # 8% prob micro-pausa (como pensando)
        if random.random() < 0.08:
            delay += random.uniform(0.25, 0.9)
        await asyncio.sleep(delay)


async def random_mouse_drift(page: Page, moves: int = 2) -> None:
    """Mueve el ratón sin propósito — simula el roce involuntario."""
    vp = page.viewport_size or {"width": 1280, "height": 720}
    w, h = vp["width"], vp["height"]
    for _ in range(moves):
        try:
            await bezier_mouse_move(
                page,
                random.randint(50, w - 50),
                random.randint(50, h - 50),
                random.randint(50, w - 50),
                random.randint(50, h - 50),
                steps=random.randint(12, 22),
            )
        except Exception:
            return
        await sleep_jitter(0.3, 1.1)


def chunk_shuffle(items: Iterable[str]) -> list[str]:
    """Mezcla preservando algo de localidad — no es full random."""
    items = list(items)
    random.shuffle(items)
    return items
