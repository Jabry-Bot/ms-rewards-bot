"""
Info del usuario en Microsoft Rewards: nivel actual y puntos.

Desde el rediseño del dashboard (Next.js/Tailwind, sin selectores estables) la
única fuente fiable es la API interna, así que delegamos en dashboard_api:
  GET /api/getuserinfo?type=1 -> dashboard.userStatus.{availablePoints, levelInfo}

Antes esto parseaba el DOM del banner `mee-rewards-*` como fallback; ese DOM ya
no existe, por lo que el fallback se ha eliminado. Si la API no responde,
devolvemos None y el llamador asume un default.

CANJES. Cada lectura de saldo se apunta en el log ("puntos=N") y el panel
calcula lo ganado restando lecturas seguidas. Si entre dos lecturas se canjea
algo (aunque sea a mano, desde el móvil, a mitad de una corrida), el saldo baja
y no se sabe cuánto se ganó. Por eso, si el último pedido de la cuenta es
posterior a la lectura anterior, se apunta ANTES del saldo una línea
    canje en Rewards: 10095 pts (Tarjeta regalo de XBOX…, 22/11 13:45)
que el panel descuenta aparte. La hora de la lectura anterior se guarda en
state/balance_seen.json.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from patchright.async_api import BrowserContext

import config
import dashboard_api

log = logging.getLogger("rewards_info")

SEEN_FILE = "balance_seen.json"


def redemption_since(order: dict[str, Any] | None, last_seen: datetime | None,
                     logged_id: str = "") -> dict[str, Any] | None:
    """El último pedido si se hizo después de la lectura de saldo anterior y no
    se apuntó ya (función pura). Sin lectura anterior conocida no se puede
    saber: None."""
    if not order or last_seen is None:
        return None
    at = order.get("at")
    if not isinstance(at, datetime) or at <= last_seen:
        return None
    if logged_id and order.get("id") == logged_id:
        return None
    return order


def format_redemption(order: dict[str, Any]) -> str:
    """Línea del log que el panel reconoce (ver panel/core.py, _LOG_BALANCE)."""
    when = order["at"].astimezone().strftime("%d/%m %H:%M")
    title = order.get("title") or "pedido"
    return f"canje en Rewards: {order['price']} pts ({title}, {when})"


def _read_seen() -> tuple[datetime | None, str]:
    """(hora de la última lectura de saldo, id del último canje apuntado)."""
    try:
        raw = json.loads((config.STATE_DIR / SEEN_FILE).read_text(encoding="utf-8"))
    except Exception:
        return None, ""
    try:
        at = datetime.fromisoformat(str(raw.get("at")))
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        at = None
    return at, str(raw.get("order") or "")


def _write_seen(now: datetime, order_id: str) -> None:
    try:
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        (config.STATE_DIR / SEEN_FILE).write_text(
            json.dumps({"at": now.isoformat(timespec="seconds"), "order": order_id}),
            encoding="utf-8")
    except Exception as exc:
        log.debug("no se pudo guardar %s: %s", SEEN_FILE, exc)


async def get_user_info(context: BrowserContext) -> dict[str, Any]:
    """
    Devuelve {'level': int|None, 'level_name': str|None, 'points': int|None,
    'source': 'api'|None, 'pc_search', 'last_order', 'redeemed'} leyendo la API
    interna del dashboard. 'redeemed' es el último pedido si se canjeó desde la
    lectura anterior (None si no).
    """
    dashboard = await dashboard_api.fetch_dashboard(context, navigate=True)
    info = dashboard_api.parse_user_info(dashboard)
    info["redeemed"] = None
    if info.get("level") or info.get("points") is not None:
        now = datetime.now(timezone.utc)
        seen_at, logged_id = _read_seen()
        order = redemption_since(info.get("last_order"), seen_at, logged_id)
        if order is not None:
            info["redeemed"] = order
            log.info(format_redemption(order))
        log.info(
            "nivel=%s (%s) puntos=%s [API]",
            info.get("level"), info.get("level_name"), info.get("points"),
        )
        if info.get("points") is not None:
            _write_seen(now, order["id"] if order else logged_id)
    else:
        log.warning("no se pudo leer nivel/puntos de /api/getuserinfo (¿sesión/rediseño?)")
    return info


async def get_level(context: BrowserContext) -> int | None:
    info = await get_user_info(context)
    return info.get("level")
