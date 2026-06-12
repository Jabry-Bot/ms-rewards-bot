"""
Lanza Chrome real bajo control de patchright usando launch_persistent_context.

Historia del bug de DNS (importante para no repetirlo):
  Todas las navegaciones fallaban con net::ERR_NAME_NOT_RESOLVED aunque el
  DNS del sistema funcionaba. Durante un tiempo se culpó al attach-over-CDP
  (connect_over_cdp) y al firewall/AV. La causa real, confirmada con un test
  A/B reproducible, era la llamada a `context.add_init_script(...)`:
  en patchright (fork stealth de Playwright) inyectar un script de init vía
  CDP rompe la resolución DNS del Chrome lanzado. Quitando esa inyección,
  launch_persistent_context resuelve DNS sin problemas.

  Además era innecesaria: patchright ya aplica el stealth de forma nativa
  (navigator.webdriver=false, window.chrome presente, plugins, etc.) sin
  necesidad de inyectar nada. La regla con patchright es NO usar
  add_init_script / expose_function / route salvo que sea imprescindible.

launch_persistent_context lanza el Chrome real del sistema (channel="chrome")
sobre el perfil persistente, así que la sesión guardada con --setup se mantiene.

El modo --setup sigue lanzando Chrome standalone con subprocess (sin
Playwright) para que puedas loguearte a mano.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from patchright.async_api import BrowserContext, async_playwright

import config

log = logging.getLogger("launcher")


def _port_in_use(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.settimeout(0.5)
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _wait_devtools(port: int, timeout: float = 25.0) -> dict:
    """Espera a que DevTools esté listo y devuelve /json/version."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=2
            ) as r:
                payload = json.loads(r.read().decode("utf-8"))
                if payload.get("webSocketDebuggerUrl"):
                    return payload
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"DevTools no respondió en {port}: {last_err}")


