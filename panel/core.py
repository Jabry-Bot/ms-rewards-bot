"""
Lógica pura del panel de control de ms_rewards.

Este módulo NO importa ninguna librería de GUI ni el código del bot: solo
resuelve rutas, construye las líneas de comando que la GUI lanzará como
subprocesos, y formatea el estado leído de `state/last_run.json`. Así puede
testearse sin display ni dependencias pesadas (patchright, customtkinter).

La GUI (panel/app.py) y los tests (tests/test_core.py) consumen esta API.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

# --- Rutas ---------------------------------------------------------------
# core.py vive en <repo>/panel/core.py → el repo es dos niveles arriba.
# Dentro del .exe de PyInstaller (onefile), __file__ se desempaqueta en una
# carpeta temporal (_MEIPASS), así que no sirve para localizar el repo: en ese
# caso anclamos a la carpeta donde está el propio .exe (que debe vivir junto a
# ms_rewards/). En desarrollo (no congelado) usamos la ruta del fuente.
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent
REWARDS_DIR = ROOT / "ms_rewards"
VENV_PY = REWARDS_DIR / ".venv" / "Scripts" / "python.exe"
RUN_PY = REWARDS_DIR / "run.py"
SWITCH_PY = REWARDS_DIR / "switch_account.py"
UNINSTALL_PY = REWARDS_DIR / "uninstall.py"
WINUTIL_PY = REWARDS_DIR / "winutil.py"
STATE_DIR = REWARDS_DIR / "state"
LAST_RUN_PATH = STATE_DIR / "last_run.json"
LOG_DIR = REWARDS_DIR / "logs"

TASK_NAME = "MsRewardsBot"


# --- Acciones del bot ----------------------------------------------------
# Cada acción mapea a una invocación de run.py. La GUI muestra `label` en el
# botón y lanza `build_run_command(action_id)` como subproceso.
@dataclass(frozen=True)
class Action:
    id: str
    label: str
    flags: tuple[str, ...]
    description: str
    # Si True, la GUI debería pedir confirmación antes de lanzar (acciones
    # destructivas o que abren ventanas de navegador para login manual).
    confirm: bool = False
    # Si True, es una corrida del bot sobre el navegador y la GUI ofrece elegir
    # primer plano (navegador visible) o segundo plano (oculto, no molesta)
    # antes de lanzar. Las acciones que no abren navegador (kill) o que deben
    # ser visibles sí o sí (login) lo dejan en False.
    prompt_visibility: bool = False


ACTIONS: dict[str, Action] = {
    "run_all": Action(
        "run_all", "▶  Ejecutar ahora",
        ("--force",),
        "Daily set + búsquedas desktop + móvil (forzado, ignora 'ya completado hoy').",
        prompt_visibility=True,
    ),
    "daily": Action(
        "daily", "📋  Solo daily set",
        ("--daily", "--force"),
        "Resuelve únicamente el daily set / actividades.",
        prompt_visibility=True,
    ),
    "searches": Action(
        "searches", "🔍  Solo búsquedas",
        ("--searches", "--force"),
        "Solo las búsquedas de Bing (desktop + móvil).",
        prompt_visibility=True,
    ),
    "login": Action(
        "login", "🔐  Login manual",
        ("--setup",),
        "Abre el navegador para iniciar sesión a mano (2FA / captcha).",
    ),
    "kill": Action(
        "kill", "⏹  Detener navegador",
        ("--kill",),
        "Mata cualquier navegador del bot que quede colgado.",
        confirm=True,
    ),
}


def venv_ready() -> bool:
    """True si el entorno virtual del bot existe (setup.exe ya corrió)."""
    return VENV_PY.exists()


# --- Auto-actualización del propio .exe ----------------------------------
# El .exe es un binario congelado: el git pull del bot actualiza el código
# Python en disco, pero NO el .exe. Para que los cambios de GUI lleguen a los
# usuarios, el panel comprueba al abrirse si la versión en disco (la que dejó
# el último git pull) es mayor que la versión con la que se compiló este .exe;
# si lo es, descarga el nuevo MsRewardsPanel.exe del release y lo intercambia.
PANEL_EXE_NAME = "MsRewardsPanel.exe"
PANEL_EXE_URL = (
    "https://github.com/Jabry-Bot/ms-rewards-bot/releases/latest/download/"
    + PANEL_EXE_NAME
)
VERSION_FILE = REWARDS_DIR / "VERSION"


def is_frozen() -> bool:
    """True si corremos dentro del .exe de PyInstaller."""
    return bool(getattr(sys, "frozen", False))


def _read_version(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def build_version() -> str:
    """Versión con la que se compiló este .exe (VERSION empaquetado dentro)."""
    if is_frozen():
        v = _read_version(Path(getattr(sys, "_MEIPASS", "")) / "VERSION")
        if v:
            return v
    return _read_version(VERSION_FILE)


def available_version() -> str:
    """Versión del código en disco (la que dejó el último git pull)."""
    return _read_version(VERSION_FILE)


def _vtuple(s: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", s or "")
    return tuple(int(n) for n in nums) if nums else (0,)


def self_update_available() -> bool:
    """True si hay una versión del .exe más nueva que la compilada."""
    if not is_frozen():
        return False
    b, a = build_version(), available_version()
    if not b or not a:
        return False
    try:
        return _vtuple(a) > _vtuple(b)
    except Exception:
        return False


def current_exe() -> Path:
    return Path(sys.executable)


def _old_exe_path() -> Path:
    exe = current_exe()
    return exe.with_name(exe.stem + ".old.exe")


def cleanup_old_exe() -> None:
    """Borra el .old.exe que dejó una auto-actualización previa (ya no en uso)."""
    if not is_frozen():
        return
    try:
        old = _old_exe_path()
        if old.exists():
            old.unlink()
    except Exception:
        pass


def download_panel_exe(dest: str | Path) -> None:
    """Descarga el último MsRewardsPanel.exe del release a `dest`."""
    urllib.request.urlretrieve(PANEL_EXE_URL, str(dest))


def swap_exe(new_exe: str | Path) -> Path:
    """
    Sustituye el .exe en uso por `new_exe`: renombra el actual a .old.exe (en
    Windows se puede renombrar un ejecutable en uso) y pone el nuevo en su sitio.
    Devuelve la ruta final del .exe (la misma de siempre).
    """
    exe = current_exe()
    old = _old_exe_path()
    try:
        if old.exists():
            old.unlink()
    except Exception:
        pass
    exe.rename(old)
    Path(new_exe).rename(exe)
    return exe


def icon_path() -> Path | None:
    """
    Ruta del icono de la app (assets/icon.ico), o None si no existe. En el .exe
    de PyInstaller los datos se extraen a sys._MEIPASS; en desarrollo, al repo.
    """
    base = Path(getattr(sys, "_MEIPASS", ROOT))
    ico = base / "assets" / "icon.ico"
    if ico.exists():
        return ico
    ico = ROOT / "assets" / "icon.ico"
    return ico if ico.exists() else None


def build_update_command() -> list[str]:
    """
    Comando que comprueba/aplica el auto-update (git pull) y sale. Lo lanza el
    panel al arrancar; reutiliza run.py --update-only, que degrada con gracia si
    no hay red, no es un repo git o no hay versión nueva.
    """
    return [str(VENV_PY), str(RUN_PY), "--update-only"]


def build_run_command(action_id: str, hidden: bool = False) -> list[str]:
    """
    Línea de comando para una acción de run.py.

    `hidden=True` añade --hidden para que el navegador corra en segundo plano
    (oculto) en una ejecución manual; por defecto la ventana es visible.
    """
    if action_id not in ACTIONS:
        raise KeyError(f"acción desconocida: {action_id!r}")
    action = ACTIONS[action_id]
    cmd = [str(VENV_PY), str(RUN_PY), *action.flags]
    if hidden:
        cmd.append("--hidden")
    return cmd


def build_switch_command() -> list[str]:
    """Línea de comando para el cambio de cuenta (switch_account.py)."""
    return [str(VENV_PY), str(SWITCH_PY)]


# Opciones de desinstalación: (clave de flag, etiqueta para la GUI, marcado por
# defecto). Cada una mapea a un flag --<clave> de ms_rewards/uninstall.py.
UNINSTALL_OPTIONS: list[tuple[str, str, bool]] = [
    ("task", "Quitar la tarea programada (deja de ejecutarse solo)", True),
    ("state", "Borrar el estado de ejecución (last_run.json)", True),
    ("credentials", "Borrar las credenciales cifradas", False),
    ("profile", "Borrar el perfil del navegador (pierdes la sesión iniciada)", False),
    ("env", "Quitar las variables de entorno MSR_*", False),
]


def build_uninstall_command(options: list[str]) -> list[str]:
    """
    Línea de comando para desinstalar (uninstall.py, no interactivo).

    `options` son claves de UNINSTALL_OPTIONS; cada una se pasa como --<clave>.
    Lanza ValueError si se cuela una opción desconocida.
    """
    valid = {key for key, _, _ in UNINSTALL_OPTIONS}
    bad = [o for o in options if o not in valid]
    if bad:
        raise ValueError(f"opciones de desinstalación desconocidas: {bad}")
    return [str(VENV_PY), str(UNINSTALL_PY), *(f"--{o}" for o in options)]


def build_task_command(install: bool) -> list[str]:
    """Registra (install=True) o quita la Scheduled Task vía winutil (Python puro)."""
    sub = "task-install" if install else "task-uninstall"
    return [str(VENV_PY), str(WINUTIL_PY), sub]


def build_task_query_command() -> list[str]:
    """
    Comando que emite en JSON el estado de la Scheduled Task (winutil task-status,
    Python puro). La GUI parsea la salida con `parse_task_query`; `{}` si no existe.
    """
    return [str(VENV_PY), str(WINUTIL_PY), "task-status"]


def parse_task_query(stdout: str) -> dict[str, Any]:
    """Parsea la salida de `build_task_query_command`. {} si no hay tarea."""
    stdout = (stdout or "").strip()
    if not stdout:
        return {}
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_last_run() -> dict[str, Any]:
    """Lee state/last_run.json. {} si no existe o está corrupto."""
    if not LAST_RUN_PATH.exists():
        return {}
    try:
        with LAST_RUN_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


# Estado → (etiqueta legible, color hex para el badge de la GUI).
_STATUS_STYLES: dict[str, tuple[str, str]] = {
    "ok": ("Completado", "#2ecc71"),
    "empty": ("Sin actividad", "#f39c12"),
    "needs_relogin": ("Requiere login", "#e74c3c"),
    "selectors_broken": ("Selectores rotos", "#e67e22"),
    "error": ("Error", "#e74c3c"),
}
_UNKNOWN_STYLE = ("Desconocido", "#95a5a6")


def status_style(last_run: dict[str, Any]) -> tuple[str, str]:
    """(etiqueta, color) para el estado actual del bot."""
    status = str(last_run.get("status", "")).lower()
    return _STATUS_STYLES.get(status, _UNKNOWN_STYLE)


def completed_today(last_run: dict[str, Any] | None = None) -> bool:
    """True si el bot ya marcó hoy como completado."""
    if last_run is None:
        last_run = read_last_run()
    return last_run.get("last_completed") == date.today().isoformat()


def format_status(last_run: dict[str, Any], task: dict[str, Any] | None = None) -> str:
    """
    Resumen multilínea legible del estado del bot, para el panel lateral.

    `last_run` es el dict de last_run.json; `task` el de `parse_task_query`.
    """
    lines: list[str] = []
    label, _ = status_style(last_run)
    lines.append(f"Estado: {label}")

    if last_run:
        if "last_completed" in last_run:
            hoy = " (hoy)" if completed_today(last_run) else ""
            lines.append(f"Último completado: {last_run['last_completed']}{hoy}")
        if last_run.get("points_after") is not None:
            pa = last_run.get("points_after")
            pb = last_run.get("points_before")
            if pb is not None:
                lines.append(f"Puntos: {pb} → {pa}  ({pa - pb:+d})")
            else:
                lines.append(f"Puntos: {pa}")
        if last_run.get("level") is not None:
            lines.append(f"Nivel: {last_run['level']}")
        partes = []
        if last_run.get("searches_done") is not None:
            partes.append(f"{last_run['searches_done']} desktop")
        if last_run.get("mobile_searches_done") is not None:
            partes.append(f"{last_run['mobile_searches_done']} móvil")
        if partes:
            lines.append("Búsquedas: " + " · ".join(partes))
        if last_run.get("daily_done") is not None:
            lines.append(f"Daily cards: {last_run['daily_done']}")
        if last_run.get("updated_at"):
            lines.append(f"Actualizado: {last_run['updated_at']}")
    else:
        lines.append("(aún no hay ninguna corrida registrada)")

    lines.append("")
    if task:
        lines.append("Tarea programada: registrada")
        if task.get("state"):
            lines.append(f"  Estado: {task['state']}")
        if task.get("next_run"):
            lines.append(f"  Próxima: {task['next_run']}")
        if task.get("last_run"):
            lines.append(
                f"  Última: {task['last_run']} (código {task.get('last_result', '?')})"
            )
    else:
        lines.append("Tarea programada: NO registrada")

    return "\n".join(lines)


def today_log_path() -> Path:
    """Ruta del log de hoy (puede no existir todavía)."""
    return LOG_DIR / f"{date.today():%Y%m%d}.log"
