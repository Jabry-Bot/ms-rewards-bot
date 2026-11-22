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
import os
import re
import sys
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
APP_TOKEN_PATH = STATE_DIR / "app_token.bin"   # permiso de la app de Bing (DPAPI)
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
        "Daily set + búsquedas desktop + imagen (forzado, ignora 'ya completado hoy').",
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
        "Solo las búsquedas de Bing (desktop + imagen).",
        prompt_visibility=True,
    ),
    "login": Action(
        "login", "🔐  Login manual",
        ("--setup",),
        "Abre el navegador para iniciar sesión a mano (2FA / captcha).",
    ),
    "link_app": Action(
        "link_app", "📱  Vincular app de Bing",
        ("--link-app",),
        "Abre una ventana para que inicies sesión tú (una vez). Desde entonces el "
        "bot hace el check-in y «Leer para ganar» de la app de Bing (+30/día).",
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
    """
    Línea de comando para el cambio de cuenta (switch_account.py --browser):
    borra la sesión y abre el navegador visible para que el usuario inicie
    sesión a mano (2FA incluido). No se piden ni se guardan credenciales.
    """
    return [str(VENV_PY), str(SWITCH_PY), "--browser"]


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


# --- Navegador (Chrome / Edge) ------------------------------------------
# El scraper elige navegador por la variable de entorno MSR_BROWSER (chrome|edge),
# que config.py lee para derivar CHANNEL/BROWSER_PROC/CHROME_PATH. El panel la
# persiste (HKCU\Environment) para futuras corridas y la tarea programada, y la
# inyecta en el entorno del subproceso inmediato. Edge otorga el bonus de
# búsquedas de Microsoft Rewards.
VALID_BROWSERS = ("chrome", "edge")
DEFAULT_BROWSER = "chrome"


def get_browser() -> str:
    """Navegador activo del panel: MSR_BROWSER de os.environ, normalizado.

    Misma normalización que config.py (chrome|edge, chrome por defecto). Es el
    valor que heredó el proceso del panel; refleja lo persistido en la sesión.
    """
    val = os.environ.get("MSR_BROWSER", DEFAULT_BROWSER).strip().lower()
    return val if val in VALID_BROWSERS else DEFAULT_BROWSER


def build_env_set_command(name: str, value: str) -> list[str]:
    """Comando que persiste una variable de usuario vía winutil (Python puro).

    El panel congelado no puede importar winutil; lo invoca con el venv-python,
    igual que build_task_command. Persiste en HKCU\\Environment (setx-like).
    """
    return [str(VENV_PY), str(WINUTIL_PY), "env-set", name, value]


def build_browser_env_command(browser: str) -> list[str]:
    """Comando para persistir MSR_BROWSER. Valida contra VALID_BROWSERS."""
    browser = (browser or "").strip().lower()
    if browser not in VALID_BROWSERS:
        raise ValueError(f"navegador desconocido: {browser!r}")
    return build_env_set_command("MSR_BROWSER", browser)


def build_env_with_browser(browser: str | None = None) -> dict[str, str]:
    """Entorno para el subprocess.Popen inmediato con MSR_BROWSER forzado.

    Persistir en el registro hace broadcast pero NO cambia el os.environ ya
    heredado por procesos vivos; pasamos MSR_BROWSER explícito en el env del
    Popen para que la corrida lanzada ahora use el navegador elegido.
    """
    env = dict(os.environ)
    env["MSR_BROWSER"] = get_browser() if browser is None else browser
    return env


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
    "incomplete": ("Incompleto", "#f39c12"),
    "needs_relogin": ("Requiere login", "#e74c3c"),
    "selectors_broken": ("Selectores rotos", "#e67e22"),
    "error": ("Error", "#e74c3c"),
}
_UNKNOWN_STYLE = ("Desconocido", "#95a5a6")


def status_style(last_run: dict[str, Any]) -> tuple[str, str]:
    """(etiqueta, color) para el estado actual del bot."""
    status = str(last_run.get("status", "")).lower()
    return _STATUS_STYLES.get(status, _UNKNOWN_STYLE)


def app_linked() -> bool:
    """True si la app de Bing está vinculada (existe el permiso cifrado)."""
    return APP_TOKEN_PATH.exists()


def app_enabled(linked: bool | None = None) -> bool:
    """Misma regla que config.APP_ACTIVITIES: MSR_APP_ACTIVITIES=1 la fuerza,
    =0 la apaga y, sin la variable, va si la cuenta está vinculada."""
    env = os.environ.get("MSR_APP_ACTIVITIES", "").strip()
    if env == "0":
        return False
    return env == "1" or (app_linked() if linked is None else linked)


_APP_STYLES: dict[str, tuple[str, str]] = {
    "unlinked": ("Sin vincular", "#95a5a6"),
    "disabled": ("Desactivada", "#95a5a6"),
    "relink": ("Revincular", "#e74c3c"),
    "pending": ("Pendiente hoy", "#3498db"),
    "done": ("Hecho hoy", "#2ecc71"),
    "partial": ("Incompleto", "#f39c12"),
}


_APP_CHECKIN_OK = re.compile(r"\[app\] \w+: check-in de la app: hecho")
_APP_CHECKIN_DONE = re.compile(r"\[app\] \w+: check-in de la app: ya hecho hoy")
_APP_CHECKIN_BAD = re.compile(r"\[app\] \w+: check-in de la app: (?:enviado pero SIN|error)")
_APP_READ = re.compile(r"\[app\] \w+: (?:Leer para ganar: (?:ya completo \()?|lectura \d+: )"
                       r"(\d+)/(\d+)")
_APP_UNLINKED = re.compile(r"\[app\] \w+: app de Bing: sin permiso")


def app_from_log(text: str, today: str | None = None) -> dict[str, Any] | None:
    """
    Resumen de la app de Bing sacado del log de hoy, con la misma forma que
    last_run["app"]. Sirve cuando la corrida del día no guardó ese resumen.
    None si el log no tiene líneas de la app.
    """
    out: dict[str, Any] = {}
    for line in (text or "").splitlines():
        if _APP_UNLINKED.search(line):
            out = {"linked": False}
            continue
        m = _APP_CHECKIN_OK.search(line)
        if m:
            out.update(linked=True, checkin="ok")
            day = re.search(r"→\s*(\d+)", line)
            if day:
                out["streak_day"] = int(day.group(1))
        elif _APP_CHECKIN_DONE.search(line):
            out.update(linked=True, checkin="done")
        elif _APP_CHECKIN_BAD.search(line):
            out.update(linked=True, checkin=out.get("checkin") or "unconfirmed")
        m = _APP_READ.search(line)
        if m:
            done, total = int(m.group(1)), int(m.group(2))
            out.update(linked=True, read=[done, total],
                       read_status="ok" if total and done >= total else "incomplete")
    if not out:
        return None
    out["date"] = today or date.today().isoformat()
    return out


def today_app_summary() -> dict[str, Any] | None:
    """app_from_log() del log de hoy (None si no hay log o no tiene la app)."""
    try:
        text = today_log_path().read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return app_from_log(text)


def app_status(last_run: dict[str, Any], linked: bool | None = None,
               today: str | None = None,
               log_app: dict[str, Any] | None = None) -> dict[str, Any]:
    """
    Estado de la app de Bing (check-in + Leer para ganar) para la GUI.

    Devuelve {"state", "label", "color", "linked", "checkin_ok", "streak_day",
    "read": (hecho, máx) | None, "lines": [...]}. `state` es una clave de
    _APP_STYLES. Lee el resumen que run.py guarda en last_run["app"]; si no es
    de hoy, usa `log_app` (el mismo resumen sacado del log de hoy).
    """
    linked = app_linked() if linked is None else linked
    today = today or date.today().isoformat()
    app = last_run.get("app") if isinstance(last_run.get("app"), dict) else {}
    if app.get("date") != today and isinstance(log_app, dict) \
            and log_app.get("date", today) == today:
        app = log_app
    fresh = app.get("date") == today
    read = app.get("read") if fresh else None
    read = tuple(read) if isinstance(read, (list, tuple)) and len(read) == 2 else None
    checkin_ok = fresh and app.get("checkin") in ("ok", "done")
    read_ok = fresh and (app.get("read_status") in ("ok", "done")
                         or bool(read and read[1] and read[0] >= read[1]))

    if not linked:
        state = "unlinked"
    elif not app_enabled(linked):
        state = "disabled"
    elif fresh and app.get("linked") is False:
        state = "relink"          # el permiso guardado ya no vale
    elif not fresh:
        state = "pending"
    else:
        state = "done" if checkin_ok and read_ok else "partial"

    lines: list[str] = []
    if state == "unlinked":
        lines.append("Vincúlala una vez para ganar +30/día extra")
    elif state == "relink":
        lines.append("El permiso caducó: vuelve a vincularla")
    elif state == "disabled":
        lines.append("Apagada con MSR_APP_ACTIVITIES=0")
    elif state == "pending":
        lines.append("Se hará en la próxima corrida")
    else:
        streak = app.get("streak_day")
        lines.append("Check-in: " + ("✓" if checkin_ok else "✗")
                     + (f" (racha día {streak})" if streak else ""))
        lines.append("Leer para ganar: "
                     + (f"{read[0]}/{read[1]}" if read else "?")
                     + (" ✓" if read_ok else ""))

    label, color = _APP_STYLES[state]
    return {"state": state, "label": label, "color": color, "linked": linked,
            "checkin_ok": checkin_ok, "streak_day": app.get("streak_day") if fresh else None,
            "read": read, "lines": lines}


