"""
Info del usuario en Microsoft Rewards: nivel actual y puntos.

Desde el rediseño del dashboard (Next.js/Tailwind, sin selectores estables) la
única fuente fiable es la API interna, así que delegamos en dashboard_api:
  GET /api/getuserinfo?type=1 -> dashboard.userStatus.{availablePoints, levelInfo}

Antes esto parseaba el DOM del banner `mee-rewards-*` como fallback; ese DOM ya
no existe, por lo que el fallback se ha eliminado. Si la API no responde,
devolvemos None y el llamador asume un default.
"""
from __future__ import annotations

import logging
from typing import Any

from patchright.async_api import BrowserContext

import dashboard_api

log = logging.getLogger("rewards_info")


async def get_user_info(context: BrowserContext) -> dict[str, Any]:
    """
    Devuelve {'level': int|None, 'level_name': str|None, 'points': int|None,
    'source': 'api'|None} leyendo la API interna del dashboard.
    """
    dashboard = await dashboard_api.fetch_dashboard(context, navigate=True)
    info = dashboard_api.parse_user_info(dashboard)
    if info.get("level") or info.get("points") is not None:
        log.info(
            "nivel=%s (%s) puntos=%s [API]",
            info.get("level"), info.get("level_name"), info.get("points"),
        )
    else:
        log.warning("no se pudo leer nivel/puntos de /api/getuserinfo (¿sesión/rediseño?)")
    return info


async def get_level(context: BrowserContext) -> int | None:
    info = await get_user_info(context)
    return info.get("level")
