"""
Operaciones de Windows en Python puro (sin PowerShell ni cmd).

Reemplaza los scripts .ps1 y las llamadas a powershell/setx/reg que antes
gestionaban la Scheduled Task, el acceso directo del Escritorio, las variables
de entorno persistentes y el conteo de procesos del navegador. Todo vía pywin32
(COM del Task Scheduler / WScript.Shell / WMI) y winreg, que ya son dependencias.

Se usa de dos formas:
  - Importado desde otros módulos del bot (uninstall.py, switch_account.py,
    setup_cli.py), que corren en el venv (con pywin32).
  - Como CLI, para que las GUI empaquetadas (panel/instalador) lo invoquen con
    el python del venv:
        python winutil.py task-install
        python winutil.py task-uninstall
        python winutil.py task-status            # imprime JSON
        python winutil.py shortcut <target> <lnk> <workdir>
        python winutil.py env-set MSR_BROWSER edge   # persiste variable de usuario
        python winutil.py env-get MSR_BROWSER        # imprime su valor ('' si no existe)
"""
from __future__ import annotations

import json
import logging
import random
import sys
import winreg
from pathlib import Path

log = logging.getLogger("winutil")

TASK_NAME = "MsRewardsBot"

_HERE = Path(__file__).resolve().parent
_VENV_PYW = _HERE / ".venv" / "Scripts" / "pythonw.exe"
_VENV_PY = _HERE / ".venv" / "Scripts" / "python.exe"
_RUN_PY = _HERE / "run.py"

# Variables de entorno persistentes que crea/borra el bot (HKCU\Environment).
ENV_VARS = [
    "MSR_USER_ID", "MSR_BROWSER", "MSR_MAINTAINER", "MSR_OLLAMA_URL",
    "MSR_OLLAMA_MODEL", "MSR_USER_DATA_DIR", "MSR_SEARCH_COUNT",
    "MSR_SEARCH_COUNT_L1", "MSR_SEARCH_COUNT_L2", "MSR_LOCALE",
    "MSR_CDP_PORT", "MSR_CHROME_PATH",
]


# --- Usuario actual ------------------------------------------------------
def current_user() -> str:
    """DOMINIO\\usuario del usuario actual (formato SAM)."""
    try:
        import win32api  # type: ignore
        return win32api.GetUserNameEx(2)  # NameSamCompatible
    except Exception:
        import os
        dom = os.environ.get("USERDOMAIN", "")
        usr = os.environ.get("USERNAME", "")
        return f"{dom}\\{usr}" if dom else usr


# --- Scheduled Task (Task Scheduler COM) ---------------------------------
# Estados de tarea → texto.
_TASK_STATE = {0: "Unknown", 1: "Disabled", 2: "Queued", 3: "Ready", 4: "Running"}


def _scheduler():
    import win32com.client  # type: ignore
    svc = win32com.client.Dispatch("Schedule.Service")
    svc.Connect()
    return svc