def completed_today(last_run: dict[str, Any] | None = None) -> bool:
    """True si el bot ya marcó hoy como completado."""
    if last_run is None:
        last_run = read_last_run()
    return last_run.get("last_completed") == date.today().isoformat()


# Códigos de LastTaskResult habituales → texto. Con los reintentos cada 30 min,
# 0x800710E0 aparece a menudo y NO es un error: el Task Scheduler ignoró la
# repetición porque ya había una corrida en marcha (MultipleInstances=IgnoreNew).
_TASK_RESULTS: dict[int, str] = {
    0: "OK",
    0x41301: "en marcha",
    0x41303: "aún no se ha ejecutado",
    0x800710E0: "omitida: ya había una corrida en marcha",
}


def task_result_text(code: Any) -> str:
    """Texto legible para LastTaskResult (o 'código N' si no se conoce)."""
    try:
        n = int(code)
    except (TypeError, ValueError):
        return "código ?"
    return _TASK_RESULTS.get(n & 0xFFFFFFFF, f"código {n}")


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
            gain = session_gain(last_run)
            if gain is not None:
                red = last_run.get("redeemed")
                extra = f", canje de {red}" if type(red) is int and red > 0 else ""
                lines.append(f"Puntos: {pb} → {pa}  ({gain:+d}{extra})")
            elif pb is not None:
                lines.append(f"Puntos: {pb} → {pa}  ({pa - pb:+d})")
            else:
                lines.append(f"Puntos: {pa}")
        if last_run.get("level") is not None:
            lines.append(f"Nivel: {last_run['level']}")
        if last_run.get("searches_done") is not None:
            lines.append(f"Búsquedas: {last_run['searches_done']}")
        if last_run.get("visual_search_done") is not None:
            lines.append(
                "Búsqueda por imagen: "
                + ("sí" if last_run.get("visual_search_done") else "no")
            )
        if last_run.get("daily_done") is not None:
            lines.append(f"Daily cards: {last_run['daily_done']}")
        ver = last_run.get("verification")
        if isinstance(ver, dict):
            if ver.get("ok"):
                lines.append("Verificación: todo acreditado ✓")
            else:
                falta = ", ".join(ver.get("retry") or []) or "?"
                lines.append(f"Verificación: falta {falta}")
        if last_run.get("updated_at"):
            lines.append(f"Actualizado: {last_run['updated_at']}")
    else:
        lines.append("(aún no hay ninguna corrida registrada)")

    app = app_status(last_run)
    lines.append(f"App de Bing: {app['label']}")
    lines.extend(f"  {line}" for line in app["lines"])

    lines.append("")
    if task:
        lines.append("Tarea programada: registrada")
        if task.get("state"):
            lines.append(f"  Estado: {task['state']}")
        if task.get("next_run"):
            lines.append(f"  Próxima: {task['next_run']}")
        if task.get("last_run"):
            lines.append(
                f"  Última: {task['last_run']} ({task_result_text(task.get('last_result'))})"
            )
    else:
        lines.append("Tarea programada: NO registrada")

    return "\n".join(lines)


def today_log_path() -> Path:
    """Ruta del log de hoy (puede no existir todavía)."""
    return LOG_DIR / f"{date.today():%Y%m%d}.log"


# =========================================================================
# Presentación del dashboard (funciones puras para la GUI)
# =========================================================================
# Todo lo que la GUI muestra se calcula aquí, sin Tk: textos en español,
# números formateados, el checklist de "Hoy" y el veredicto del día. Los
# "tonos" (ok/warn/error/info/neutral/gold/silver) son claves semánticas que la
# GUI traduce a colores con sus design tokens (panel/theme.py).
_MONTHS_ES = ("ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic")
_LEVEL_NAMES = {1: "Miembro", 2: "Plata", 3: "Oro"}
_LEVEL_TONES = {1: "neutral", 2: "silver", 3: "gold"}
MAX_LEVEL = 3


def asset_path(name: str) -> Path | None:
    """assets/<name> dentro del .exe (sys._MEIPASS) o en el repo; None si falta."""
    for base in (Path(getattr(sys, "_MEIPASS", ROOT)), ROOT):
        p = base / "assets" / name
        if p.exists():
            return p
    return None


def action_text(action: Action | str) -> str:
    """Etiqueta de una acción sin el emoji/símbolo inicial ('▶  Ejecutar ahora'
    → 'Ejecutar ahora'). La GUI pone sus propios iconos."""
    label = action.label if isinstance(action, Action) else str(action)
    i = 0
    while i < len(label) and not label[i].isalnum():
        i += 1
    return label[i:].strip()


def split_option_label(label: str) -> tuple[str, str]:
    """'Quitar la tarea (deja de ejecutarse solo)' → ('Quitar la tarea',
    'deja de ejecutarse solo'). Sin paréntesis final, la pista va vacía."""
    label = (label or "").strip()
    if label.endswith(")") and " (" in label:
        main, _, hint = label.rpartition(" (")
        return main.strip(), hint[:-1].strip()
    return label, ""


def format_points(value: Any) -> str:
    """29268 → '29.268' (separador de miles español). '—' si no es un número."""
    if isinstance(value, bool):
        return "—"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "—"
    return f"{n:,}".replace(",", ".")


def format_delta(value: Any) -> str:
    """367 → '+367', -12 → '-12', 1234 → '+1.234'. '—' si no es un número."""
    if isinstance(value, bool):
        return "—"
    try:
        n = int(value)
    except (TypeError, ValueError):
        return "—"
    return ("+" if n >= 0 else "-") + format_points(abs(n))


def level_name(level: Any) -> str:
    """3 → 'Oro', 2 → 'Plata', 1 → 'Miembro'. '—' si no se conoce."""
    if level is None or isinstance(level, bool):
        return "—"
    try:
        n = int(level)
    except (TypeError, ValueError):
        return "—"
    return _LEVEL_NAMES.get(n, f"Nivel {n}")


def parse_when(value: Any) -> datetime | None:
    """
    Fecha/hora local (naive) desde 'YYYY-MM-DDTHH:MM:SS+01:00' (last_run) o
    'YYYY-MM-DD HH:MM:SS+00:00' (Task Scheduler vía pywin32, que etiqueta como
    UTC lo que en realidad es hora local). Se ignora la zona en ambos casos.
    None si no se puede leer o es la fecha "nunca" del Programador (1899/1999).
    """
    if isinstance(value, datetime):
        dt = value.replace(tzinfo=None)
    else:
        m = re.match(r"\s*(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?",
                     str(value or ""))
        if not m:
            return None
        try:
            dt = datetime(*(int(g) for g in m.groups(default="0")))
        except ValueError:
            return None
    return dt if dt.year >= 2000 else None


def _short_date(d: date) -> str:
    return f"{d.day} {_MONTHS_ES[d.month - 1]}"


def short_date(value: Any) -> str:
    """'2026-11-12' (o un date) → '12 nov'. '' si no es una fecha."""
    if isinstance(value, date):
        return _short_date(value)
    try:
        return _short_date(date.fromisoformat(str(value)))
    except ValueError:
        return ""


_WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def today_label(d: date | None = None) -> str:
    """Fecha para la cabecera: 'Jueves, 20 nov'."""
    d = d or date.today()
    return f"{_WEEKDAYS_ES[d.weekday()].capitalize()}, {_short_date(d)}"


def format_when(value: Any, now: datetime | None = None) -> str:
    """'hoy, 10:04' / 'ayer, 22:00' / 'mañana, 09:00' / '18 nov, 10:04'."""
    dt = parse_when(value)
    if dt is None:
        return "—"
    now = now or datetime.now()
    days = (dt.date() - now.date()).days
    day = {0: "hoy", -1: "ayer", 1: "mañana"}.get(days, _short_date(dt.date()))
    return f"{day}, {dt:%H:%M}"


