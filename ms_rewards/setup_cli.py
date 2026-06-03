"""
Asistente interactivo de instalación: USER_ID, credenciales, login asistido,
opcionalmente modo maintainer.

Se invoca desde setup.bat tras instalar dependencias.
"""
from __future__ import annotations

import asyncio
import getpass
import logging
import os
import socket
import subprocess
import sys

import config
import credentials
from launcher import launch
from login import is_session_alive, perform_login

log = logging.getLogger("setup")


def _setx(name: str, value: str) -> None:
    """Persistir variable de entorno del usuario actual (HKCU)."""
    try:
        subprocess.run(["setx", name, value], capture_output=True, text=True, check=False)
        # `setx` no afecta a la sesión actual; lo seteamos también en os.environ
        # para que el resto del setup_cli use el nuevo valor.
        os.environ[name] = value
    except Exception as exc:
        log.warning("setx %s falló: %s", name, exc)


def _ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    val = input(f"{prompt}{hint}: ").strip()
    return val or default


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    d = "S/n" if default else "s/N"
    val = input(f"{prompt} [{d}]: ").strip().lower()
    if not val:
        return default
    return val in ("s", "si", "sí", "y", "yes")


async def _login_flow(email: str, password: str) -> bool:
    print("\n>> Abriendo Chrome para hacer login (puede pedir 2FA la primera vez)...")
    async with launch() as ctx:
        if await is_session_alive(ctx):
            print(">> Ya hay sesión activa en este perfil. Login no necesario.")
            return True
        ok = await perform_login(ctx, email, password, interactive=True)
        return ok


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    print("============================================================")
    print(" Setup de ms_rewards")
    print("============================================================\n")

    # 1) USER_ID
    default_user = os.environ.get("MSR_USER_ID") or socket.gethostname()
    user_id = _ask("Identificador para esta instalación", default_user)
    _setx("MSR_USER_ID", user_id)
    # Recargar config para que USER_DATA_DIR refleje el nuevo USER_ID
    import importlib
    importlib.reload(config)

    print(f"\n>> Perfil de Chrome: {config.USER_DATA_DIR}\n")

    # 2) Credenciales
    if not credentials.is_supported():
        print(
            "ADVERTENCIA: pywin32 no disponible — las credenciales no se "
            "guardarán cifradas. Tendrás que hacer login manualmente cada vez.\n"
        )
    existing = credentials.load() if credentials.is_supported() else None
    if existing:
        print(f">> Ya hay credenciales guardadas para {existing[0]}.")
        if _ask_yes_no("¿Quieres sobrescribirlas?", default=False):
            existing = None

    if existing is None:
        email = _ask("Email de la cuenta Microsoft")
        if not email:
            print("ERROR: email vacío, abortando.")
            return 1
        password = getpass.getpass("Contraseña (no se mostrará): ")
        if not password:
            print("ERROR: contraseña vacía, abortando.")
            return 1
        if credentials.is_supported():
            credentials.save(email, password)
            print(">> Credenciales cifradas y guardadas.")
        else:
            print(">> Credenciales NO guardadas (DPAPI no disponible).")
    else:
        email, password = existing

    # 3) Login asistido
    try:
        ok = asyncio.run(_login_flow(email, password))
    except Exception as exc:
        log.exception("login falló: %s", exc)
        ok = False

    if ok:
        print("\n>> Login OK. La sesión ha quedado guardada en el perfil.\n")
    else:
        print(
            "\n>> Login NO confirmado. Revisa email/contraseña o completa la\n"
            "   verificación manual en Chrome cuando vuelvas a ejecutar setup.bat.\n"
        )

    # 4) Maintainer mode (opcional)
    if _ask_yes_no("\n¿Esta máquina es la del maintainer (auto-fix de selectores con Ollama)?", default=False):
        ollama_url = _ask("Endpoint Ollama", os.environ.get("MSR_OLLAMA_URL", "http://localhost:11434"))
        ollama_model = _ask("Modelo Ollama", os.environ.get("MSR_OLLAMA_MODEL", "qwen2.5-coder:7b"))
        _setx("MSR_MAINTAINER", "1")
        _setx("MSR_OLLAMA_URL", ollama_url)
        _setx("MSR_OLLAMA_MODEL", ollama_model)
        print(">> Modo maintainer activado.\n")

    # Resumen
    print("============================================================")
    print(" Resumen")
    print("============================================================")
    print(f" USER_ID         : {user_id}")
    print(f" Perfil Chrome   : {config.USER_DATA_DIR}")
    print(f" Credenciales    : {'guardadas' if credentials.load() else 'NO guardadas'}")
    print(f" Sesión activa   : {'sí' if ok else 'no — re-ejecuta setup.bat'}")
    print(f" Maintainer mode : {'sí' if os.environ.get('MSR_MAINTAINER') == '1' else 'no'}")
    print("============================================================\n")
    print("setup.bat continuará registrando la Scheduled Task de Windows.\n")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
