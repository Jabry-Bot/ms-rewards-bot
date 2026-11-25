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

RESPALDO (2026-11): con algunas cuentas /api/getuserinfo ya no responde, aunque
la web va bien. La página /earn (Next.js) trae los mismos datos incrustados en
su payload RSC (`self.__next_f.push(...)`): saldo y nivel, el contador de
búsquedas de PC (pointbreakdown), el conjunto diario y las demás actividades.
Si la API falla, `fetch_dashboard` monta con eso un dict con la MISMA forma que
el de la API (ver `dashboard_from_rsc`), así el resto del bot no cambia.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from patchright.async_api import BrowserContext, Page

from humanize import safe_goto, sleep_jitter

log = logging.getLogger("dashboard_api")

# Root de rewards; redirige a /dashboard. La API es relativa al origen, así que
# basta con estar en cualquier página de rewards.bing.com.
REWARDS_HOME = "https://rewards.bing.com/"

EARN_URL = "https://rewards.bing.com/earn"

# Devuelve {status, data} para poder decir en el log POR QUÉ falla la API.
_API_JS = """
async () => {
  try {
    const r = await fetch('/api/getuserinfo?type=1', {credentials: 'include'});
    let data = null;
    if (r.ok) { try { data = await r.json(); } catch (e) { data = null; } }
    return {status: r.status, data: data};
  } catch (e) { return {status: 0, data: null, error: String(e).slice(0, 120)}; }
}
"""

# Se avisa una sola vez por proceso de que la API no va y se usa la página.
_fallback_noted = False


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
    why = "sin respuesta"
    try:
        res = await page.evaluate(_API_JS)
    except Exception as exc:  # noqa: BLE001
        res, why = None, f"error: {str(exc)[:80]}"
    if isinstance(res, dict):
        data = res.get("data")
        dash = data.get("dashboard") if isinstance(data, dict) else None
        if isinstance(dash, dict) and dash:
            return dash
        why = (f"HTTP {res.get('status')}" if res.get("status")
               else f"error: {res.get('error') or '?'}")
        if isinstance(data, dict):
            why += " sin «dashboard»"
        elif res.get("status") == 200:
            why += " sin datos"
    # La API no da datos: leerlos de la propia página /earn.
    dash = await fetch_dashboard_from_page(context)
    global _fallback_noted
    if not _fallback_noted:
        _fallback_noted = True
        if dash:
            log.info("la API de Rewards no responde (%s): datos leídos de la página "
                     "/earn", why)
        else:
            log.warning("la API de Rewards no responde (%s) y la página /earn "
                        "tampoco trae datos", why)
    return dash


async def fetch_dashboard_from_page(context: BrowserContext) -> dict[str, Any]:
    """
    Respaldo de la API: descarga /earn con la sesión del navegador y saca los
    datos del payload RSC (ver dashboard_from_rsc). Si la descarga directa no
    trae datos, abre /earn en una pestaña aparte (la principal no se toca: el
    conjunto diario se clica en ella) y lee su HTML. {} si no hay datos.
    """
    try:
        resp = await context.request.get(EARN_URL, timeout=30_000)
        if resp.ok:
            dash = dashboard_from_rsc(await resp.text())
            if dash:
                return dash
    except Exception as exc:  # noqa: BLE001
        log.debug("descarga de /earn falló: %s", exc)
    tab = None
    try:
        tab = await context.new_page()
        if await safe_goto(tab, EARN_URL, attempts=3, timeout=25_000):
            await sleep_jitter(3.0, 5.0)
            return dashboard_from_rsc(await tab.content())
    except Exception as exc:  # noqa: BLE001
        log.debug("lectura de /earn en pestaña falló: %s", exc)
    finally:
        if tab is not None:
            try:
                await tab.close()
            except Exception:  # noqa: BLE001
                pass
    return {}


# --- Payload RSC de Next.js ------------------------------------------------
_RSC_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_RSC_ROW = re.compile(r"^([0-9a-zA-Z]+):(.*)$")
_DAILY_ID = re.compile(r"_DailySet_(\d{4})(\d{2})(\d{2})_", re.I)


def rsc_objects(html: str) -> list[dict]:
    """Todos los objetos (dicts) del payload RSC de una página Next.js."""
    try:
        text = "".join(json.loads(c) for c in _RSC_PUSH.findall(html or ""))
    except ValueError:
        return []
    out: list[dict] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    for line in text.split("\n"):
        m = _RSC_ROW.match(line)
        if not m or m.group(2)[:1] not in "[{":
            continue
        try:
            walk(json.loads(m.group(2)))
        except ValueError:
            continue
    return out


def _int(v: Any) -> int | None:
    return v if type(v) is int else None