def _spawn_chrome() -> subprocess.Popen | None:
    """Lanza Chrome con CDP. Devuelve None si ya estaba corriendo en el puerto."""
    if _port_in_use(config.CDP_PORT):
        log.info("Chrome ya está escuchando en :%d — reutilizando", config.CDP_PORT)
        return None

    user_data = Path(config.USER_DATA_DIR)
    user_data.mkdir(parents=True, exist_ok=True)

    # Nota: NO usamos --disable-blink-features=AutomationControlled.
    # Chrome moderno muestra un banner amarillo ("No se admite el indicador
    # de línea de comandos...") cuando esa flag está presente — y eso
    # delata al bot. Como conectamos vía CDP attach a un Chrome real (no
    # via Playwright launch), navigator.webdriver ya queda en undefined
    # sin necesidad de la flag.
    args = [
        config.CHROME_PATH,
        f"--remote-debugging-port={config.CDP_PORT}",
        f"--user-data-dir={user_data}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        # Combo de features deshabilitadas:
        # - DefaultBrowserSettingEnforcement: quita el banner "Chrome no es predeterminado"
        # - DnsOverHttps / DnsOverHttpsUpgrade: fuerza al resolver del SO.
        #   En un perfil --user-data-dir nuevo, Chrome arranca con DoH on por
        #   defecto y, si el resolver DoH no responde (router doméstico, red
        #   corporativa, etc.), se queda sin resolver NADA — incluido bing.com.
        "--disable-features=DefaultBrowserSettingEnforcement,DnsOverHttps,DnsOverHttpsUpgrade",
        # Abrimos directamente el dashboard de rewards.
        # En España rewards.bing.com da NXDOMAIN; rewards.microsoft.com es
        # el dominio canónico actual (Microsoft migró todo allí).
        "https://rewards.microsoft.com/",
    ]
    log.info("Lanzando Chrome: %s (port=%d, profile=%s)",
             config.CHROME_PATH, config.CDP_PORT, user_data)
    proc = subprocess.Popen(
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    return proc


@asynccontextmanager
async def launch(visible: bool = True) -> AsyncIterator[BrowserContext]:
    """
    Yields un BrowserContext sobre el perfil persistente del bot.

    Usa pw.chromium.launch_persistent_context con channel="chrome" para
    lanzar el Chrome real instalado en el sistema (no el Chromium de
    Playwright). La sesión guardada con --setup se carga automáticamente
    porque apuntamos al mismo user_data_dir.

    visible:
        True  → ventana visible en pantalla (setup / switch_account / ejecutar
                manual): se puede ver e interactuar con el CDP.
        False → ventana fuera de pantalla (corrida automática programada): no
                molesta, pero la página sigue "visible" para Bing y acredita.

    IMPORTANTE: Chrome no permite dos instancias sobre el mismo
    user_data_dir. Asegúrate de no tener un Chrome del bot abierto antes
    de entrar aquí — usa `py run.py --kill` si dudas.
    """
    user_data = Path(config.USER_DATA_DIR)
    user_data.mkdir(parents=True, exist_ok=True)

    launch_args = [
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        # DefaultBrowserSettingEnforcement: quita el banner "Chrome no es predeterminado".
        # DnsOverHttps / DnsOverHttpsUpgrade: fuerza resolver del SO.
        "--disable-features=DefaultBrowserSettingEnforcement,DnsOverHttps,DnsOverHttpsUpgrade",
        # --disable-blink-features=AutomationControlled: hace que
        # navigator.webdriver sea false. Imprescindible cuando usamos el
        # Chrome del sistema (channel="chrome") — el stealth nativo de
        # patchright se aplica solo al Chromium bundled, no al Chrome real.
        # No dispara la barra amarilla de Chrome (esa lista solo incluye
        # flags como --no-sandbox o --enable-automation).
        "--disable-blink-features=AutomationControlled",
    ]

    # Posición de la ventana: visible (0,0) si es interactiva, fuera de
    # pantalla si es la corrida automática programada.
    window_position = config.WINDOW_POSITION if visible else config.WINDOW_POSITION_HIDDEN
    log.info("ventana: %s", "visible" if visible else "oculta (fuera de pantalla)")
    if window_position:
        launch_args.append(f"--window-position={window_position}")
    if config.WINDOW_SIZE:
        launch_args.append(f"--window-size={config.WINDOW_SIZE}")

    # Antes de lanzar, asegúrate de que NADIE más tiene el perfil tomado:
    # un chrome.exe del bot colgado de una corrida previa (o dos triggers de
    # la Scheduled Task solapados — logon + diario) bloquea el user-data-dir
    # y hace que launch_persistent_context arranque Chrome y se cierre al
    # instante ("Target page, context or browser has been closed").
    shutdown_chrome()
    _clear_profile_locks()

    async def _open(pw):
        return await pw.chromium.launch_persistent_context(
            user_data_dir=str(user_data),
            channel=config.CHANNEL,
            headless=False,
            args=launch_args,
            no_viewport=True,  # respeta el tamaño real de ventana
            # Strip de flags que añade Playwright por defecto y que delatan
            # automatización:
            #   --enable-automation: pone navigator.webdriver=true y muestra
            #     el banner "Chrome está siendo controlado por software".
            #   --no-sandbox: muestra el banner amarillo "Estás usando una
            #     marca de línea de comandos no compatible". Lo ven los
            #     scripts de detección de Bing/MS Rewards trivialmente.
            #   --disable-blink-features=AutomationControlled: aunque suena
            #     anti-detección, su sola presencia es detectable (las flags
            #     activas se leen vía CDP / chrome://version). Patchright ya
            #     hace el stealth equivalente de forma nativa.
            #   --no-startup-window: causa una ventana fantasma sin foco.
            # Strip de flags que Playwright añade por defecto y delatan
            # automatización a Bing / scripts de detección:
            #   --enable-automation: pone navigator.webdriver=true y muestra
            #     el aviso "Chrome está siendo controlado por software".
            #   --no-sandbox: muestra la barra amarilla "Estás usando una
            #     marca de línea de comandos no compatible".
            # OJO: --disable-blink-features=AutomationControlled NO va aquí
            # porque la pasamos manualmente en launch_args (es la que
            # realmente esconde navigator.webdriver con channel="chrome").
            ignore_default_args=[
                "--enable-automation",
                "--no-sandbox",
            ],
        )

    async with async_playwright() as pw:
        log.info("lanzando navegador (channel=%s, profile=%s)", config.CHANNEL, user_data)
        try:
            context = await _open(pw)
        except Exception as exc:
            # Reintento: a veces el lock tarda en liberarse tras matar el
            # Chrome viejo. Limpiamos de nuevo, esperamos y volvemos a probar
            # una vez antes de rendirnos.
            log.warning("launch falló (%s) — limpiando perfil y reintentando", exc)
            shutdown_chrome()
            _clear_profile_locks()
            await asyncio.sleep(2.0)
            context = await _open(pw)
        log.info("contexto persistente listo (%d página/s)", len(context.pages))

        # OJO: NO usar context.add_init_script(...) aquí. En patchright rompe
        # la resolución DNS (ERR_NAME_NOT_RESOLVED) y además es innecesario:
        # el stealth ya viene aplicado de forma nativa. Ver docstring del módulo.

        try:
            yield context
        finally:
            log.info("cerrando Chrome")
            try:
                await context.close()
            except Exception:
                pass


def shutdown_chrome() -> None:
    """Mata cualquier proceso del navegador colgado del user-data-dir del bot."""
    try:
        # El navegador lanza varios procesos hijo (renderer, gpu, utility), todos
        # con el --user-data-dir en su CommandLine. winutil los mata en Python
        # puro (WMI); matar el padre arrastra a los hijos y los ya-muertos se
        # ignoran en silencio.
        import winutil
        winutil.kill_browser_processes(config.BROWSER_PROC, config.USER_DATA_DIR)
    except Exception as exc:
        log.warning("shutdown_chrome: %s", exc)


def _clear_profile_locks() -> None:
    """
    Borra los ficheros Singleton* del perfil que Chrome usa como lock de
    instancia única. Si una corrida previa murió sin cerrar limpio (PC
    suspendido, cierre forzado), estos locks quedan y hacen que el siguiente
    launch_persistent_context arranque Chrome y se cierre de inmediato
    ("Target page, context or browser has been closed"). En Windows son
    ficheros normales (no symlinks como en Linux), así que se pueden borrar.
    """
    user_data = Path(config.USER_DATA_DIR)
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock = user_data / name
        try:
            if lock.exists():
                lock.unlink()
                log.info("lock de perfil eliminado: %s", lock)
        except Exception as exc:
            log.warning("no se pudo borrar %s: %s", lock, exc)