def ago_text(value: Any, now: datetime | None = None) -> str:
    """'ahora mismo' / 'hace 5 min' / 'hace 2 h' / 'hace 3 días'. '' si no hay fecha."""
    dt = parse_when(value)
    if dt is None:
        return ""
    now = now or datetime.now()
    secs = (now - dt).total_seconds()
    if secs < 60:
        return "ahora mismo"
    if secs < 3600:
        return f"hace {int(secs // 60)} min"
    if secs < 86400:
        return f"hace {int(secs // 3600)} h"
    n = int(secs // 86400)
    return "hace 1 día" if n == 1 else f"hace {n} días"


def format_elapsed(seconds: float) -> str:
    """Duración de un proceso: 42 → '0:42', 754 → '12:34', 3723 → '1:02:03'."""
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def last_run_date(last_run: dict[str, Any]) -> str | None:
    """Fecha ISO (YYYY-MM-DD) de la última corrida: updated_at o last_completed."""
    dt = parse_when(last_run.get("updated_at"))
    if dt is not None:
        return dt.date().isoformat()
    lc = last_run.get("last_completed")
    return str(lc) if lc else None


def ran_today(last_run: dict[str, Any], today: str | None = None) -> bool:
    """True si la última corrida registrada es de hoy."""
    today = today or date.today().isoformat()
    return bool(last_run) and (last_run_date(last_run) == today
                               or last_run.get("last_completed") == today)


def _verification_fresh(last_run: dict[str, Any], today: str) -> dict[str, Any] | None:
    """La verificación de last_run si es de hoy. Una corrida que abortó por
    sesión caída o error no la reescribe, así que entonces es de otro día."""
    ver = last_run.get("verification")
    if not isinstance(ver, dict) or not ran_today(last_run, today):
        return None
    if str(last_run.get("status", "")).lower() in ("needs_relogin", "error"):
        return None
    return ver


def _pair(value: Any) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return int(value[0]), int(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _check_item(key: str, label: str, pair: tuple[int, int] | None,
                unit: str = "") -> dict[str, Any]:
    if pair is None:
        return {"key": key, "label": label, "state": "unknown", "value": "Sin datos",
                "progress": None}
    done, total = pair
    ok = total <= 0 or done >= total
    suffix = f" {unit}" if unit else ""
    return {"key": key, "label": label, "state": "ok" if ok else "todo",
            "value": f"{done} / {total}{suffix}",
            "progress": 1.0 if total <= 0 else max(0.0, min(1.0, done / total))}


_CHECK_LABELS = (("pc_search", "Búsquedas en PC"), ("daily_set", "Conjunto diario"),
                 ("visual", "Búsqueda por imagen"), ("edge", "Minutos en Edge"))


def today_checklist(last_run: dict[str, Any], *, browser: str | None = None,
                    linked: bool | None = None,
                    today: str | None = None) -> list[dict[str, Any]]:
    """
    Checklist "Hoy" del dashboard a partir de last_run["verification"].

    Devuelve 5 elementos (búsquedas de PC, conjunto diario, búsqueda por imagen,
    minutos de Edge y app de Bing), cada uno {"key", "label", "state", "value",
    "progress"}. state: ok | todo | pending (aún sin corrida hoy) | na (no
    aplica) | unknown (sin datos). progress: 0..1 o None.
    """
    today = today or date.today().isoformat()
    browser = (browser or get_browser()).lower()
    app = app_status(last_run, linked=linked, today=today)

    def _app_item(ver: dict[str, Any] | None) -> dict[str, Any]:
        item: dict[str, Any] = {"key": "app", "label": "App de Bing", "progress": None}
        if app["state"] in ("unlinked", "disabled"):
            return {**item, "state": "na", "value": app["label"]}
        if app["state"] == "relink":
            return {**item, "state": "todo", "value": "Revincular", "progress": 0.0}
        pair = _pair((ver or {}).get("app_streak"))
        if pair is not None:
            return _check_item("app", "App de Bing", pair)
        if app["state"] == "done":
            return {**item, "state": "ok", "value": "Hecho", "progress": 1.0}
        if app["state"] == "partial":
            return {**item, "state": "todo", "value": "Incompleto", "progress": 0.5}
        return {**item, "state": "pending", "value": "Pendiente", "progress": 0.0}

    def _edge_na(item: dict[str, Any]) -> dict[str, Any]:
        if browser != "edge" and item["state"] != "ok":
            return {**item, "state": "na", "value": "Solo con Edge", "progress": None}
        return item

    status = str(last_run.get("status", "")).lower()
    if not ran_today(last_run, today) or status in ("needs_relogin", "error"):
        items = [{"key": k, "label": lbl, "state": "pending", "value": "Pendiente",
                  "progress": 0.0} for k, lbl in _CHECK_LABELS]
        items[3] = _edge_na(items[3])
        app_item = _app_item(None)
        if app_item["state"] == "ok":   # datos de la app de hoy con la corrida caída
            app_item = {**app_item, "state": "pending", "value": "Pendiente",
                        "progress": 0.0}
        return items + [app_item]

    ver = _verification_fresh(last_run, today)
    if ver is None:
        # Corrida de hoy sin verificación (bot antiguo): tirar de los contadores.
        def _legacy(key: str, label: str, done: Any) -> dict[str, Any]:
            if done is None:
                return {"key": key, "label": label, "state": "unknown",
                        "value": "Sin datos", "progress": None}
            ok = bool(done)
            value = ("Hecho" if done is True else "No" if done is False else str(done))
            return {"key": key, "label": label, "state": "ok" if ok else "todo",
                    "value": value, "progress": 1.0 if ok else 0.0}
        items = [
            _legacy("pc_search", "Búsquedas en PC", last_run.get("searches_done")),
            _legacy("daily_set", "Conjunto diario", last_run.get("daily_done")),
            _legacy("visual", "Búsqueda por imagen", last_run.get("visual_search_done")),
            _edge_na({"key": "edge", "label": "Minutos en Edge", "state": "unknown",
                      "value": "Sin datos", "progress": None}),
        ]
        return items + [_app_item(None)]

    return [
        _check_item("pc_search", "Búsquedas en PC", _pair(ver.get("pc_search")), "pts"),
        _check_item("daily_set", "Conjunto diario", _pair(ver.get("daily_set"))),
        _check_item("visual", "Búsqueda por imagen", _pair(ver.get("visual"))),
        _edge_na(_check_item("edge", "Minutos en Edge", _pair(ver.get("edge")), "min")),
        _app_item(ver),
    ]


def checklist_subtitle(last_run: dict[str, Any], today: str | None = None) -> str:
    """Línea bajo el título del checklist: de dónde salen los datos."""
    today = today or date.today().isoformat()
    if not last_run:
        return "Se rellenará tras la primera corrida"
    if not ran_today(last_run, today):
        return "Se rellenará con la corrida de hoy"
    if _verification_fresh(last_run, today) is None:
        status = str(last_run.get("status", "")).lower()
        if status in ("needs_relogin", "error"):
            return "La corrida de hoy no llegó a comprobarlo"
        return "Según los contadores de la última corrida"
    dt = parse_when(last_run.get("updated_at"))
    return f"Comprobado en Rewards a las {dt:%H:%M}" if dt else "Comprobado en Rewards"


def checklist_counts(items: list[dict[str, Any]]) -> tuple[int, int]:
    """(hechos, aplicables) del checklist; los 'na' no cuentan."""
    applicable = [i for i in items if i.get("state") != "na"]
    return sum(1 for i in applicable if i.get("state") == "ok"), len(applicable)


def checklist_notes(last_run: dict[str, Any],
                    today: str | None = None) -> list[tuple[str, str]]:
    """Avisos bajo el checklist: [(tono, texto)] — reintentos y sellos pendientes."""
    today = today or date.today().isoformat()
    ver = _verification_fresh(last_run, today)
    if ver is None:
        return []
    notes: list[tuple[str, str]] = []
    retry = [str(r) for r in (ver.get("retry") or [])]
    if retry:
        notes.append(("warn", "Se reintentará: " + ", ".join(retry)))
    try:
        punch = int(ver.get("pending_punch") or 0)
    except (TypeError, ValueError):
        punch = 0
    if punch:
        # Son las promociones de varios pasos de Rewards (Spotify, Duolingo…),
        # no los sellos de las rachas: opcionales y a mano.
        noun = ("promoción con tareas sin hacer" if punch == 1
                else "promociones con tareas sin hacer")
        notes.append(("info", f"{punch} {noun} (Spotify, Duolingo…): opcionales, "
                              "a mano en Rewards"))
    return notes


def day_summary(last_run: dict[str, Any], task: dict[str, Any] | None = None, *,
                venv_ok: bool = True, today: str | None = None,
                now: datetime | None = None) -> dict[str, str]:
    """
    Veredicto del día para la cabecera del dashboard: ¿está todo hecho hoy?

    Devuelve {"tone", "icon", "title", "subtitle"}; tone ∈ ok|warn|error|info e
    icon ∈ check|warning|error|clock|info.
    """
    today = today or date.today().isoformat()
    status = str(last_run.get("status", "")).lower()
    next_run = format_when(task.get("next_run"), now) if task else "—"

    def _out(tone: str, icon: str, title: str, subtitle: str) -> dict[str, str]:
        return {"tone": tone, "icon": icon, "title": title, "subtitle": subtitle}

    if not venv_ok:
        return _out("error", "error", "Falta instalar el bot",
                    "Ejecuta setup.exe primero. Hasta entonces, las acciones están "
                    "desactivadas.")
    if not last_run:
        return _out("info", "info", "Todavía no se ha ejecutado",
                    "Pulsa «Ejecutar ahora» para la primera corrida"
                    + ("." if task else " o registra la tarea para que vaya solo."))
    if status == "needs_relogin":
        return _out("error", "error", "Tienes que iniciar sesión",
                    "La sesión de Microsoft ha caducado. Usa «Login manual» o "
                    "«Cambiar cuenta».")
    if status == "error":
        return _out("error", "error", "La última corrida falló",
                    "Abre el registro para ver qué pasó y vuelve a ejecutarlo.")
    if not ran_today(last_run, today):
        if task and next_run != "—":
            sub = f"Próxima corrida automática: {next_run}."
        else:
            sub = "Pulsa «Ejecutar ahora» o registra la tarea programada."
        return _out("info", "clock", "Hoy aún no se ha ejecutado", sub)

    ver = _verification_fresh(last_run, today) or {}
    missing = ", ".join(str(r) for r in (ver.get("retry") or []))
    completed = last_run.get("last_completed") == today
    if status == "incomplete" or missing:
        what = f"Falta: {missing}. " if missing else ""
        if completed:
            return _out("warn", "warning", "Hoy ha quedado algo pendiente",
                        what + "Puedes volver a pulsar «Ejecutar ahora».")
        retry = (f"Se reintentará solo ({next_run})." if task and next_run != "—"
                 else "Pulsa «Ejecutar ahora» para terminarlo.")
        return _out("warn", "warning", "Faltan cosas por hacer hoy", what + retry)
    if status == "empty":
        return _out("warn", "warning", "La última corrida no hizo nada",
                    "Puede que ya estuviera todo hecho o que Rewards no respondiera.")
    if status == "selectors_broken":
        return _out("warn", "warning", "Hecho, pero con avisos",
                    "Rewards ha cambiado algo en su web y puede faltar alguna "
                    "actividad.")
    if completed:
        auto = ("Mañana se ejecutará solo." if task else
                "Registra la tarea programada para que vaya solo cada día.")
        return _out("ok", "check", "Todo hecho por hoy",
                    "Todo acreditado en Rewards. " + auto)
    return _out("info", "clock", "La corrida de hoy no ha terminado",
                "Pulsa «Ejecutar ahora» si no hay ninguna en marcha.")


def session_gain(last_run: dict[str, Any]) -> int | None:
    """Puntos ganados en la última sesión: saldo final − inicial + lo canjeado
    a mitad de sesión (`redeemed`, lo apunta el bot). None si falta algún saldo."""
    pa, pb = (last_run or {}).get("points_after"), (last_run or {}).get("points_before")
    if type(pa) is not int or type(pb) is not int:
        return None
    red = (last_run or {}).get("redeemed")
    return pa - pb + (red if type(red) is int and red > 0 else 0)


def kpi_tiles(last_run: dict[str, Any], today: str | None = None,
              now: datetime | None = None) -> list[dict[str, str]]:
    """
    Tarjetas KPI: puntos, ganados hoy, nivel y última corrida.
    Cada una {"key", "label", "value", "sub", "tone"}.
    """
    today = today or date.today().isoformat()
    now = now or datetime.now()
    has = bool(last_run)
    pa, pb = last_run.get("points_after"), last_run.get("points_before")

    points = {"key": "points", "label": "Puntos", "tone": "neutral",
              "value": format_points(pa),
              "sub": "disponibles" if pa is not None else "Sin datos todavía"}

    gained = {"key": "gained", "label": "Ganados hoy", "tone": "neutral", "value": "—",
              "sub": "Aún sin corrida hoy" if has else "Sin datos todavía"}
    delta = session_gain(last_run)
    if ran_today(last_run, today) and delta is not None:
        gained.update(value=format_delta(delta), tone="ok" if delta > 0 else "neutral",
                      sub=f"{format_points(pb)} → {format_points(pa)}")

    lvl = last_run.get("level")
    known = isinstance(lvl, int) and not isinstance(lvl, bool)
    level = {"key": "level", "label": "Nivel", "value": level_name(lvl),
             "tone": _LEVEL_TONES.get(lvl, "neutral") if known else "neutral",
             "sub": f"Nivel {lvl} de {MAX_LEVEL}" if known else "Sin datos todavía"}

    when = parse_when(last_run.get("updated_at")) if has else None
    if when is None:
        last = {"key": "last", "label": "Última corrida", "value": "—",
                "sub": "Sin fecha" if has else "Nunca", "tone": "neutral"}
    else:
        days = (now.date() - when.date()).days
        ago = ago_text(when, now)
        if days == 0:
            value, sub = f"{when:%H:%M}", f"hoy · {ago}"
        elif days == 1:
            value, sub = "Ayer", f"{when:%H:%M} · {ago}"
        else:
            value, sub = _short_date(when.date()), f"{when:%H:%M} · {ago}"
        last = {"key": "last", "label": "Última corrida", "value": value,
                "sub": sub, "tone": "neutral"}
    return [points, gained, level, last]


def task_summary(task: dict[str, Any] | None,
                 now: datetime | None = None) -> dict[str, Any]:
    """
    Estado de la tarea programada para su tarjeta.
    {"state": missing|ready|running|disabled, "label", "tone", "lines": [...]}.
    """
    if not task:
        return {"state": "missing", "label": "No registrada", "tone": "warn",
                "lines": ["Regístrala para que el bot se ejecute solo cada día."]}
    raw = str(task.get("state", "")).lower()
    if raw == "running":
        state, label, tone = "running", "En marcha", "info"
    elif raw == "disabled":
        state, label, tone = "disabled", "Desactivada", "warn"
    else:
        state, label, tone = "ready", "Activa", "ok"
    lines: list[str] = []
    nxt = format_when(task.get("next_run"), now)
    if nxt != "—":
        lines.append(f"Próxima: {nxt}")
    if "last_run" in task:
        last = format_when(task.get("last_run"), now)
        if last == "—":
            lines.append("Última: nunca")
        else:
            lines.append(f"Última: {last} · {task_result_text(task.get('last_result'))}")
    if state == "disabled":
        lines.append("Actívala en el Programador de tareas o vuelve a registrarla.")
    return {"state": state, "label": label, "tone": tone, "lines": lines}


def app_rows(st: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Filas de la tarjeta "App de Bing" a partir de app_status(): check-in y
    "Leer para ganar". Cada una {"key", "label", "value", "state", "progress"}
    con state ok | todo | pending.
    """
    if st.get("state") not in ("done", "partial"):
        return [{"key": "checkin", "label": "Check-in diario", "value": "Pendiente",
                 "state": "pending", "progress": None},
                {"key": "read", "label": "Leer para ganar", "value": "Pendiente",
                 "state": "pending", "progress": 0.0}]
    streak = st.get("streak_day")
    if st.get("checkin_ok"):
        checkin = {"value": f"Racha: día {streak}" if streak else "Hecho", "state": "ok"}
    else:
        checkin = {"value": "Sin hacer", "state": "todo"}
    read = st.get("read")
    if read:
        done, total = read
        ok = (not total) or done >= total
        rd = {"value": f"{done} / {total}", "state": "ok" if ok else "todo",
              "progress": 1.0 if not total else max(0.0, min(1.0, done / total))}
    else:
        rd = {"value": "Sin datos", "state": "todo", "progress": 0.0}
    return [{"key": "checkin", "label": "Check-in diario", "progress": None, **checkin},
            {"key": "read", "label": "Leer para ganar", **rd}]


_LOG_PREFIX = re.compile(
    r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[,.]\d+)?\s+"   # fecha y hora
    r"(?:\[[^\]]*\]\s+|[\w.]+\s+(?=[A-Z]{3,}\b))?"                 # [logger] o logger
    r"(?:(?:DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL):?\s+)?")       # nivel


def clean_log_line(line: str) -> str:
    """Quita fecha/logger/nivel del principio de una línea de log para el
    resumen de la barra de actividad: '2026-11-20 10:04:30,1 [run] INFO: hola'
    → 'hola'."""
    text = (line or "").strip()
    return _LOG_PREFIX.sub("", text, count=1).strip() or text


def log_line_kind(line: str) -> str:
    """Tipo de línea del log para colorearla: cmd | header | error | warn | ok | normal."""
    text = (line or "").strip()
    if text.startswith("$ "):
        return "cmd"
    if text.startswith("---"):
        return "header"
    up = text.upper()
    if any(w in up for w in ("ERROR", "TRACEBACK", "EXCEPTION", "CRITICAL")):
        return "error"
    if "WARN" in up or "AVISO" in up:
        return "warn"
    if "✓" in text:
        return "ok"
    return "normal"


# =========================================================================
# Historial de puntos (a partir de los logs diarios)
# =========================================================================
# El bot escribe el saldo en ms_rewards/logs/YYYYMMDD.log cada vez que lo lee:
#   12:07:36 [run] INFO: puntos: 27364 → 27589 (ganados hoy en esta sesión: +225)
#   11:06:37 [rewards_info] INFO: nivel=3 (Nivel Oro) puntos=27364 [API]
#   19:05:22 [run] INFO: estado cuenta: nivel=3 puntos=28770 fuente=api
#
# CÓMO SE CALCULA LO GANADO EN UN DÍA
# Se ponen en fila todas las lecturas de saldo (de todos los días, en orden) y
# se mira cada salto entre una lectura y la siguiente:
#   · si el saldo sube, la subida se apunta al día de la lectura posterior;
#   · si baja, es un canje: no resta de lo ganado (se anota en "redeemed");
#   · una línea "canje en Rewards: N pts" (el bot la escribe justo antes del
#     saldo cuando el último pedido de la cuenta es de después de la lectura
#     anterior) apunta N como canje y lo descuenta del saldo previo: así, un
#     canje hecho a mano a mitad de corrida no se come lo ganado ese día;
#   · una subida imposible (> MAX_PLAUSIBLE_GAIN) es un cambio de cuenta: se
#     ignora.
# Es decir, "diferencia entre lecturas consecutivas", no "último − primero del
# día": así también cuentan los puntos que llegan entre corridas (app del
# móvil, Xbox, lotes pendientes que se acreditan de madrugada). La pega,
# asumida: tras varios días sin log, lo acumulado en ese hueco se apunta al
# primer día que vuelve a haber lectura. Un día sin log no tiene dato (None),
# que no es lo mismo que haber ganado 0.
HISTORY_DAYS = 31             # ventana del gráfico: cubre siempre el mes en curso
MAX_PLAUSIBLE_GAIN = 5000     # un salto mayor entre dos lecturas = otra cuenta
BONUS_MIN = 500               # mínimo para considerar un día "con bonus"
_MONTHS_FULL_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
                   "agosto", "septiembre", "octubre", "noviembre", "diciembre")
# De "puntos: A → B" solo interesa B: A ya salió antes en su propia lectura y
# repetirlo detrás de B parecería un canje seguido de otra subida.
_LOG_BALANCE = re.compile(
    r"\bpuntos: \d+ → (\d+)|\bpuntos=(\d+)|\bcanje en Rewards: (\d+) pts")
_log_cache: dict[str, tuple[tuple[float, int], list[int]]] = {}


def parse_log_balances(text: str) -> list[int]:
    """Saldos que aparecen en el texto de un log diario, en orden. Las lecturas
    a 0 (fallo de la API) se descartan. Un canje apuntado por el bot va como
    número NEGATIVO (−puntos canjeados) en su sitio de la secuencia."""
    out: list[int] = []
    for m in _LOG_BALANCE.finditer(text or ""):
        if m.group(3):
            if int(m.group(3)) > 0:
                out.append(-int(m.group(3)))
            continue
        value = int(m.group(1) or m.group(2))
        if value > 0:
            out.append(value)
    return out


def read_log_balances(days: int = HISTORY_DAYS, today: date | None = None,
                      log_dir: Path | None = None) -> dict[str, list[int]]:
    """
    (E/S) Lee los logs de los últimos `days` días —más el último log anterior,
    que da el saldo de partida— y devuelve {"YYYY-MM-DD": [saldos…]}. Los días
    sin log (o sin saldos) no aparecen. Cachea por fecha de modificación y
    tamaño del archivo.
    """
    today = today or date.today()
    log_dir = Path(log_dir) if log_dir is not None else LOG_DIR
    first = today - timedelta(days=days - 1)
    try:
        names = sorted(p.name for p in log_dir.glob("????????.log"))
    except OSError:
        return {}
    found: list[tuple[date, str]] = []
    for name in names:
        try:
            d = datetime.strptime(name[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if d <= today:
            found.append((d, name))
    out: dict[str, list[int]] = {}
    # Del más reciente al más antiguo: toda la ventana y, después, solo hasta
    # dar con el primer log anterior que tenga algún saldo.
    for d, name in reversed(found):
        path = log_dir / name
        try:
            st = path.stat()
            stamp = (st.st_mtime, st.st_size)
            cached = _log_cache.get(str(path))
            if cached is None or cached[0] != stamp:
                cached = (stamp, parse_log_balances(
                    path.read_text(encoding="utf-8", errors="replace")))
                _log_cache[str(path)] = cached
        except OSError:
            continue
        if cached[1]:
            out[d.isoformat()] = list(cached[1])
            if d < first:
                break
    return dict(sorted(out.items()))


def points_history(balances: dict[str, list[int]], today: date | None = None,
                   days: int = HISTORY_DAYS) -> list[dict[str, Any]]:
    """
    Historial diario (función pura) de los últimos `days` días, del más antiguo
    a hoy. Cada día: {"date", "gained": int | None (None = sin log ese día),
    "balance": último saldo | None, "redeemed": puntos canjeados, "bonus": bool}.
    En `balances`, un número negativo es un canje apuntado (ver
    parse_log_balances), no un saldo. Ver arriba cómo se reparte lo ganado. `bonus` marca los días muy por encima
    de lo normal: al menos BONUS_MIN y el doble de la mediana de los días con
    puntos.
    """
    today = today or date.today()
    first = (today - timedelta(days=days - 1)).isoformat()
    last = today.isoformat()
    gained: dict[str, int] = {}
    redeemed: dict[str, int] = {}
    closing: dict[str, int] = {}
    prev: int | None = None
    for day in sorted(k for k in balances if k <= last):
        for value in balances[day]:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            if value < 0:                       # canje apuntado por el bot
                redeemed[day] = redeemed.get(day, 0) - value
                if prev is not None:
                    prev += value
                continue
            if value == 0:
                continue
            gained.setdefault(day, 0)
            if prev is not None:
                step = value - prev
                if 0 < step <= MAX_PLAUSIBLE_GAIN:
                    gained[day] += step
                elif step < 0:
                    redeemed[day] = redeemed.get(day, 0) - step
            prev = value
            closing[day] = value
    positives = sorted(v for k, v in gained.items() if v > 0 and k >= first)
    median = positives[len(positives) // 2] if positives else 0
    out: list[dict[str, Any]] = []
    for i in range(days):
        key = (today - timedelta(days=days - 1 - i)).isoformat()
        g = gained.get(key)
        out.append({"date": key, "gained": g, "balance": closing.get(key),
                    "redeemed": redeemed.get(key, 0),
                    "bonus": bool(g and g >= BONUS_MIN and g >= 2 * median)})
    return out


def history_summary(history: list[dict[str, Any]],
                    today: date | None = None) -> dict[str, Any]:
    """
    Cifras del historial para su tarjeta (función pura):
      month_label  'noviembre'
      month_total  puntos ganados en el mes en curso
      pace         ritmo mensual: media diaria × 30, contando desde el primer
                   día con datos de la ventana hasta hoy (los días sin corrida
                   bajan el ritmo; los anteriores a instalar el bot, no)
      average      media por día con datos (0 si no hay)
      best         {"date", "gained"} del mejor día, o None
      streak       días seguidos con puntos hasta hoy (o hasta ayer si hoy aún
                   no hay corrida)
      days         días con datos · has_data: si hay algo que enseñar
    """
    today = today or date.today()
    month_prefix = today.isoformat()[:7]
    with_data = [h for h in history if h.get("gained") is not None]
    total = sum(h["gained"] for h in with_data)
    month_total = sum(h["gained"] for h in with_data
                      if str(h["date"]).startswith(month_prefix))
    pace = 0
    if with_data:
        try:
            span = (today - date.fromisoformat(with_data[0]["date"])).days + 1
        except ValueError:
            span = len(history)
        pace = round(total / max(1, span) * 30)
    best = max(with_data, key=lambda h: h["gained"], default=None)
    if best is not None and best["gained"] <= 0:
        best = None
    streak = 0
    pending = list(reversed(history))
    if pending and not pending[0].get("gained") and pending[0]["date"] == today.isoformat():
        pending = pending[1:]             # hoy todavía sin puntos: no rompe la racha
    for h in pending:
        if not h.get("gained"):
            break
        streak += 1
    return {"month_label": _MONTHS_FULL_ES[today.month - 1], "month_total": month_total,
            "pace": pace, "average": round(total / len(with_data)) if with_data else 0,
            "best": ({"date": best["date"], "gained": best["gained"]} if best else None),
            "streak": streak, "days": len(with_data), "has_data": bool(with_data)}


def history_day_text(entry: dict[str, Any] | None, today: date | None = None) -> str:
    """Texto de un día del historial: 'Hoy · +367 pts', '12 nov · +1.240 pts ·
    bonus', '3 nov · sin corrida', 'Ayer · +210 pts · canje de 5.000'."""
    if not entry:
        return ""
    today = today or date.today()
    try:
        d = date.fromisoformat(str(entry.get("date")))
    except ValueError:
        return ""
    name = {0: "Hoy", 1: "Ayer"}.get((today - d).days, _short_date(d))
    g = entry.get("gained")
    if g is None:
        return f"{name} · sin corrida"
    text = f"{name} · {format_delta(g)} pts"
    if entry.get("bonus"):
        text += " · bonus"
    if entry.get("redeemed"):
        text += f" · canje de {format_points(entry['redeemed'])}"
    return text


# =========================================================================
# Fila de detalle del dashboard: actividades, app de Bing y tarea programada
# =========================================================================
# Cada pieza lleva su cifra (hecho / total / unidad, para pintarla con
# jerarquía) y su estado en palabras ("Hecho", "Faltan 36 pts"). Todo son
# funciones puras sobre lo que ya devuelven today_checklist(), app_status() y
# la consulta de la tarea.
_ACTIVITY_NAMES = {"pc_search": "Búsquedas", "daily_set": "Conjunto diario",
                   "visual": "Búsqueda visual", "edge": "Minutos en Edge",
                   "app": "App de Bing"}
_UNIT_SINGULAR = {"pts": "pt"}
_VALUE_PAIR = re.compile(r"^\s*(\d+)\s*/\s*(\d+)(?:\s+(\S+))?\s*$")
STREAK_CYCLE = 7              # la racha de la app se premia por ciclos de 7 días


def activity_status_text(item: dict[str, Any]) -> str:
    """Estado en palabras de un elemento de today_checklist(): 'Hecho',
    'Faltan 36 pts', 'Falta 1 de 3', 'Sin hacer', 'Pendiente', 'Solo con Edge'…"""
    state = item.get("state")
    value = str(item.get("value") or "")
    if state == "ok":
        return "Hecho"
    if state == "pending":
        return "Pendiente"
    if state == "na":
        return value or "No aplica"
    if state != "todo":
        return "Sin datos"
    m = _VALUE_PAIR.match(value)
    if not m:
        return value if value and not value.isdigit() and value != "No" else "Sin hacer"
    done, total, unit = int(m.group(1)), int(m.group(2)), m.group(3)
    left = max(0, total - done)
    if total <= 1 or left == 0:
        return "Sin hacer"
    verb = "Falta" if left == 1 else "Faltan"
    if unit:
        return f"{verb} {left} {_UNIT_SINGULAR.get(unit, unit) if left == 1 else unit}"
    return f"{verb} {left} de {total}"


def activity_tiles(items: list[dict[str, Any]],
                   streaks: dict[str, tuple[int, int]] | None = None
                   ) -> list[dict[str, Any]]:
    """
    `streaks` (de activity_streaks()) añade a cada ficha "streak": (día,
    total) | None y "streak_text".

    Fichas de "Actividades de hoy" a partir de today_checklist(): una por
    actividad web (la app de Bing tiene su propia tarjeta y no se repite).
    Cada una {"key", "label", "state", "status", "progress", "done", "total",
    "unit"}; done/total son None si la actividad aún no tiene cifra.
    """
    out = []
    for i in items:
        if i.get("key") == "app":
            continue
        m = _VALUE_PAIR.match(str(i.get("value") or ""))
        out.append({"key": i.get("key"),
                    "label": _ACTIVITY_NAMES.get(i.get("key"), i.get("label", "")),
                    "state": i.get("state", "unknown"),
                    "status": activity_status_text(i), "progress": i.get("progress"),
                    "done": int(m.group(1)) if m else None,
                    "total": int(m.group(2)) if m else None,
                    "unit": (m.group(3) or "") if m else ""})
    for tile in out:
        pair = (streaks or {}).get(tile["key"])
        tile["streak"] = pair
        tile["streak_text"] = streak_text(pair)
    return out


def activities_verdict(tiles: list[dict[str, Any]]) -> tuple[str, str]:
    """(texto, tono) que resume las fichas: ('Todo hecho', 'ok'), ('Faltan 2',
    'warn'), ('Pendiente', 'neutral')."""
    applicable = [t for t in tiles if t.get("state") != "na"]
    todo = sum(1 for t in applicable if t.get("state") == "todo")
    if todo:
        return ("Falta 1" if todo == 1 else f"Faltan {todo}"), "warn"
    if applicable and all(t.get("state") == "ok" for t in applicable):
        return "Todo hecho", "ok"
    return "Pendiente", "neutral"


def app_headline(st: dict[str, Any]) -> str:
    """Una frase para la tarjeta de la app de Bing a partir de app_status()."""
    state = st.get("state")
    if state == "done":
        return "Check-in y lectura del día, hechos"
    if state == "partial":
        missing = []
        for row in app_rows(st):
            if row["state"] == "ok":
                continue
            if row["key"] == "checkin":
                missing.append("el check-in")
            else:
                read = st.get("read")
                missing.append(f"leer para ganar ({read[0]} de {read[1]})" if read
                               else "leer para ganar")
        return "Falta " + " y ".join(missing) if missing else "Incompleto"
    return (st.get("lines") or [""])[0]


def streak_cycle(streak_day: Any) -> dict[str, Any] | None:
    """Racha de la app como ciclo de STREAK_CYCLE días: {"day", "position"
    (1..7 dentro del ciclo), "text"}. None si no se conoce la racha."""
    if isinstance(streak_day, bool):
        return None
    try:
        day = int(streak_day)
    except (TypeError, ValueError):
        return None
    if day < 1:
        return None
    return {"day": day, "position": (day - 1) % STREAK_CYCLE + 1,
            "text": f"Racha · día {day}"}


def task_headline(task: dict[str, Any] | None,
                  now: datetime | None = None) -> dict[str, str]:
    """
    Tarea programada para su tarjeta: {"state", "label", "tone"} (los de
    task_summary()), una frase ("headline"), una aclaración ("detail", solo si
    hace falta) y los datos "next" ('hoy, 14:02'), "last" ('hoy, 13:35') y
    "result" ('OK'); '' cuando no se conocen.
    """
    ts = task_summary(task, now)
    out = {"state": ts["state"], "label": ts["label"], "tone": ts["tone"],
           "detail": "", "next": "", "last": "", "result": ""}
    if ts["state"] == "missing":
        return {**out, "headline": "El bot no se ejecuta solo",
                "detail": "Regístrala y lo hará cada día, sin abrir el panel."}
    nxt = format_when(task.get("next_run"), now)
    out["next"] = "" if nxt == "—" else nxt
    if "last_run" in task:
        last = format_when(task.get("last_run"), now)
        if last == "—":
            out["last"] = "nunca"
        else:
            out["last"] = last
            out["result"] = task_result_text(task.get("last_result"))
    if ts["state"] == "disabled":
        return {**out, "headline": "Desactivada en el Programador de tareas",
                "detail": "Vuelve a registrarla para reactivarla."}
    if ts["state"] == "running":
        return {**out, "headline": "Ejecutándose ahora mismo"}
    return {**out, "headline": "Se ejecuta sola cada día"}


# --- Rachas de 7 días y sellos ----------------------------------------------
# verify.py guarda en last_run["verification"]:
#   "streak_days": {"search": [1, 7], "daily_set": [3, 7], "edge": [3, 7],
#                   "app": [1, 7], "visual": [5, 7]}   día de cada racha
#   "stamps": [8, 12]                                  sellos que lleva / necesarios
# Cada racha completada (7/7) da un sello; con todos los sellos se cobran
# STAMP_BONUS puntos. Las corridas antiguas no traen estos datos: todo aquí
# tolera que falten (o vengan rotos) y lo dice con "known"/None.
STAMP_BONUS = 1000
_STREAK_KEYS = {"search": "pc_search", "daily_set": "daily_set", "visual": "visual",
                "edge": "edge", "app": "app"}


def activity_streaks(last_run: dict[str, Any]) -> dict[str, tuple[int, int]]:
    """{clave de ficha: (día, total)} con la racha de cada actividad
    (pc_search, daily_set, visual, edge, app). Vacío si el bot no la guardó."""
    ver = last_run.get("verification") if isinstance(last_run, dict) else None
    raw = ver.get("streak_days") if isinstance(ver, dict) else None
    out: dict[str, tuple[int, int]] = {}
    if not isinstance(raw, dict):
        return out
    for name, key in _STREAK_KEYS.items():
        pair = _pair(raw.get(name))
        if pair is not None and pair[1] > 0:
            out[key] = (max(0, min(pair[0], pair[1])), pair[1])
    return out


def streak_text(pair: tuple[int, int] | None) -> str:
    """(3, 7) → 'Racha · día 3 de 7'; (0, 7) → 'Racha sin empezar'; (7, 7) →
    'Racha completa · 7 de 7'; None → ''."""
    if not pair:
        return ""
    day, total = pair
    if day <= 0:
        return "Racha sin empezar"
    if day >= total:
        return f"Racha completa · {total} de {total}"
    return f"Racha · día {day} de {total}"


# El bot guarda sellos y día de racha (en last_run y en el log) desde esta
# fecha; de antes no existe el dato y Microsoft no da histórico.
TRACKING_SINCE = date(2026, 11, 20)


def no_data_text(what: str, past: bool = False) -> str:
    """Texto de "no hay dato" (que NO es lo mismo que cero) para sellos o
    rachas: 'Sellos: sin dato ese día · se guardan desde el 20 nov' al mirar
    un día pasado; 'Sellos: sin dato todavía · se sabrá tras la próxima
    corrida' si es hoy."""
    if past:
        return (f"{what}: sin dato ese día · se guardan desde el "
                f"{_short_date(TRACKING_SINCE)}")
    return f"{what}: sin dato todavía · se sabrá tras la próxima corrida"


def stamps_summary(last_run: dict[str, Any], past: bool = False) -> dict[str, Any]:
    """
    Sellos para su franja: {"known", "done", "total", "left", "text"}.
    P. ej. 8 de 12 → '+1.000 pts al completar · faltan 4'. Sin datos (corrida
    antigua): known False, done/total None y un texto que lo explica.
    """
    ver = last_run.get("verification") if isinstance(last_run, dict) else None
    pair = _pair(ver.get("stamps")) if isinstance(ver, dict) else None
    if pair is None or pair[1] <= 0:
        return {"known": False, "done": None, "total": None, "left": None,
                "text": no_data_text("Sellos", past)}
    total = pair[1]
    done = max(0, min(pair[0], total))
    left = total - done
    bonus = f"+{format_points(STAMP_BONUS)} pts"
    if left == 0:
        text = f"Completo · {bonus} por cobrar"
    else:
        text = f"{bonus} al completar · " + ("falta 1" if left == 1 else f"faltan {left}")
    return {"known": True, "done": done, "total": total, "left": left, "text": text}


# =========================================================================
# Objetivo de compra: un precio en € → puntos que cuesta y cuándo se llega
# =========================================================================
# Se paga con tarjetas regalo de Xbox/Microsoft canjeadas en Rewards (catálogo
# de España, precios a 20/11/2026). Hay dos tipos:
#   · Fijas: 2 € = 2.675 pts, 5 € = 5.660 pts, 10 € = 11.320 pts.
#   · «Personalizado»: UNA tarjeta de un importe entero entre 5 y 100 €, más
#     barata por euro. Precios medidos en la cuenta real ese día:
#         15 € = 15.440 · 25 € = 25.735 · 69 € = 71.020 · 100 € = 102.930
#     Los cuatro (y 5 € = 5.145, 10 € = 10.295) encajan con 1.029,3 pts/€
#     redondeado al múltiplo de 5 más cercano (el medio, hacia arriba).
# Si Microsoft cambia los precios, basta con tocar estas constantes.
GIFT_CARDS: tuple[tuple[int, int], ...] = ((2, 2675), (5, 5660), (10, 11320))
CUSTOM_CARD_MIN = 5
CUSTOM_CARD_MAX = 100
CUSTOM_RATE = 1029.3          # puntos por euro de la tarjeta personalizada
# Una tarjeta más solo compensa si ahorra al menos esto. Sin este umbral, el
# redondeo a múltiplos de 5 haría "óptimo" trocear 150 € en 30 tarjetas de 5 €
# para ahorrar 45 puntos: cierto sobre el papel, absurdo al canjear.
CARD_MIN_SAVING = 50
CUSTOM_CARD_HINT = (f"Tarjeta regalo de Xbox · opción Personalizado "
                    f"({CUSTOM_CARD_MIN}–{CUSTOM_CARD_MAX} €)")


def custom_points(eur: int) -> int | None:
    """Puntos de una tarjeta personalizada de `eur` euros (entero entre
    CUSTOM_CARD_MIN y CUSTOM_CARD_MAX): 5 · ⌊eur · 1.029,3 / 5 + 0,5⌋. Se
    calcula con enteros para que el redondeo no dependa de los decimales del
    float. None si el importe no se puede pedir."""
    if isinstance(eur, bool) or not isinstance(eur, int) \
            or not CUSTOM_CARD_MIN <= eur <= CUSTOM_CARD_MAX:
        return None
    tenths = round(CUSTOM_RATE * 10)                 # 10.293 décimas de punto por €
    return 5 * ((eur * tenths + 25) // 50)


GOAL_PATH = STATE_DIR / "goal.json"
MAX_GOAL_EUR = 5000
GIFT_CARD_NOTE = ("El saldo de la tarjeta caduca 90 días después de canjearla: "
                  "canjea justo antes de comprar.")


def parse_price(text: Any) -> float | None:
    """'56', '56,99', '56.99', ' 56,99 € ', '1.299,99' → euros (2 decimales).
    None si no es un número mayor que 0 (y razonable: hasta MAX_GOAL_EUR)."""
    if isinstance(text, bool):
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        raw = str(text or "").replace("€", "").replace(" ", "").replace("\u00a0", "")
        if "," in raw and "." in raw:
            raw = raw.replace(".", "")          # 1.299,99: el punto es de millares
        raw = raw.replace(",", ".")
        if not re.fullmatch(r"\d+(\.\d*)?|\.\d+", raw):
            return None
        value = float(raw)
    if value != value or not 0 < value <= MAX_GOAL_EUR:
        return None
    return round(value, 2)


def format_eur(value: Any) -> str:
    """56.0 → '56 €', 56.99 → '56,99 €', 1299.5 → '1.299,50 €'."""
    try:
        v = round(float(value), 2)
    except (TypeError, ValueError):
        return "—"
    whole = format_points(int(v))
    cents = round((v - int(v)) * 100)
    return f"{whole} €" if cents == 0 else f"{whole},{cents:02d} €"


def read_goal(path: Path | None = None) -> dict[str, Any] | None:
    """(E/S) Objetivo guardado: {"name", "price_eur", "set_at"} o None si no
    hay, no se puede leer o no es válido."""
    try:
        data = json.loads(Path(path or GOAL_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    price = parse_price(data.get("price_eur"))
    if price is None:
        return None
    return {"name": str(data.get("name") or "").strip(), "price_eur": price,
            "set_at": str(data.get("set_at") or "")}


def save_goal(name: Any, price_eur: Any, path: Path | None = None,
              now: datetime | None = None) -> dict[str, Any] | None:
    """(E/S) Guarda el objetivo y lo devuelve; None si el precio no vale o no
    se pudo escribir."""
    price = parse_price(price_eur)
    if price is None:
        return None
    goal = {"name": str(name or "").strip()[:60], "price_eur": price,
            "set_at": (now or datetime.now()).isoformat(timespec="seconds")}
    target = Path(path or GOAL_PATH)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(goal, ensure_ascii=False, indent=2),
                          encoding="utf-8")
    except OSError:
        return None
    return goal


def clear_goal(path: Path | None = None) -> bool:
    """(E/S) Quita el objetivo. True si ya no existe."""
    target = Path(path or GOAL_PATH)
    try:
        target.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def _cards_text(cards: list[tuple[str, int]]) -> str:
    """Desglose legible: '1 tarjeta personalizada de 69 €', 'Personalizadas:
    100 € + 50 €', '1 × 2 €' o la mezcla de ambas."""
    custom = [v for kind, v in cards if kind == "custom"]
    fixed: dict[int, int] = {}
    for kind, v in cards:
        if kind == "fixed":
            fixed[v] = fixed.get(v, 0) + 1
    parts = []
    if len(custom) == 1:
        parts.append(f"1 tarjeta personalizada de {custom[0]} €")
    elif custom:
        parts.append("Personalizadas: " + " + ".join(f"{v} €" for v in custom))
    if fixed:
        text = " + ".join(f"{n} × {v} €" for v, n in sorted(fixed.items(), reverse=True))
        parts.append(("fijas: " if custom else "") + text)
    return " · ".join(parts)


def points_for_price(eur: Any) -> dict[str, Any] | None:
    """
    Puntos que cuesta cubrir `eur` euros con tarjetas regalo: la combinación
    de MENOS puntos cuya suma en € llegue al precio (redondeado hacia arriba a
    euros enteros), usando tarjetas personalizadas (5–100 € cada una; varias
    si hace falta más de 100 €) y las fijas de 2/5/10 €. Programación dinámica
    sobre euros enteros. Cada tarjeta de más tiene que ahorrar al menos
    CARD_MIN_SAVING puntos (los céntimos de redondeo no justifican trocear el
    canje); a igualdad, menos tarjetas y, después, la que menos se pasa.

    Devuelve {"points", "covered_eur", "cards": [(tipo, €)…] con tipo
    "custom" | "fixed" y de mayor a menor, "breakdown" (texto legible),
    "uses_custom": bool} o None si el precio no vale.
    P. ej. 69 € → 1 personalizada de 69 € = 71.020 pts; 56 € → 57.640;
    3 € → personalizada de 5 € (5.145), que gana a 2 × 2 € (5.350).
    """
    price = parse_price(eur)
    if price is None:
        return None
    need = int(-(-price // 1))                       # techo: tarjetas de € enteros
    options = [("fixed", v, p) for v, p in GIFT_CARDS]
    options += [("custom", v, custom_points(v))
                for v in range(CUSTOM_CARD_MIN, CUSTOM_CARD_MAX + 1)]
    limit = need + max(CUSTOM_CARD_MIN, max(v for v, _ in GIFT_CARDS))
    def rank(points: int, n: int) -> tuple[int, int]:
        return points + CARD_MIN_SAVING * n, n

    # best[a] = (puntos, n.º de tarjetas, tipo, valor de la última tarjeta)
    best: list[tuple[int, int, str, int] | None] = [None] * (limit + 1)
    best[0] = (0, 0, "", 0)
    for amount in range(1, limit + 1):
        for kind, value, pts in options:
            prev = best[amount - value] if amount >= value else None
            if prev is not None and (best[amount] is None or
                                     rank(prev[0] + pts, prev[1] + 1)
                                     < rank(*best[amount][:2])):
                best[amount] = (prev[0] + pts, prev[1] + 1, kind, value)
    covered = min((a for a in range(need, limit + 1) if best[a] is not None),
                  key=lambda a: (*rank(*best[a][:2]), a))
    cards: list[tuple[str, int]] = []
    amount = covered
    while amount > 0:
        _pts, _n, kind, value = best[amount]
        cards.append((kind, value))
        amount -= value
    cards.sort(key=lambda c: (c[0] != "custom", -c[1]))
    return {"points": best[covered][0], "covered_eur": covered, "cards": cards,
            "breakdown": _cards_text(cards),
            "uses_custom": any(kind == "custom" for kind, _ in cards)}


def current_balance(last_run: dict[str, Any],
                    history: list[dict[str, Any]] | None = None) -> int | None:
    """Saldo actual: points_after de la última corrida o, si falta, el último
    saldo del historial. None si no se conoce."""
    pa = (last_run or {}).get("points_after")
    if type(pa) is int and pa >= 0:
        return pa
    for day in reversed(history or []):
        if day.get("balance") is not None:
            return int(day["balance"])
    return None


def _eta_text(days: int, today: date) -> str:
    when = today + timedelta(days=days)
    target = f"hacia el {when.day} de {_MONTHS_FULL_ES[when.month - 1]}"
    if when.year != today.year and days >= 300:
        target += f" de {when.year}"
    if days < 45:
        return f"≈ {days} día · {target}" if days == 1 else f"≈ {days} días · {target}"
    months = f"{days / 30:.1f}".replace(".", ",")
    if months.endswith(",0"):
        months = months[:-2]
    return f"≈ {months} " + ("mes" if months == "1" else "meses") + f" · {target}"


def goal_progress(goal: dict[str, Any] | None, balance: int | None,
                  pace_month: Any = 0, today: date | None = None) -> dict[str, Any] | None:
    """
    Seguimiento del objetivo (función pura). `pace_month` es el ritmo mensual
    de history_summary(); el diario es ese entre 30. Devuelve None si no hay
    objetivo válido; si no:
      name, price_text ('56 €'), points, breakdown, covered_eur,
      balance (None si no se conoce), fraction (0..1) y percent (0..100, con
      tope), left (puntos que faltan, mínimo 0), surplus (los que
      sobrarían), reached, pace_text ('a ~9.075 pts/mes'; '' sin ritmo),
      days (None si no se puede estimar), eta_date y eta_text:
      '¡Ya lo tienes!' · '≈ 12 días · hacia el 2 de diciembre' ·
      '≈ 3,5 meses · hacia el 5 de marzo' · 'Sin datos suficientes'.
    """
    cost = points_for_price((goal or {}).get("price_eur"))
    if cost is None:
        return None
    today = today or date.today()
    points = cost["points"]
    have = max(0, int(balance)) if balance is not None else 0
    left = max(0, points - have)
    fraction = min(1.0, have / points)
    try:
        daily = float(pace_month) / 30
    except (TypeError, ValueError):
        daily = 0.0
    out = {"name": str(goal.get("name") or "").strip() or "Mi objetivo",
           "price_text": format_eur(goal["price_eur"]), "points": points,
           "breakdown": cost["breakdown"], "covered_eur": cost["covered_eur"],
           "card_hint": CUSTOM_CARD_HINT if cost["uses_custom"] else "",
           "balance": balance, "fraction": fraction, "percent": int(fraction * 100),
           "left": left, "surplus": max(0, have - points),
           "pace_text": (f"a ~{format_points(round(daily * 30))} pts/mes"
                         if daily > 0 else ""),
           "reached": balance is not None and left == 0,
           "days": None, "eta_date": None, "eta_text": "Sin datos suficientes"}
    if out["reached"]:
        return {**out, "days": 0, "eta_date": today, "eta_text": "¡Ya lo tienes!"}
    if balance is None or daily <= 0:
        return out
    days = int(-(-left // daily))
    if days > 36500:
        return out
    return {**out, "days": days, "eta_date": today + timedelta(days=days),
            "eta_text": _eta_text(days, today)}


# =========================================================================
# Desglose de un día pasado (a partir de su log)
# =========================================================================
# Al pulsar un día en el historial, el panel enseña el detalle de ESE día. Lo
# saca del ÚLTIMO bloque "=== VERIFICACIÓN FINAL ===" de su log y lo devuelve
# con la misma forma que last_run.json, para pintarlo con las mismas funciones
# (today_checklist, app_status, activity_streaks, stamps_summary…) pasándoles
# ese día como "hoy". El bloque existe desde el 13/11/2026; antes (o sin log)
# no hay desglose y se dice tal cual.
_VERIFY_HEADER = "=== VERIFICACIÓN FINAL ==="
_VERIFY_LINE = re.compile(r"^(\d{2}:\d{2}:\d{2})\s+\[verify\]\s+\w+:\s*(.*)$")
_VERIFY_PAIRS = (
    ("pc_search", re.compile(r"^búsquedas de PC: (\d+)/(\d+)")),
    ("daily_set", re.compile(r"^conjunto diario: (\d+)/(\d+)")),
    ("visual", re.compile(r"^búsqueda por imagen: (\d+)/(\d+)")),
    ("edge", re.compile(r"^racha de Edge(?: \(minutos\))?: (\d+)/(\d+)")),
    ("app_streak", re.compile(r"^racha de la app de Bing: (\d+)/(\d+)")),
    ("stamps", re.compile(r"^sellos: (\d+)/(\d+)")),
)
_VERIFY_PENDING = re.compile(
    r"^(?:tarjetas de sellos|promociones con tareas[^:]*): (\d+) pendientes")
_VERIFY_READ = re.compile(r"^app de Bing — Leer para ganar: (\d+)/(\d+)")
_VERIFY_STREAK = re.compile(
    r"(búsqueda visual|búsqueda|conjunto diario|Edge|app de Bing) día (\d+)/(\d+)")
_VERIFY_STREAK_KEYS = {"búsqueda": "search", "conjunto diario": "daily_set",
                       "Edge": "edge", "app de Bing": "app", "búsqueda visual": "visual"}
_LOG_SESSION = re.compile(r"\bpuntos: (\d+) → (\d+)")


def parse_verification_log(text: str, day: str) -> dict[str, Any] | None:
    """
    Desglose de un día (función pura) a partir del texto de su log: el último
    bloque de verificación, con forma de last_run. None si el log no lo trae.
    Claves: status ("ok" | "incomplete"), updated_at, last_completed (si quedó
    todo), verification {pc_search, daily_set, visual, edge, app_streak,
    pending_punch, streak_days, stamps, retry, ok} (solo lo que aparezca),
    app {date, linked, checkin, read, read_status} si hay líneas de la app,
    browser ("chrome" si la racha de Edge se omitió, "edge" si no),
    points_before/points_after de la última sesión y from_log=True.
    """
    lines = (text or "").splitlines()
    start = next((i for i in range(len(lines) - 1, -1, -1)
                  if _VERIFY_HEADER in lines[i]), None)
    if start is None:
        return None
    head = _VERIFY_LINE.match(lines[start].strip())
    clock = head.group(1) if head else "00:00:00"
    ver: dict[str, Any] = {"retry": []}
    app: dict[str, Any] = {}
    browser = "edge"
    ok = None
    for raw in lines[start + 1:]:
        m = _VERIFY_LINE.match(raw.strip())
        if not m:
            continue
        msg = m.group(2).strip()
        if msg.startswith("VERIFICACIÓN:"):
            ok = "✓" in msg
            clock = m.group(1)
            continue
        for key, rx in _VERIFY_PAIRS:
            hit = rx.match(msg)
            if hit:
                ver[key] = [int(hit.group(1)), int(hit.group(2))]
                break
        else:
            hit = _VERIFY_PENDING.match(msg)
            if hit:
                ver["pending_punch"] = int(hit.group(1))
            elif msg.startswith("racha de Edge") and "omitida" in msg:
                browser = "chrome"
            elif msg.startswith("rachas:"):
                days = {_VERIFY_STREAK_KEYS[n]: [int(a), int(b)]
                        for n, a, b in _VERIFY_STREAK.findall(msg)}
                if days:
                    ver["streak_days"] = days
            elif msg.startswith("app de Bing — check-in:"):
                app["checkin"] = "ok" if "✓" in msg else "fail"
            else:
                hit = _VERIFY_READ.match(msg)
                if hit:
                    app["read"] = [int(hit.group(1)), int(hit.group(2))]
                    app["read_status"] = "ok" if "✓" in msg else "partial"
    pairs = [ver[k] for k in ("pc_search", "daily_set", "visual", "edge") if k in ver]
    if ok is None:                       # bloque cortado: se deduce de las cifras
        ok = bool(pairs) and all(a >= b for a, b in pairs)
    ver["ok"] = ok
    out: dict[str, Any] = {"status": "ok" if ok else "incomplete",
                           "updated_at": f"{day}T{clock}", "verification": ver,
                           "browser": browser, "from_log": True}
    if ok:
        out["last_completed"] = day
    if app:
        out["app"] = {"date": day, "linked": True, **app}
    sessions = _LOG_SESSION.findall(text or "")
    if sessions:
        out["points_before"], out["points_after"] = (int(v) for v in sessions[-1])
    return out


_day_cache: dict[str, tuple[tuple[float, int], dict[str, Any] | None]] = {}


def read_day_detail(day: str, log_dir: Path | None = None) -> dict[str, Any] | None:
    """(E/S) parse_verification_log() del log del día `day` ('YYYY-MM-DD').
    None si no hay log, no se puede leer o no trae el bloque. Cachea por fecha
    de modificación y tamaño."""
    try:
        name = date.fromisoformat(str(day)).strftime("%Y%m%d") + ".log"
        path = Path(log_dir if log_dir is not None else LOG_DIR) / name
        st = path.stat()
        stamp = (st.st_mtime, st.st_size)
        cached = _day_cache.get(str(path))
        if cached is None or cached[0] != stamp:
            cached = (stamp, parse_verification_log(
                path.read_text(encoding="utf-8", errors="replace"), str(day)))
            _day_cache[str(path)] = cached
        return cached[1]
    except (OSError, ValueError):
        return None


def day_title(day: Any, today: date | None = None) -> str:
    """Título de la tarjeta de actividades según el día que se mira:
    'Actividades de hoy', 'Actividades de ayer', 'Actividades del martes 3 nov'."""
    today = today or date.today()
    try:
        d = day if isinstance(day, date) else date.fromisoformat(str(day))
    except ValueError:
        return "Actividades de hoy"
    delta = (today - d).days
    if delta == 0:
        return "Actividades de hoy"
    if delta == 1:
        return "Actividades de ayer"
    return f"Actividades del {_WEEKDAYS_ES[d.weekday()]} {_short_date(d)}"


def day_empty_text(entry: dict[str, Any] | None) -> tuple[str, str]:
    """(título, explicación) del estado vacío de un día sin desglose, con lo
    poco que sí se sabe por el historial (los puntos de ese día)."""
    title = "Sin desglose guardado para este día"
    gained = (entry or {}).get("gained")
    if gained is None:
        return title, "Ese día no hubo corrida del bot (no hay registro)."
    if gained > 0:
        return title, (f"Ganaste {format_delta(gained)} pts, pero el registro de ese día "
                       "no guarda el detalle por actividad.")
    return title, "Hubo corrida, pero no sumó puntos ni guardó el detalle por actividad."
