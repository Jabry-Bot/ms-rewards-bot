"""
Ejecución discreta en segundo plano: que el bot no moleste a quien usa el PC.

Dos problemas que resuelve:

1. Ventanas de consola que aparecen "de la nada".
   La Scheduled Task arranca `pythonw.exe`, que no tiene consola. Cuando un
   proceso sin consola lanza un programa de consola (git.exe, el driver node
   de patchright, taskkill...), Windows le crea una consola NUEVA y visible
   para cada uno. En Windows 11 suele ser una ventana de Terminal, que además
   puede robar el foco y sacar de un juego a pantalla completa.

   Solución de raíz: bajo pythonw nos relanzamos como `python.exe` con
   CREATE_NO_WINDOW (lo mismo que ya hace el panel). Ese hijo tiene una consola
   SIN ventana y todos sus descendientes la heredan, así que ningún programa de
   consola que se lance después puede abrir ventana.

2. Arrancar justo cuando alguien está jugando.
   `SHQueryUserNotificationState` (la API que usa Windows para no mostrar
   notificaciones durante juegos/presentaciones) dice si hay una app o un juego
   a pantalla completa. Las corridas automáticas se posponen mientras dure y la
   tarea programada las reintenta más tarde.

Este módulo no importa config ni nada del bot para poder testearse aislado.
"""
from __future__ import annotations

import ctypes
import datetime as _dt
import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("quiet")

# Flag de CreateProcess: consola sin ventana. 0 fuera de Windows.
NO_WINDOW: int = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Marca de entorno del hijo relanzado (evita relanzarse en bucle).
CHILD_ENV = "MSR_HIDDEN_CHILD"


# --- 1. Consola invisible -------------------------------------------------
def should_respawn(executable: str | None = None, env: dict | None = None) -> bool:
    """True si corremos bajo pythonw.exe y aún no somos el hijo relanzado."""
    exe = os.path.basename(executable if executable is not None else sys.executable).lower()
    env = os.environ if env is None else env
    return exe.startswith("pythonw") and env.get(CHILD_ENV) != "1"


def console_python(executable: str | None = None) -> Path:
    """python.exe hermano de pythonw.exe (en un venv siempre existen los dos)."""
    exe = Path(executable if executable is not None else sys.executable)
    return exe.with_name("python.exe")


def child_args(argv: list[str]) -> list[str]:
    """
    Argumentos para el hijo. Bajo pythonw la corrida es la automática, así que
    se garantiza --scheduled (las tareas antiguas no lo pasaban) para que el
    hijo, que ya es python.exe, siga sabiendo que va oculto.
    """
    args = list(argv)
    if "--scheduled" not in args:
        args.append("--scheduled")
    return args


def respawn_hidden(script: str, argv: list[str]) -> int | None:
    """
    Si corremos bajo pythonw, relanza `script` con python.exe + CREATE_NO_WINDOW,
    espera a que termine y devuelve su código de salida. None si no hace falta
    (o no se pudo): en ese caso el llamador sigue en este mismo proceso.
    """
    if not should_respawn():
        return None
    py = console_python()
    if not py.exists():
        return None
    env = dict(os.environ)
    env[CHILD_ENV] = "1"
    try:
        proc = subprocess.run(
            [str(py), script, *child_args(argv)],
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW,
        )
        return proc.returncode
    except Exception:
        return None  # mejor correr con pythonw que no correr


# --- 2. ¿Está el usuario jugando / a pantalla completa? -------------------
# Valores de QUERY_USER_NOTIFICATION_STATE (shellapi.h).
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2                      # app a pantalla completa (incluye borderless)
QUNS_RUNNING_D3D_FULL_SCREEN = 3   # juego Direct3D en pantalla completa exclusiva
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

_BUSY_REASONS = {
    QUNS_BUSY: "app a pantalla completa",
    QUNS_RUNNING_D3D_FULL_SCREEN: "juego a pantalla completa",
    QUNS_PRESENTATION_MODE: "modo presentación",
}


def notification_state() -> int | None:
    """Estado de SHQueryUserNotificationState, o None si no se pudo leer."""
    try:
        state = ctypes.c_int(0)
        hr = ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))
        if hr != 0:
            return None
        return state.value
    except Exception:
        return None


def busy_reason(state: int | None) -> str | None:
    """Motivo legible si `state` indica que no hay que molestar; None si no."""
    if state is None:
        return None
    return _BUSY_REASONS.get(state)


def user_busy() -> str | None:
    """Motivo si ahora mismo hay un juego/app a pantalla completa; None si no."""
    return busy_reason(notification_state())


def game_deadline_hour() -> int:
    """
    Hora a partir de la cual ya no se pospone por juego (la corrida es invisible
    igualmente; solo evitamos perder el día). MSR_GAME_DEADLINE_HOUR, por
    defecto 21. Con 24 no se corre nunca mientras haya pantalla completa.
    """
    try:
        return int(os.getenv("MSR_GAME_DEADLINE_HOUR", "21"))
    except ValueError:
        return 21


def should_postpone(reason: str | None, now: _dt.datetime | None = None) -> bool:
    """True si hay que posponer: hay pantalla completa y aún no es la hora límite."""
    if not reason:
        return False
    now = now or _dt.datetime.now()
    return now.hour < game_deadline_hour()
