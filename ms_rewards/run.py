"""
Orquestador del bot de Microsoft Rewards.

Uso:
  py run.py                 # daily set + búsquedas (todo)
  py run.py --searches      # solo búsquedas
  py run.py --daily         # solo daily set
  py run.py --setup         # arranca Chrome para login manual y sale
  py run.py --force         # ignora el check de idempotencia diaria
  py run.py --no-update     # salta el auto-update por git pull
  py run.py --kill          # mata cualquier Chrome del bot colgado

Flujo automático (ejecución normal):
  1. updater.update_if_needed()  → git pull si VERSION remoto != local
  2. runstate.completed_today()  → si ya se completó hoy, sale
  3. launch() + verificar sesión activa; si caducó, intentar login auto
  4. daily set + búsquedas
  5. healer.heal(...) por cada selector roto (solo si MAINTAINER)
  6. runstate.mark_completed(...)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import random
import sys
from datetime import datetime

import config
import credentials
import daily as daily_mod
import login as login_mod
import rewards_info
import runstate
import updater
from humanize import sleep_jitter
from launcher import launch, shutdown_chrome
from searches import run_searches
from searches_mobile import run_mobile_searches
from daily import run_daily


class _SingleInstance:
    """
    Lock de instancia única por perfil (USER_ID), vía named mutex de Windows.

    Evita que dos corridas se ejecuten a la vez sobre el mismo perfil — el
    caso típico es que los dos triggers de la Scheduled Task (logon + diario)
    disparen casi simultáneamente y choquen en el user-data-dir de Chrome.
    El SO libera el mutex solo cuando el proceso muere, así que no quedan
    locks stale aunque la corrida se mate a la fuerza.

    Si pywin32 no está disponible, degrada a no-op (mejor seguir corriendo
    sin lock que abortar). El launcher ya limpia locks de Chrome como red de
    seguridad de segundo nivel.
    """

    def __init__(self) -> None:
        self._handle = None
        self._held = False

    def acquire(self) -> bool:
        try:
            import win32event  # type: ignore
            import winerror  # type: ignore
        except Exception:
            logging.getLogger("run").info(
                "pywin32 no disponible — sin lock de instancia única"
            )
            return True
        safe = "".join(c if c.isalnum() else "_" for c in config.USER_ID)
        name = f"MsRewardsBot_{safe}"
        self._handle = win32event.CreateMutex(None, True, name)
        if win32event.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            return False
        self._held = True
        return True

    def release(self) -> None:
        if self._handle is not None:
            try:
                import win32api  # type: ignore
                win32api.CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


def _setup_logging() -> None:
    # Forzar UTF-8 en stdout/stderr — Windows por defecto usa cp1252 y revienta
    # con cualquier carácter no-Latin1 (símbolos como →, tildes en títulos de
    # cards, etc.). reconfigure() existe desde Python 3.7.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass

    logfile = config.LOG_DIR / f"{datetime.now():%Y%m%d}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.FileHandler(logfile, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    logging.getLogger("patchright").setLevel(logging.WARNING)


async def _ensure_session(context) -> bool:
    """
    Verifica que la sesión esté activa. Si no, intenta login no-interactivo
    con las credenciales guardadas. Devuelve True si finalmente hay sesión.
    """
    log = logging.getLogger("run")
    if await login_mod.is_session_alive(context):
        return True
    log.warning("sesión caducada o no iniciada — intentando autologin")
    creds = credentials.load()
    if not creds:
        log.error("no hay credenciales guardadas (ejecuta setup.bat)")
        return False
    email, password = creds
    ok = await login_mod.perform_login(context, email, password, interactive=False)
    if not ok:
        log.error("autologin no-interactivo falló (probablemente 2FA)")
    return ok


async def _run(do_daily: bool, do_searches: bool) -> tuple[int, int, int, dict]:
    log = logging.getLogger("run")
    searches_done = 0
    mobile_searches_done = 0
    daily_done = 0
    info: dict = {"level": None, "points_before": None, "points_after": None}
    async with launch() as context:
        # Pequeña pausa inicial humanizada
        await sleep_jitter(3.0, 7.0)

        # Verificar sesión antes de hacer nada
        if not await _ensure_session(context):
            runstate.mark_needs_relogin()
            log.error("sesión no disponible — abortando. Re-ejecuta setup.bat")
            return 0, 0, 0, info

        # Detectar nivel + puntos iniciales
        try:
            ui = await rewards_info.get_user_info(context)
            info["level"] = ui.get("level")
            info["points_before"] = ui.get("points")
            log.info(
                "estado cuenta: nivel=%s puntos=%s fuente=%s",
                ui.get("level"), ui.get("points"), ui.get("source"),
            )
        except Exception as exc:
            log.warning("no se pudo leer info de cuenta: %s", exc)

        if do_daily:
            log.info("=== DAILY SET ===")
            try:
                daily_done = await run_daily(context)
                log.info("daily set: %d cards", daily_done)
            except Exception as exc:
                log.exception("daily set falló: %s", exc)

            if do_searches:
                gap = random.uniform(30.0, 90.0)
                log.info("pausa entre daily y búsquedas: %.1fs", gap)
                await asyncio.sleep(gap)

        if do_searches:
            log.info("=== BÚSQUEDAS BING ===")
            # Ajustar cantidad según nivel detectado (si MSR_SEARCH_COUNT no
            # está fijado explícitamente por el usuario).
            count = config.SEARCH_COUNT
            if "MSR_SEARCH_COUNT" not in os.environ and info["level"] in (1, 2):
                count = config.SEARCH_COUNT_BY_LEVEL[info["level"]]
                log.info(
                    "nivel %d → objetivo de búsquedas: %d", info["level"], count
                )
            try:
                searches_done = await run_searches(context, count=count)
                log.info("búsquedas: %d", searches_done)
            except Exception as exc:
                log.exception("búsquedas fallaron: %s", exc)

            # --- Búsquedas móviles ---
            # Bing acredita aparte las búsquedas móviles (Sec-CH-UA-Mobile=?1
            # + UA móvil + viewport pequeño + touch). Las hacemos desde el
            # mismo Chrome emulando un Pixel 8 vía CDP, manteniendo la sesión.
            if config.MOBILE_SEARCHES_ENABLED:
                # Gap humano 5-15 min entre desktop y móvil para que la
                # correlación misma-IP+misma-cuenta+UAs distintos parezca un
                # usuario que cambió de dispositivo.
                gap = random.uniform(300.0, 900.0)
                log.info("gap antes de búsquedas móviles: %.0fs (~%.1f min)", gap, gap / 60)
                await asyncio.sleep(gap)

                m_count = config.MOBILE_SEARCH_COUNT
                if "MSR_MOBILE_SEARCH_COUNT" not in os.environ and info["level"] in (1, 2):
                    m_count = config.MOBILE_SEARCH_COUNT_BY_LEVEL[info["level"]]
                    log.info(
                        "nivel %d → objetivo de búsquedas móviles: %d",
                        info["level"], m_count,
                    )
                log.info("=== BÚSQUEDAS MÓVIL ===")
                try:
                    mobile_searches_done = await run_mobile_searches(context, count=m_count)
                    log.info("búsquedas móviles: %d", mobile_searches_done)
                except Exception as exc:
                    log.exception("búsquedas móviles fallaron: %s", exc)

        # Puntos al final (para diff)
        try:
            ui2 = await rewards_info.get_user_info(context)
            info["points_after"] = ui2.get("points")
            if info["points_before"] is not None and info["points_after"] is not None:
                gain = info["points_after"] - info["points_before"]
                log.info(
                    "puntos: %s → %s (ganados hoy en esta sesión: %+d)",
                    info["points_before"], info["points_after"], gain,
                )
        except Exception:
            pass

        await sleep_jitter(4.0, 9.0)

    return searches_done, mobile_searches_done, daily_done, info


async def _post_run_heal() -> list[str]:
    """
    Si hay selectores rotos y estamos en modo maintainer, dispara healer.heal
    por cada uno. Devuelve la lista de claves que se intentaron reparar.
    """
    log = logging.getLogger("run")
    broken = list(daily_mod.broken_selectors)
    if not broken:
        return []
    if not config.MAINTAINER:
        log.warning(
            "selectores rotos detectados: %s — pero esta máquina no es "
            "maintainer; espera la publicación del fix",
            [b["key"] for b in broken],
        )
        return [b["key"] for b in broken]

    import healer  # import diferido — sólo el maintainer carga httpx/Ollama
    fixed = []
    for entry in broken:
        try:
            result = await healer.heal(entry["key"], entry["html"], entry["url"])
            if result:
                fixed.append(entry["key"])
        except Exception as exc:
            log.exception("healer falló para %s: %s", entry["key"], exc)
    return fixed


def _setup_mode() -> None:
    """Abre Chrome para que el usuario haga login a mano y luego sale."""
    from launcher import _spawn_chrome  # noqa: PLC0415
    log = logging.getLogger("run")
    proc = _spawn_chrome()
    log.info(
        "Chrome lanzado en :%d con perfil %s.\n"
        "→ Inicia sesión en https://rewards.bing.com\n"
        "→ Cuando termines, cierra esta consola (Chrome puede seguir abierto)\n",
        config.CDP_PORT,
        config.USER_DATA_DIR,
    )
    if proc:
        try:
            proc.wait()
        except KeyboardInterrupt:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--daily", action="store_true", help="solo daily set")
    parser.add_argument("--searches", action="store_true", help="solo búsquedas")
    parser.add_argument("--setup", action="store_true",
                        help="abre Chrome para login manual y espera")
    parser.add_argument("--kill", action="store_true",
                        help="mata cualquier Chrome del bot que quede colgado")
    parser.add_argument("--force", action="store_true",
                        help="ignora el check de idempotencia diaria")
    parser.add_argument("--no-update", action="store_true",
                        help="salta el auto-update (git pull)")
    args = parser.parse_args()

    _setup_logging()
    log = logging.getLogger("run")
    log.info("user=%s perfil=%s", config.USER_ID, config.USER_DATA_DIR)

    if args.kill:
        shutdown_chrome()
        return 0

    if args.setup:
        _setup_mode()
        return 0

    # Lock de instancia única: si ya hay otra corrida sobre este perfil
    # (típicamente los dos triggers de la Scheduled Task solapándose), salimos
    # limpiamente en vez de pelearnos por el perfil de Chrome.
    instance = _SingleInstance()
    if not instance.acquire():
        log.info("ya hay otra corrida en marcha para este perfil — saliendo")
        return 0

    try:
        return _main_locked(args, log)
    finally:
        instance.release()


def _main_locked(args, log) -> int:
    # 1) Auto-update (puede relanzar el proceso)
    if not args.no_update:
        try:
            updated = updater.update_if_needed()
            if updated:
                updater.relaunch()  # no retorna
        except Exception as exc:
            log.warning("auto-update falló: %s (continuando con la versión local)", exc)

    # 2) Idempotencia diaria
    if not args.force and runstate.completed_today():
        log.info("ya completado hoy (%s) — saliendo. Usa --force para forzar.",
                 runstate.read().get("updated_at"))
        return 0

    do_daily = args.daily or (not args.daily and not args.searches)
    do_searches = args.searches or (not args.daily and not args.searches)

    try:
        searches_done, mobile_searches_done, daily_done, info = asyncio.run(
            _run(do_daily=do_daily, do_searches=do_searches)
        )
    except KeyboardInterrupt:
        log.info("interrumpido por usuario")
        return 130
    except Exception as exc:
        log.exception("ejecución abortada: %s", exc)
        runstate.write(status="error", error=str(exc)[:200])
        return 1

    # 3) Heal post-ejecución (si maintainer y hay selectores rotos)
    try:
        fixed = asyncio.run(_post_run_heal())
        if fixed:
            log.info("healer reparó: %s", fixed)
    except Exception as exc:
        log.exception("post-run heal falló: %s", exc)

    # 4) Estado final (incluye nivel y puntos para visibilidad del usuario)
    if daily_mod.broken_selectors:
        runstate.mark_selectors_broken([b["key"] for b in daily_mod.broken_selectors])
    runstate.write(
        level=info.get("level"),
        points_before=info.get("points_before"),
        points_after=info.get("points_after"),
        mobile_searches_done=mobile_searches_done,
    )
    if searches_done == 0 and mobile_searches_done == 0 and daily_done == 0:
        runstate.write(status="empty", searches_done=0, daily_done=0)
    else:
        runstate.mark_completed(searches_done, daily_done, status="ok")

    return 0


if __name__ == "__main__":
    sys.exit(main())