def dashboard_from_rsc(html: str) -> dict[str, Any]:
    """
    Monta, con los datos incrustados en /earn, un dict con la forma del de
    /api/getuserinfo (función pura):
      userStatus.availablePoints / levelInfo.activeLevel   <- {"balance", "level"}
      userStatus.counters.pcSearch                          <- pointbreakdown.pc
      dailySetPromotions["MM/DD/YYYY"]                      <- ofertas *_DailySet_*
      morePromotions                                        <- el resto con destino
    Las actividades bloqueadas van con pointProgressMax 0 (no se intentan).
    {} si la página no trae ni saldo ni actividades (p. ej. sin sesión).
    """
    objs = rsc_objects(html)
    balance = level = None
    pc = None
    offers: dict[str, dict] = {}
    for o in objs:
        if balance is None and _int(o.get("balance")) is not None and "level" in o:
            balance, level = o["balance"], _int(o.get("level"))
        if pc is None and o.get("type") == "pointbreakdown":
            model = o.get("model") if isinstance(o.get("model"), dict) else {}
            counters = model.get("pointsCounters")
            if isinstance(counters, dict) and isinstance(counters.get("pc"), dict):
                pc = counters["pc"]
        oid = o.get("offerId")
        dest = o.get("destination") or o.get("destinationUrl")
        if not isinstance(oid, str) or not oid or not isinstance(dest, str) \
                or not dest.startswith("http") or _int(o.get("points")) is None:
            continue
        prev = offers.get(oid)
        if prev is None or (not prev.get("title") and o.get("title")):
            offers[oid] = o
    if balance is None and not offers:
        return {}

    status: dict[str, Any] = {}
    if balance is not None:
        status["availablePoints"] = balance
    if level is not None:
        status["levelInfo"] = {"activeLevel": f"newLevel{level}", "activeLevelName": None}
    if pc is not None and _int(pc.get("max")):
        status["counters"] = {"pcSearch": [{
            "pointProgress": _int(pc.get("progress")) or 0,
            "pointProgressMax": pc["max"], "attributes": {}}]}

    daily: dict[str, list[dict]] = {}
    more: list[dict] = []
    for oid, o in offers.items():
        locked = o.get("isLocked") is True
        promo = {"offerId": oid, "name": oid,
                 "title": str(o.get("title") or oid),
                 "destinationUrl": o.get("destination") or o.get("destinationUrl"),
                 "complete": o.get("isCompleted") is True,
                 "promotionType": "urlreward",
                 "pointProgressMax": 0 if locked else o["points"],
                 "hash": o.get("hash")}
        m = _DAILY_ID.search(oid)
        if m:
            key = o.get("date") if isinstance(o.get("date"), str) else \
                f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
            daily.setdefault(key, []).append(promo)
        else:
            more.append(promo)
    return {"userStatus": status, "dailySetPromotions": daily,
            "morePromotions": more, "punchCards": [], "source": "page"}


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


def parse_pc_search(dashboard: dict) -> tuple[int, int, int] | None:
    """
    (progreso, máximo, puntos_por_búsqueda) del contador de búsquedas de PC de
    hoy (userStatus.counters.pcSearch), o None si no viene. Es la fuente de
    verdad de cuántas búsquedas faltan: el tope cambia con el nivel y con el
    tiempo (2026-11: nivel Oro = 120 pts a 3 por búsqueda = 40 búsquedas).
    """
    counters = _user_status(dashboard).get("counters")
    items = counters.get("pcSearch") if isinstance(counters, dict) else None
    if not isinstance(items, list) or not items:
        return None
    prog = mx = 0
    pps = 3
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            prog += int(it.get("pointProgress") or 0)
            mx += int(it.get("pointProgressMax") or 0)
        except (TypeError, ValueError):
            continue
        desc = str((it.get("attributes") or {}).get("description", ""))
        m = re.search(r"(\d+)\s*puntos?\s+por\s+b|(\d+)\s*points?\s+per\s+search", desc, re.I)
        if m:
            pps = int(m.group(1) or m.group(2))
    return (prog, mx, pps) if mx > 0 else None


def daily_set_status(dashboard: dict) -> tuple[int, int] | None:
    """(completadas, total) del conjunto diario de hoy, o None si no viene."""
    daily = dashboard.get("dailySetPromotions")
    key = _today_key(daily) if isinstance(daily, dict) else None
    if not key:
        return None
    items = [p for p in daily.get(key, []) or [] if isinstance(p, dict)]
    return sum(1 for p in items if p.get("complete")), len(items)


def parse_last_order(dashboard: dict) -> dict[str, Any] | None:
    """
    Último canje de la cuenta (userStatus.lastOrder): {'id', 'price', 'title',
    'at'} con `at` en datetime con zona, o None si no hay o no se entiende.
    `price` es lo que se pagó de verdad (con descuentos), no el de catálogo.
    """
    order = _user_status(dashboard).get("lastOrder")
    if not isinstance(order, dict):
        return None
    price = order.get("price")
    if type(price) is not int or price <= 0:
        return None
    raw = str(order.get("timestamp") or "")
    # .NET manda 7 decimales de segundo; fromisoformat solo quiere hasta 6.
    raw = re.sub(r"(\.\d{6})\d+", r"\1", raw).replace("Z", "+00:00")
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    item = order.get("catalogItem") if isinstance(order.get("catalogItem"), dict) else {}
    title = str(item.get("title") or order.get("sku") or "").strip()
    return {"id": str(order.get("id") or ""), "price": price, "title": title, "at": at}


def parse_user_info(dashboard: dict) -> dict[str, Any]:
    """{'level', 'level_name', 'points', 'source', 'pc_search', 'last_order'}
    desde el dict dashboard."""
    if not dashboard:
        return {"level": None, "level_name": None, "points": None, "source": None,
                "pc_search": None, "last_order": None}
    level, name = parse_level(dashboard)
    return {
        "level": level,
        "level_name": name,
        "points": parse_points(dashboard),
        "source": (dashboard.get("source") or "api")
                  if (level or parse_points(dashboard) is not None) else None,
        "pc_search": parse_pc_search(dashboard),
        "last_order": parse_last_order(dashboard),
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
