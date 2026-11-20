"""
Configuración de la cuenta al instalar, SIN consola ni preguntas.

Lo lanza setup.exe (oculto, con la salida en su propio log):
    setup_cli.py [--browser chrome|edge]

1. Fija MSR_USER_ID (el nombre del equipo, si no existía) y MSR_BROWSER.
2. Deja el perfil del bot limpio (mismo reset que «Cambiar cuenta»).
3. Abre el navegador para que la persona inicie sesión ELLA MISMA. No se pide
   ni se guarda ninguna contraseña. Si la sesión caduca algún día, se renueva
   con «Login manual» en el panel.

El modo maintainer (auto-reparación de selectores con IA local) ya no se
pregunta aquí: se configura en un .env local (ver .env.example).

Códigos de salida: 0 = sesión iniciada; 2 = configurado pero sin login
confirmado (ventana cerrada / tiempo agotado); 1 = fallo al limpiar el perfil.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import socket
import sys

import config
import switch_account as switch

log = logging.getLogger("setup")


def _setx(name: str, value: str) -> None:
    """Persiste una variable de usuario (HKCU) y la aplica a este proceso."""
    try:
        import winutil  # noqa: PLC0415
        winutil.set_env_var(name, value)
    except Exception as exc:
        log.warning("set_env_var %s falló: %s", name, exc)
    os.environ[name] = value


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout)])

    current = os.environ.get("MSR_BROWSER", "").strip().lower()
    parser = argparse.ArgumentParser(description="Configura la cuenta del bot.")
    parser.add_argument("--browser", choices=("chrome", "edge"),
                        default=current if current in ("chrome", "edge") else "chrome")
    args = parser.parse_args(argv)

    if not os.environ.get("MSR_USER_ID"):
        _setx("MSR_USER_ID", socket.gethostname())
    _setx("MSR_BROWSER", args.browser)
    importlib.reload(config)   # USER_DATA_DIR / CHANNEL con los valores nuevos

    print(f">> Navegador: {config.BROWSER}  ·  perfil: {config.USER_DATA_DIR}", flush=True)
    if not switch.reset_session():
        print(">> No se pudo limpiar la sesión anterior. Cierra el navegador del bot "
              "y vuelve a intentarlo.", flush=True)
        return 1

    print(">> Abriendo el navegador para iniciar sesión…", flush=True)
    try:
        ok = asyncio.run(switch.browser_fresh_login())
    except Exception as exc:
        log.exception("login en el navegador falló: %s", exc)
        ok = False

    if ok:
        print(">> Sesión iniciada y guardada en el perfil del bot ✓", flush=True)
        return 0
    print(">> No se detectó el inicio de sesión (ventana cerrada o tiempo agotado). "
          "Puedes completarlo después con «Login manual» en el panel.", flush=True)
    return 2


if __name__ == "__main__":
    sys.exit(main())