def install_task(task_name: str = TASK_NAME) -> None:
    """
    Registra la Scheduled Task del bot: arranca al iniciar sesión (con un
    retardo aleatorio) y a diario a una hora aleatoria entre las 10:00 y 14:00.
    Idempotente (CREATE_OR_UPDATE). Equivalente al antiguo install_task.ps1.
    """
    svc = _scheduler()
    root = svc.GetFolder("\\")
    td = svc.NewTask(0)

    td.RegistrationInfo.Description = "Microsoft Rewards bot (auto-run diario)"

    user = current_user()
    exe = str(_VENV_PYW if _VENV_PYW.exists() else _VENV_PY)

    # --- Triggers ---
    # Logon (tipo 9) con retardo PT2-10M.
    logon = td.Triggers.Create(9)
    logon.UserId = user
    logon.Delay = f"PT{random.randint(2, 10)}M"

    # Daily (tipo 2) a hora aleatoria 10:00-13:59. La fecha del StartBoundary
    # solo fija la HORA de disparo; el día es indiferente para -Daily.
    daily = td.Triggers.Create(2)
    hh = random.randint(10, 13)
    mm = random.randint(0, 59)
    daily.StartBoundary = f"2020-01-01T{hh:02d}:{mm:02d}:00"
    daily.DaysInterval = 1

    # --- Action: pythonw run.py --scheduled ---
    action = td.Actions.Create(0)  # TASK_ACTION_EXEC
    action.Path = exe
    action.Arguments = f'"{_RUN_PY}" --scheduled'
    action.WorkingDirectory = str(_HERE)

    # --- Settings ---
    s = td.Settings
    s.StartWhenAvailable = True
    s.DisallowStartIfOnBatteries = False
    s.StopIfGoingOnBatteries = False
    s.ExecutionTimeLimit = "PT2H"
    s.MultipleInstances = 2  # IgnoreNew
    s.IdleSettings.StopOnIdleEnd = False

    # --- Principal: interactivo, sin privilegios elevados ---
    p = td.Principal
    p.UserId = user
    p.LogonType = 3   # TASK_LOGON_INTERACTIVE_TOKEN
    p.RunLevel = 0    # TASK_RUNLEVEL_LUA (limited)

    # CREATE_OR_UPDATE=6, logonType interactive token=3
    root.RegisterTaskDefinition(task_name, td, 6, user, None, 3)
    log.info("Scheduled Task '%s' registrada (logon + diaria %02d:%02d)",
             task_name, hh, mm)


def uninstall_task(task_name: str = TASK_NAME) -> bool:
    """Elimina la Scheduled Task. True si existía y se borró."""
    svc = _scheduler()
    root = svc.GetFolder("\\")
    try:
        root.DeleteTask(task_name, 0)
        log.info("Scheduled Task '%s' eliminada", task_name)
        return True
    except Exception:
        log.info("Scheduled Task '%s' no existía", task_name)
        return False


def task_status(task_name: str = TASK_NAME) -> dict:
    """
    Estado de la tarea como dict (vacío si no está registrada). Mismas claves
    que esperaba el panel: state, last_run, last_result, next_run.
    """
    try:
        svc = _scheduler()
        root = svc.GetFolder("\\")
    except Exception as exc:
        log.warning("no se pudo conectar al Task Scheduler: %s", exc)
        return {}
    try:
        task = root.GetTask(task_name)
    except Exception:
        return {}
    try:
        return {
            "state": _TASK_STATE.get(int(task.State), str(task.State)),
            "last_run": str(task.LastRunTime),
            "last_result": int(task.LastTaskResult),
            "next_run": str(task.NextRunTime),
        }
    except Exception as exc:
        log.warning("no se pudo leer el estado de la tarea: %s", exc)
        return {"state": "Registrada"}


# --- Acceso directo (WScript.Shell) --------------------------------------
def create_shortcut(target: str, shortcut: str, workdir: str) -> None:
    """Crea un .lnk apuntando a `target`. Equivalente al PS WScript.Shell."""
    import win32com.client  # type: ignore
    shell = win32com.client.Dispatch("WScript.Shell")
    sc = shell.CreateShortcut(str(shortcut))
    sc.TargetPath = str(target)
    sc.WorkingDirectory = str(workdir)
    sc.Save()
    log.info("acceso directo creado: %s", shortcut)


# --- Variables de entorno persistentes (winreg HKCU\Environment) ---------
def _broadcast_env_change() -> None:
    """Avisa a Windows de que cambió el entorno (para nuevas sesiones)."""
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        win32gui.SendMessageTimeout(
            win32con.HWND_BROADCAST, win32con.WM_SETTINGCHANGE, 0,
            "Environment", win32con.SMTO_ABORTIFHUNG, 5000,
        )
    except Exception:
        pass


