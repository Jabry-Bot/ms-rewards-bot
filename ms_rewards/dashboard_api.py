"""
Lectura del dashboard de Microsoft Rewards vía la API interna.

Contexto (rediseño 2026-07): el dashboard de rewards.bing.com pasó de Angular
(`mee-rewards-*`) a Next.js/Tailwind, sin selectores CSS estables. Scrapear las
cards por DOM dejó de funcionar. Pero la API interna sigue intacta:

  GET /api/getuserinfo?type=1  ->  { "dashboard": { ... } }

con la estructura clásica:
  dashboard.userStatus.availablePoints                     (puntos)
  dashboard.userStatus.levelInfo.activeLevel = "newLevel3" (nivel: 3 tramos)
  dashboard.dailySetPromotions["MM/DD/YYYY"] -> [3 items]  (daily set del día)
  dashboard.morePromotions -> [...]                        (más actividades)
  dashboard.punchCards -> [{childPromotions:[...]}]        (punch cards)

Cada actividad que da puntos es promotionType "urlreward" y se completa
VISITANDO su `destinationUrl` (una búsqueda de Bing con filtros de tracking).
Este módulo NO ejecuta nada: solo lee y normaliza. daily.py consume
`pending_activities()` y visita cada destino; rewards_info.py consume
`parse_user_info()`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any

from patchright.async_api import BrowserContext, Page

from humanize import safe_goto, sleep_jitter

log = logging.getLogger("dashboard_api")

# Root de rewards; redirige a /dashboard. La API es relativa al origen, así que
# basta con estar en cualquier página de rewards.bing.com.
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


# --- Modelo de una actividad ejecutable -----------------------------------
@dataclass(frozen=True)
class Activity:
    offer_id: str
    title: str
    destination_url: str
    source: str           # "daily" | "more" | "punch"
    points_max: int


# --- Navegación + fetch ----------------------------------------------------
async def _ensure_on_rewards(context: BrowserContext) -> Page | None:
    page = context.pages[0] if context.pages else await context.new_page()
    if "rewards." not in (page.url or ""):
        if not await safe_goto(page, REWARDS_HOME, attempts=4, timeout=20_000):
            return None
        await sleep_jitter(2.0, 4.0)
    return page


async def fetch_dashboard(context: BrowserContext, *, navigate: bool = True) -> dict[str, Any]:
    """
    Devuelve el dict `dashboard` de /api/getuserinfo?type=1, o {} si falla.

    navigate=True asegura primero que hay una página en rewards.bing.com;
    daily.py ya navega por su cuenta y llama con navigate=False.
    """
    page: Page | None
    if navigate:
        page = await _ensure_on_rewards(context)
    else:
        page = context.pages[0] if context.pages else None
    if page is None:
        return {}
    try:
        payload = await page.evaluate(_API_JS)
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch getuserinfo falló: %s", exc)
        return {}
    if not isinstance(payload, dict):
        return {}
    dash = payload.get("dashboard")
    return dash if isinstance(dash, dict) else {}


# --- Nivel + puntos --------------------------------------------------------
def _user_status(dashboard: dict) -> dict:
    us = dashboard.get("userStatus")
    return us if isinstance(us, dict) else {}


def parse_level(dashboard: dict) -> tuple[int | None, str | None]:
    """
    (nivel_int, nombre) desde userStatus.levelInfo.

    El esquema nuevo usa "newLevel1/2/3" (Miembro/Plata/Oro); el viejo usaba
    "Level2". Extraemos el último dígito de activeLevel en ambos casos.
    """
    li = _user_status(dashboard).get("levelInfo")
    if not isinstance(li, dict):
        return None, None
    active = str(li.get("activeLevel", ""))
    name = li.get("activeLevelName") or None
    # último dígito de "newLevel3" / "Level2" / "3"
    digits = [c for c in active if c.isdigit()]
    level = int(digits[-1]) if digits else None
    return level, name


def parse_points(dashboard: dict) -> int | None:
    pts = _user_status(dashboard).get("availablePoints")
    return pts if isinstance(pts, int) else None


def parse_user_info(dashboard: dict) -> dict[str, Any]:
    """{'level', 'level_name', 'points', 'source'} desde el dict dashboard."""
    if not dashboard:
        return {"level": None, "level_name": None, "points": None, "source": None}
    level, name = parse_level(dashboard)
    return {
        "level": level,
        "level_name": name,
        "points": parse_points(dashboard),
        "source": "api" if (level or parse_points(dashboard) is not None) else None,
    }


# --- Enumeración de actividades pendientes --------------------------------
def _today_key(daily_promos: dict) -> str | None:
    """
    Clave "MM/DD/YYYY" del daily set de HOY dentro de dailySetPromotions.

    Prefiere la fecha de hoy exacta; si no está (desfase de zona horaria),
    la mayor fecha <= hoy. Nunca elige una fecha futura (el set de mañana
    aparece con complete=false pero aún no acredita).
    """
    if not isinstance(daily_promos, dict) or not daily_promos:
        return None
    today = date.today()
    today_key = f"{today.month:02d}/{today.day:02d}/{today.year}"
    if today_key in daily_promos:
        return today_key
    best: tuple[date, str] | None = None
    for k in daily_promos:
        try:
            m, d, y = (int(x) for x in k.split("/"))
            dt = date(y, m, d)
        except Exception:
            continue
        if dt <= today and (best is None or dt > best[0]):
            best = (dt, k)
    return best[1] if best else None


def _is_earnable(promo: Any) -> bool:
    """True si es una actividad urlreward pendiente con puntos y destino."""
    if not isinstance(promo, dict):
        return False
    if bool(promo.get("complete")):
        return False
    if str(promo.get("promotionType", "")).lower() != "urlreward":
        return False
    try:
        if int(promo.get("pointProgressMax") or 0) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return bool(promo.get("destinationUrl"))


def _to_activity(promo: dict, source: str) -> Activity:
    return Activity(
        offer_id=str(promo.get("offerId") or promo.get("name") or ""),
        title=str(promo.get("title") or promo.get("name") or "actividad"),
        destination_url=str(promo["destinationUrl"]),
        source=source,
        points_max=int(promo.get("pointProgressMax") or 0),
    )


def pending_activities(dashboard: dict, *, include_more: bool = True,
                       include_punch: bool = True, cap: int = 40) -> list[Activity]:
    """
    Lista de actividades pendientes que dan puntos, en orden de prioridad:
    daily set de hoy → more promotions → punch cards. Filtra completadas,
    banners (pointProgressMax=0) y todo lo que no sea urlreward.
    """
    acts: list[Activity] = []
    seen: set[str] = set()

    def _add(promo: dict, source: str) -> None:
        if _is_earnable(promo):
            a = _to_activity(promo, source)
            key = a.offer_id or a.destination_url
            if key not in seen:
                seen.add(key)
                acts.append(a)

    # 1) Daily set de hoy
    daily = dashboard.get("dailySetPromotions")
    if isinstance(daily, dict):
        key = _today_key(daily)
        if key:
            for p in daily.get(key, []) or []:
                _add(p, "daily")

    # 2) More promotions (muchas son banners sin puntos → filtradas)
    if include_more:
        for p in dashboard.get("morePromotions", []) or []:
            _add(p, "more")

    # 3) Punch cards: aplanar childPromotions pendientes
    if include_punch:
        for card in dashboard.get("punchCards", []) or []:
            if not isinstance(card, dict):
                continue
            for child in card.get("childPromotions", []) or []:
                _add(child, "punch")

    return acts[:cap]


# --- Self-test manual (solo lectura, no ejecuta actividades) ---------------
if __name__ == "__main__":
    import asyncio
    import launcher

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    async def _main() -> None:
        async with launcher.launch(visible=False) as context:
            dash = await fetch_dashboard(context, navigate=True)
            if not dash:
                print("SIN DATOS: la API no respondió (¿login?)")
                return
            ui = parse_user_info(dash)
            print(f"nivel={ui['level']} ({ui['level_name']})  puntos={ui['points']}")
            acts = pending_activities(dash)
            print(f"actividades pendientes: {len(acts)}")
            for a in acts:
                print(f"  [{a.source}] {a.title[:55]}  ({a.points_max} pts)")
                print(f"         -> {a.destination_url[:110]}")

    asyncio.run(_main())
