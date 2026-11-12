"""
Verificación final de la corrida: antes de cerrar el navegador comprueba en
Microsoft Rewards que todo lo que hace el bot se ha acreditado DE VERDAD y
repite lo que haya quedado a medias. Lo de móvil (la racha de la app de Bing)
se excluye: el bot no lo hace.

Por qué: "hecho" en el log no significa "acreditado". Ejemplos reales:
  - 2026-11-12: la card de Dubái del conjunto diario se clicó bien pero quedó
    a 0/10 (repitiendo el clic se acreditó al instante).
  - Las búsquedas de PC se calculaban con una tabla por nivel (34 en Oro)
    mientras el tope real era 120 pts = 40 búsquedas: quedaba en 108/120.

Fuentes de verdad:
  - API /api/getuserinfo: contador de búsquedas de PC (counters.pcSearch) y
    actividades pendientes (conjunto diario, Ganar y tarjetas de sellos).
  - Página /earn, sección "Rachas": búsqueda por imagen ("Actividad: 1/1"),
    minutos de Edge ("Minutos: 30/30"), racha de búsqueda y de conjunto diario.
"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from typing import Any

from patchright.async_api import BrowserContext

import config
import daily
import dashboard_api
from humanize import safe_goto, sleep_jitter

log = logging.getLogger("verify")

EARN_URL = "https://rewards.bing.com/earn"

# Tope de búsquedas extra por ronda y rondas de recuperación.
MAX_EXTRA_SEARCHES = 20
SEARCH_ROUNDS = 2

# --- Rachas de la página /earn --------------------------------------------
# Título de cada racha → clave. Español (es-ES, la región probada) + inglés.
_STREAK_KEYS = [
    (re.compile(r"b[uú]squeda visual|visual search", re.I), "visual"),
    (re.compile(r"\bedge\b", re.I), "edge"),
    (re.compile(r"conjunto diario|daily set", re.I), "daily_set"),
    (re.compile(r"aplicaci[oó]n|\bapp\b|m[oó]vil|mobile", re.I), "app"),
    (re.compile(r"b[uú]squeda|search", re.I), "search"),
]
_STREAK_TITLE = re.compile(r"^(racha de|.*\bstreak\b)", re.I)
_METRIC = re.compile(r"^[^\d:]{3,30}:\s*(\d+)\s*/\s*(\d+)\s*$")


def parse_streaks(text: str) -> dict[str, tuple[int, int]]:
    """
    Extrae {clave: (progreso, máximo)} de las rachas del texto de /earn.

    Cada racha es un bloque "Racha de <X>" … "<Métrica>: a/b"; la métrica se
    asigna al último título de racha visto. Claves: search, daily_set, edge,
    app (móvil), visual.
    """
    out: dict[str, tuple[int, int]] = {}
    current: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _STREAK_TITLE.match(line):
            current = next((k for rx, k in _STREAK_KEYS if rx.search(line)), None)
            continue
        m = _METRIC.match(line)
        if m and current and current not in out:
            out[current] = (int(m.group(1)), int(m.group(2)))
    return out


async def read_streaks(context: BrowserContext) -> dict[str, tuple[int, int]]:
    """Carga /earn y lee las rachas (espera a que la SPA las pinte)."""
    page = context.pages[0] if context.pages else await context.new_page()
    if not await safe_goto(page, EARN_URL, attempts=3, timeout=25_000):
        return {}
    streaks: dict[str, tuple[int, int]] = {}
    for _ in range(10):
        await asyncio.sleep(1.5)
        try:
            streaks = parse_streaks(await page.evaluate("() => document.body.innerText"))
        except Exception:
            streaks = {}
        if "visual" in streaks or "edge" in streaks:
            break
    return streaks


def _ok(pair: tuple[int, int] | None) -> bool:
    return pair is not None and pair[0] >= pair[1]


def _fmt(pair: tuple[int, int] | None) -> str:
    if pair is None:
        return "? (no se pudo leer)"
    return f"{pair[0]}/{pair[1]} {'✓' if _ok(pair) else '✗'}"


def searches_needed(pc: tuple[int, int, int] | None, margin: int = 1) -> int | None:
    """Búsquedas que faltan según el contador (None si no se conoce)."""
    if pc is None:
        return None
    prog, mx, pps = pc
    missing = mx - prog
    if missing <= 0:
        return 0
    return math.ceil(missing / max(1, pps)) + margin


# --- Verificación + recuperación ------------------------------------------
async def verify_and_fix(context: BrowserContext, *, do_daily: bool,
                         do_searches: bool, searches_used: int) -> dict[str, Any]:
    """
    Comprueba cada actividad, repite las que falten y deja un resumen en el log.
    Devuelve un dict serializable para last_run.json.
    """
    from searches import run_searches          # noqa: PLC0415 (evita ciclos)
    from visual_search import run_visual_search  # noqa: PLC0415

    log.info("=== VERIFICACIÓN FINAL ===")
    summary: dict[str, Any] = {"extra_searches": 0}

    # 1) Búsquedas de PC: contador de la API; si falta, más búsquedas NUEVAS.
    pc = None
    if do_searches:
        for rnd in range(1, SEARCH_ROUNDS + 1):
            await sleep_jitter(3.0, 5.0)  # el contador tarda un poco en subir
            dash = await dashboard_api.fetch_dashboard(context, navigate=True)
            pc = dashboard_api.parse_pc_search(dash)
            need = searches_needed(pc)
            if not need:
                break
            need = min(need, MAX_EXTRA_SEARCHES)
            log.info("búsquedas de PC en %d/%d — faltan; %d búsqueda(s) más (ronda %d/%d)",
                     pc[0], pc[1], need, rnd, SEARCH_ROUNDS)
            done = await run_searches(context, count=need, offset=searches_used)
            searches_used += need
            summary["extra_searches"] += done
        else:
            await sleep_jitter(3.0, 5.0)
            dash = await dashboard_api.fetch_dashboard(context, navigate=True)
            pc = dashboard_api.parse_pc_search(dash)
        summary["pc_search"] = list(pc[:2]) if pc else None

    # 2) Conjunto diario y "Ganar": una pasada más por si algo no se acreditó.
    remaining = None
    if do_daily:
        remaining = await daily.retry_unconfirmed(context, rounds=1, include_more=False)

    # 3) Rachas de /earn: búsqueda por imagen y Edge. Si la búsqueda por imagen
    #    no consta, se repite una vez.
    streaks = await read_streaks(context)
    if do_searches and "visual" in streaks and not _ok(streaks["visual"]):
        log.info("búsqueda por imagen sin acreditar (%s) — repitiéndola",
                 _fmt(streaks["visual"]))
        try:
            await run_visual_search(context)
        except Exception as exc:
            log.warning("reintento de búsqueda por imagen falló: %s", exc)
        streaks = await read_streaks(context) or streaks

    # 4) Estado final según la API.
    dash = await dashboard_api.fetch_dashboard(context, navigate=True)
    ds = dashboard_api.daily_set_status(dash) if dash else None
    if remaining is None and dash:
        remaining = dashboard_api.pending_activities(dash)
    remaining = remaining or []
    pend_daily = [a.title for a in remaining if a.source == "daily"]
    pend_more = [a.title for a in remaining if a.source == "more"]
    pend_punch = [a.title for a in remaining if a.source == "punch"]

    # --- Resumen -----------------------------------------------------------
    lines: list[str] = []
    if do_searches:
        lines.append(f"búsquedas de PC: {_fmt(tuple(pc[:2]) if pc else None)}")
    if do_daily:
        lines.append(f"conjunto diario: {_fmt(ds)}"
                     + (f" — sin acreditar: {', '.join(t[:40] for t in pend_daily)}"
                        if pend_daily else ""))
        lines.append("Ganar (más actividades): "
                     + ("todo ✓" if not pend_more else
                        f"{len(pend_more)} sin acreditar: {', '.join(t[:40] for t in pend_more)}"))
        if pend_punch:
            lines.append(f"tarjetas de sellos: {len(pend_punch)} pendientes "
                         "(el bot no puede acreditarlas visitándolas)")
    if do_searches:
        lines.append(f"búsqueda por imagen: {_fmt(streaks.get('visual'))}")
    if config.BROWSER == "edge":
        lines.append(f"racha de Edge (minutos): {_fmt(streaks.get('edge'))}")
    else:
        lines.append("racha de Edge: omitida (navegador Chrome; requiere Edge)")
    if "search" in streaks:
        lines.append(f"racha de búsqueda: {_fmt(streaks.get('search'))}")
    for line in lines:
        log.info("  %s", line)

    # Lo que merece otra corrida (run.py no da el día por terminado y la tarea
    # lo reintenta en ~30 min). "Ganar", sellos y Edge NO: alguna no se acredita
    # nunca por esta vía y provocaría corridas en bucle todo el día.
    retry: list[str] = []
    if do_searches and pc and pc[0] < pc[1]:
        retry.append("búsquedas de PC")
    if do_daily and (pend_daily or (ds and ds[0] < ds[1])):
        retry.append("conjunto diario")
    if do_searches and "visual" in streaks and not _ok(streaks["visual"]):
        retry.append("búsqueda por imagen")
    if retry:
        log.warning("VERIFICACIÓN: sin completar: %s", ", ".join(retry))
    elif any("✗" in ln or "sin acreditar" in ln for ln in lines):
        log.info("VERIFICACIÓN: lo principal está acreditado ✓ (lo que queda "
                 "pendiente no se puede completar desde el bot)")
    else:
        log.info("VERIFICACIÓN: todo acreditado ✓")

    summary.update({
        "retry": retry,
        "daily_set": list(ds) if ds else None,
        "pending_daily": pend_daily,
        "pending_more": pend_more,
        "pending_punch": len(pend_punch),
        "visual": list(streaks["visual"]) if "visual" in streaks else None,
        "edge": list(streaks["edge"]) if "edge" in streaks else None,
        "ok": not retry,
    })
    return summary