def set_env_var(name: str, value: str) -> None:
    """Persiste una variable de usuario (equivalente a `setx`)."""
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    _broadcast_env_change()


def get_env_var(name: str) -> str:
    r"""Lee una variable de usuario persistente (HKCU\Environment). '' si no existe."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return str(value)
    except (FileNotFoundError, OSError):
        return ""


def delete_env_vars(names: list[str] | None = None) -> int:
    """Borra variables de usuario persistentes. Devuelve cuántas borró."""
    names = names or ENV_VARS
    removed = 0
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_SET_VALUE) as key:
        for name in names:
            try:
                winreg.DeleteValue(key, name)
                removed += 1
            except FileNotFoundError:
                pass
            except OSError:
                pass
    if removed:
        _broadcast_env_change()
    return removed


# --- Conteo de procesos del navegador (WMI) ------------------------------
def count_browser_processes(proc_name: str, cmdline_substr: str) -> int:
    """Cuántos procesos `proc_name` tienen `cmdline_substr` en su línea de comando."""
    try:
        import win32com.client  # type: ignore
        wmi = win32com.client.GetObject("winmgmts:")
        q = f"SELECT CommandLine FROM Win32_Process WHERE Name='{proc_name}'"
        n = 0
        for p in wmi.ExecQuery(q):
            cl = p.CommandLine or ""
            if cmdline_substr in cl:
                n += 1
        return n
    except Exception as exc:
        log.warning("no se pudo contar procesos: %s", exc)
        return 0


def kill_browser_processes(proc_name: str, cmdline_substr: str) -> int:
    """
    Mata los procesos `proc_name` cuya línea de comando contenga `cmdline_substr`
    (el navegador del bot, identificado por su --user-data-dir). Python puro vía
    WMI; matar el padre arrastra a los hijos, así que los PID ya muertos se
    ignoran en silencio. Devuelve cuántos se intentaron matar.
    """
    try:
        import win32com.client  # type: ignore
        wmi = win32com.client.GetObject("winmgmts:")
        q = (f"SELECT ProcessId, CommandLine FROM Win32_Process "
             f"WHERE Name='{proc_name}'")
        killed = 0
        for p in wmi.ExecQuery(q):
            cl = p.CommandLine or ""
            if cmdline_substr in cl:
                try:
                    p.Terminate()
                    killed += 1
                except Exception:
                    pass  # ya había muerto (hijo del que matamos antes)
        return killed
    except Exception as exc:
        log.warning("no se pudieron matar procesos: %s", exc)
        return 0


# --- CLI -----------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    # El log va a stderr: en task-status el stdout debe llevar SOLO el JSON
    # (el panel lo parsea), y aún así los mensajes se ven en el log en vivo.
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stderr)])

    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("uso: winutil.py task-install|task-uninstall|task-status|shortcut|env-set|env-get ...")
        return 2

    cmd = args[0]
    try:
        if cmd == "task-install":
            install_task()
            return 0
        if cmd == "task-uninstall":
            uninstall_task()
            return 0
        if cmd == "task-status":
            print(json.dumps(task_status(), ensure_ascii=False))
            return 0
        if cmd == "shortcut":
            if len(args) < 4:
                print("uso: winutil.py shortcut <target> <lnk> <workdir>")
                return 2
            create_shortcut(args[1], args[2], args[3])
            return 0
        if cmd == "env-set":
            if len(args) < 3:
                print("uso: winutil.py env-set <NOMBRE> <valor>")
                return 2
            set_env_var(args[1], args[2])
            log.info("variable de usuario %s = %s", args[1], args[2])
            return 0
        if cmd == "env-get":
            if len(args) < 2:
                print("uso: winutil.py env-get <NOMBRE>")
                return 2
            print(get_env_var(args[1]))
            return 0
        print(f"comando desconocido: {cmd}")
        return 2
    except Exception as exc:
        log.exception("winutil falló: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
