"""
Autologin a cuenta Microsoft.

Estrategia:
  1. Comprobar si la sesión ya está activa cargando rewards.bing.com y
     mirando que no aparezca el botón "Iniciar sesión".
  2. Si no, navegar a login.live.com y autorrellenar email + contraseña con
     tipeo humanizado (delays realistas).
  3. Si Microsoft pide 2FA / captcha / "actividad inusual" e `interactive`
     está activo, pausar y pedir al usuario que complete la verificación
     manualmente en la ventana de Chrome.
  4. Tras el submit, validar éxito intentando cargar rewards.bing.com de
     nuevo y comprobando ausencia de botón de login.

En ejecuciones programadas (no interactivas), si llega el 2FA, devolvemos
False y dejamos que run.py marque needs_relogin en el state.
"""
from __future__ import annotations

import asyncio
import logging

from patchright.async_api import BrowserContext, Page

import selectors
from humanize import human_type, safe_goto, sleep_jitter

log = logging.getLogger("login")

REWARDS_HOME = "https://rewards.bing.com/"
LOGIN_URL = "https://login.live.com/"


async def _is_signed_in(page: Page) -> bool:
    """
    True si la página actual indica que el usuario está logueado.
    Heurística doble: ausencia del botón de login + presencia de UI de cuenta.
    """
    try:
        sign_in = await page.query_selector(selectors.get("rewards.signin_button"))
        if sign_in:
            visible = False
            try:
                visible = await sign_in.is_visible()
            except Exception:
                visible = True
            if visible:
                return False
        acc = await page.query_selector(selectors.get("bing.signed_in_indicator", ""))
        if acc:
            return True
    except Exception as exc:
        log.debug("comprobación de sesión falló: %s", exc)
    # Si no hay botón de login visible, asumimos sesión activa
    return True


async def is_session_alive(context: BrowserContext) -> bool:
    """Carga rewards.bing.com y comprueba que no haya botón de login."""
    page = context.pages[0] if context.pages else await context.new_page()
    if not await safe_goto(page, REWARDS_HOME, attempts=4, timeout=20_000):
        return False
    await sleep_jitter(2.0, 4.0)
    return await _is_signed_in(page)


async def _detect_twofa(page: Page) -> bool:
    try:
        el = await page.query_selector(selectors.get("login.twofa_indicator"))
        return el is not None
    except Exception:
        return False


async def perform_login(
    context: BrowserContext,
    email: str,
    password: str,
    *,
    interactive: bool = True,
) -> bool:
    """
    Realiza el login. Devuelve True si la sesión queda activa.
    """
    page = context.pages[0] if context.pages else await context.new_page()

    if not await safe_goto(page, LOGIN_URL, attempts=5, timeout=25_000):
        log.error("no se pudo cargar login.live.com")
        return False
    await sleep_jitter(1.5, 3.0)

    # --- email ---
    try:
        await human_type(page, selectors.get("login.email_input"), email)
    except Exception as exc:
        log.error("no se pudo escribir el email: %s", exc)
        return False
    await sleep_jitter(0.5, 1.4)
    try:
        await page.click(selectors.get("login.email_next"))
    except Exception:
        # Algunos flows submitean con Enter
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
    await sleep_jitter(2.0, 4.0)

    # --- password ---
    try:
        await human_type(page, selectors.get("login.password_input"), password)
    except Exception as exc:
        # Puede haber saltado a 2FA antes incluso de pedir contraseña en
        # cuentas con passkeys u otros métodos.
        if interactive and await _detect_twofa(page):
            return await _interactive_wait(context)
        log.error("no se pudo escribir la contraseña: %s", exc)
        return False
    await sleep_jitter(0.5, 1.4)
    try:
        await page.click(selectors.get("login.password_submit"))
    except Exception:
        try:
            await page.keyboard.press("Enter")
        except Exception:
            pass
    await sleep_jitter(3.0, 6.0)

    # --- 2FA / captcha ---
    if await _detect_twofa(page):
        if interactive:
            return await _interactive_wait(context)
        log.warning("2FA requerido y modo no-interactivo: abortando login")
        return False

    # --- "¿Mantener sesión iniciada?" ---
    try:
        kmsi = await page.query_selector(selectors.get("login.stay_signed_in_yes"))
        if kmsi and await kmsi.is_visible():
            await sleep_jitter(1.0, 2.0)
            await kmsi.click()
            await sleep_jitter(2.0, 4.0)
    except Exception:
        pass

    return await is_session_alive(context)


async def _interactive_wait(context: BrowserContext) -> bool:
    print(
        "\n"
        "============================================================\n"
        " Microsoft pide verificación adicional (2FA / captcha).\n"
        " Por favor, completa la verificación en la ventana de Chrome.\n"
        " Cuando hayas terminado y veas el dashboard de rewards, pulsa\n"
        " ENTER aquí para continuar...\n"
        "============================================================\n"
    )
    # input() bloquea el loop de asyncio; lo metemos en un executor.
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, input)
    return await is_session_alive(context)
