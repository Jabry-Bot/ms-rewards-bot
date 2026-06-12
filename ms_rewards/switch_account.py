"""
Cambio de cuenta robusto.

A diferencia del enfoque en .bat (rmdir frágil que sufría una race con el
kill de Chrome y dejaba la sesión vieja viva), aquí:
  1. Matamos SOLO el Chrome del bot (por user-data-dir) y ESPERAMOS a que
     los procesos mueran de verdad antes de tocar el perfil.
  2. Borramos el perfil con reintentos (los archivos pueden seguir
     bloqueados unos instantes tras el kill) y VERIFICAMOS que desaparece.
  3. Borramos credenciales + state.
  4. Pedimos las credenciales de la cuenta nueva y FORZAMOS el login
     (sin el atajo "ya hay sesión activa", que era justo lo que impedía
     entrar con la cuenta nueva si el wipe fallaba).
"""
from __future__ import annotations

import asyncio
import getpass
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

import config
import credentials
from launcher import launch, shutdown_chrome
from login import perform_login, is_session_alive

log = logging.getLogger("switch")


def _bot_chrome_count() -> int:
    """Cuántos procesos del navegador están abiertos sobre el perfil del bot."""
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='" + config.BROWSER_PROC + "'\" | "
        "Where-Object { $_.CommandLine -like '*" + config.USER_DATA_DIR + "*' } | "
        "Measure-Object | Select-Object -Expand Count"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15,
        )
        return int((out.stdout or "0").strip() or "0")
    except Exception:
        return 0


def _kill_and_wait(timeout: float = 15.0) -> bool:
    """Mata el Chrome del bot y espera a que desaparezca. True si quedó limpio."""
    shutdown_chrome()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _bot_chrome_count() == 0:
            # margen extra para que el SO libere los handles del perfil
            time.sleep(1.5)
            return True
        time.sleep(0.5)
    return _bot_chrome_count() == 0


def _wipe_profile(retries: int = 6) -> bool:
    """Borra el perfil del bot con reintentos. True si ya no existe."""
    p = Path(config.USER_DATA_DIR)
    if not p.exists():
        return True
    for i in range(retries):
        try:
            shutil.rmtree(p)
        except Exception as exc:
            log.warning("intento %d de borrar perfil falló: %s", i + 1, exc)
            time.sleep(1.5)
        if not p.exists():
            return True
    # Último recurso: borrado tolerante a errores
    shutil.rmtree(p, ignore_errors=True)
    return not p.exists()


def reset_session() -> bool:
    """
    Deja el perfil del bot como recién instalado: cierra el Chrome del bot,
    espera a que libere el perfil, lo borra, y borra credenciales + state.
    Devuelve True si todo quedó limpio. Lo comparten switch_account y setup
    para que ambos hagan EXACTAMENTE el mismo reset antes del login.
    """
    print("[*] Cerrando Chrome del bot y esperando a que libere el perfil...")
    if not _kill_and_wait():
        print(
            "  AVISO: aún hay Chrome del bot abierto. Cierra cualquier ventana "
            "del bot manualmente y reintenta."
        )
        return False
    print("  OK, sin procesos del bot.")

    print("[*] Borrando la sesión anterior (perfil de Chrome)...")
    if not _wipe_profile():
        print(
            "  ERROR: no se pudo borrar el perfil por completo. Puede haber un "
            "archivo bloqueado. Cierra TODO Chrome y reintenta."
        )
        return False
    print("  OK, perfil eliminado.")

    print("[*] Borrando credenciales y estado anteriores...")
    try:
        credentials.delete()
    except Exception:
        pass
    try:
        if config.LAST_RUN_PATH.exists():
            config.LAST_RUN_PATH.unlink()
    except Exception:
        pass
    print("  OK.")
    return True


async def fresh_login(email: str, password: str) -> bool:
    """Lanza el contexto y fuerza el login (sin atajo de sesión activa)."""
    async with launch() as ctx:
        # Verificación defensiva: tras el wipe NO debería haber sesión. Si la
        # hay, es que el wipe no fue completo — avisamos pero forzamos login
        # igualmente (perform_login navega a login.live.com directamente).
        if await is_session_alive(ctx):
            log.warning(
                "todavía se detecta una sesión activa tras el wipe; "
                "forzando login de todos modos"
            )
        ok = await perform_login(ctx, email, password, interactive=True)
        return ok


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    print("=" * 60)
    print(" Cambiar de cuenta - ms_rewards")
    print("=" * 60)
    print(f"\n Perfil del bot: {config.USER_DATA_DIR}\n")

    # 1-3) Reset robusto del perfil (kill + wait + wipe + borrar creds/state)
    if not reset_session():
        return 1

    # 4) Credenciales nuevas + login forzado
    print("[4/4] Cuenta nueva:")
    email = input("  Email de la cuenta Microsoft: ").strip()
    if not email:
        print("  ERROR: email vacío. Cancelado.")
        return 1
    password = getpass.getpass("  Contraseña (no se mostrará): ")
    if not password:
        print("  ERROR: contraseña vacía. Cancelado.")
        return 1

    if credentials.is_supported():
        credentials.save(email, password)
        print("  Credenciales cifradas y guardadas.")

    try:
        ok = asyncio.run(fresh_login(email, password))
    except Exception as exc:
        log.exception("login falló: %s", exc)
        ok = False

    print("\n" + "=" * 60)
    if ok:
        print(" Cuenta cambiada correctamente. Sesión nueva guardada.")
    else:
        print(" El login NO se confirmó. Revisa email/contraseña o completa")
        print(" la verificación (2FA) en la ventana de Chrome y reintenta")
        print(" ejecutando switch_account.bat de nuevo.")
    print("=" * 60)
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
