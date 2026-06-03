"""
Obtener información del usuario en Microsoft Rewards: nivel actual y puntos.

Estrategia doble (defensiva):
  1) API interna: fetch a /api/getuserinfo?type=1 desde dentro de la página
     ya autenticada en rewards.bing.com. Si responde, leemos el JSON.
  2) Fallback DOM: parseamos el banner del header (`Nivel 2`, `Level 2`).

Si todo falla, devolvemos None y dejamos que el llamador asuma un default.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from patchright.async_api import BrowserContext, Page

from humanize import safe_goto, sleep_jitter

log = logging.getLogger("rewards_info")

REWARDS_HOME = "https://rewards.bing.com/"


_API_JS = """
async () => {
  try {
    const r = await fetch('/api/getuserinfo?type=1', {credentials: 'include'});
    if (!r.ok) return null;
    return await r.json();
  } catch (e) { return null; }
}
"""

_DOM_LEVEL_JS = """
() => {
  // Recoge el texto completo del banner / header de usuario.
  const candidates = [
    'mee-rewards-user-status-banner-component',
    'mee-rewards-user-status-component',
    '[class*="user-status"]',
    '[class*="UserStatus"]',
    'header',
  ];
  for (const sel of candidates) {
    const el = document.querySelector(sel);
    if (el && el.innerText) return el.innerText.slice(0, 400);
  }
  return document.body ? document.body.innerText.slice(0, 1200) : '';
}
"""


def _parse_level_from_text(text: str) -> int | None:
    if not text:
        return None
    # Acepta "Nivel 2", "Level 2", "Nivel2", "L2", "Tier 2"
    m = re.search(r"(?:nivel|level|tier|l)\s*([12])\b", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    return None


def _level_from_api(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    # estructura típica: payload.userStatus.levelInfo.activeLevel = "Level2"
    try:
        active = (
            payload.get("userStatus", {})
            .get("levelInfo", {})
            .get("activeLevel", "")
        )
        if isinstance(active, str):
            m = re.search(r"([12])", active)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    # variante: payload.dashboard.userStatus.levelInfo.activeLevel
    try:
        active = (
            payload.get("dashboard", {})
            .get("userStatus", {})
            .get("levelInfo", {})
            .get("activeLevel", "")
        )
        if isinstance(active, str):
            m = re.search(r"([12])", active)
            if m:
                return int(m.group(1))
    except Exception:
        pass
    return None


def _points_from_api(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    try:
        pts = payload.get("userStatus", {}).get("availablePoints")
        if isinstance(pts, int):
            return pts
    except Exception:
        pass
    return None


async def _ensure_on_dashboard(context: BrowserContext) -> Page | None:
    """Devuelve una página cargada en rewards.bing.com (la primera disponible)."""
    page = context.pages[0] if context.pages else await context.new_page()
    if "rewards." not in (page.url or ""):
        if not await safe_goto(page, REWARDS_HOME, attempts=4, timeout=20_000):
            return None
        await sleep_jitter(2.0, 4.0)
    return page


async def get_user_info(context: BrowserContext) -> dict[str, Any]:
    """
    Devuelve {'level': int|None, 'points': int|None, 'source': 'api'|'dom'|None}.
    """
    page = await _ensure_on_dashboard(context)
    if not page:
        return {"level": None, "points": None, "source": None}

    # 1) API
    try:
        payload = await page.evaluate(_API_JS)
        level = _level_from_api(payload)
        points = _points_from_api(payload)
        if level:
            log.info("nivel detectado vía API: %d (puntos=%s)", level, points)
            return {"level": level, "points": points, "source": "api"}
    except Exception as exc:
        log.debug("API getuserinfo falló: %s", exc)

    # 2) DOM
    try:
        text = await page.evaluate(_DOM_LEVEL_JS)
        level = _parse_level_from_text(text or "")
        if level:
            log.info("nivel detectado vía DOM: %d", level)
            return {"level": level, "points": None, "source": "dom"}
        log.warning("no se pudo detectar el nivel (texto: %r)", (text or "")[:120])
    except Exception as exc:
        log.debug("parseo DOM de nivel falló: %s", exc)

    return {"level": None, "points": None, "source": None}


async def get_level(context: BrowserContext) -> int | None:
    info = await get_user_info(context)
    return info.get("level")
