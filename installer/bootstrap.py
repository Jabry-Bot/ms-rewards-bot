"""
Lógica pura del instalador (setup.exe).

Este módulo NO importa ninguna librería de GUI ni dependencias de terceros: solo
usa la stdlib. Es deliberado — el instalador se empaqueta con PyInstaller y debe
poder ejecutarse en una máquina SIN Python ni el venv del bot todavía. Aquí solo
detectamos qué hay instalado y construimos las líneas de comando; la ejecución de
subprocesos y descargas (con efectos laterales) vive en installer/app.py, para
que esta capa quede testeable sin tocar el sistema.

Términos:
  - "Python del sistema": el python.exe real instalado en el PC (el que usará la
    Scheduled Task). NO es sys.executable cuando corremos dentro del .exe
    empaquetado (ahí sys.executable es el propio setup.exe).
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --- Rutas del repo ------------------------------------------------------
# El setup.exe debe vivir en la raíz del repo, junto a ms_rewards/. Congelado
# (PyInstaller onefile) anclamos a la carpeta del .exe; en desarrollo, al fuente.
if getattr(sys, "frozen", False):
    ROOT = Path(sys.executable).resolve().parent
else:
    ROOT = Path(__file__).resolve().parent.parent

REWARDS_DIR = ROOT / "ms_rewards"
VENV_DIR = REWARDS_DIR / ".venv"
VENV_PY = VENV_DIR / "Scripts" / "python.exe"
REQUIREMENTS = REWARDS_DIR / "requirements.txt"
SETUP_CLI = REWARDS_DIR / "setup_cli.py"
INSTALL_TASK_PS1 = REWARDS_DIR / "scheduler" / "install_task.ps1"

# Python mínimo soportado y versión que instalamos si falta.
MIN_PYTHON = (3, 10)
PYTHON_VERSION = "3.12.7"
PYTHON_INSTALLER_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/"
    f"python-{PYTHON_VERSION}-amd64.exe"
)

# Git for Windows: instalador oficial silencioso como fallback de winget.
GIT_VERSION = "2.47.1"
GIT_INSTALLER_URL = (
    f"https://github.com/git-for-windows/git/releases/download/"
    f"v{GIT_VERSION}.windows.1/Git-{GIT_VERSION}-64-bit.exe"
)

# IDs de winget para cada herramienta.
WINGET_IDS = {
    "git": "Git.Git",
    "python": "Python.Python.3.12",
    "edge": "Microsoft.Edge",
    "chrome": "Google.Chrome",
}


# --- Detección de variables de entorno (resueltas en runtime) ------------
def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _local_app_data() -> Path:
    return Path(_env("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))


def _program_files() -> Path:
    return Path(_env("ProgramFiles", r"C:\Program Files"))


def _program_files_x86() -> Path:
    return Path(_env("ProgramFiles(x86)", r"C:\Program Files (x86)"))


# --- Rutas candidatas de cada herramienta --------------------------------
def candidate_python_paths() -> list[Path]:
    """Ubicaciones típicas de python.exe (instalación por-usuario y global)."""
    lad = _local_app_data()
    pf = _program_files()
    out: list[Path] = []
    for ver in ("313", "312", "311", "310"):
        out.append(lad / "Programs" / "Python" / f"Python{ver}" / "python.exe")
        out.append(pf / f"Python{ver}" / "python.exe")
    return out


def candidate_git_paths() -> list[Path]:
    lad = _local_app_data()
    return [
        _program_files() / "Git" / "cmd" / "git.exe",
        _program_files_x86() / "Git" / "cmd" / "git.exe",
        lad / "Programs" / "Git" / "cmd" / "git.exe",
    ]


def candidate_edge_paths() -> list[Path]:
    return [
        _program_files_x86() / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        _program_files() / "Microsoft" / "Edge" / "Application" / "msedge.exe",
    ]


def candidate_chrome_paths() -> list[Path]:
    return [
        _program_files() / "Google" / "Chrome" / "Application" / "chrome.exe",
        _program_files_x86() / "Google" / "Chrome" / "Application" / "chrome.exe",
    ]


def first_existing(paths: list[Path]) -> Path | None:
    """Primera ruta de la lista que exista en disco, o None."""
    for p in paths:
        try:
            if p.exists():
                return p
        except OSError:
            continue
    return None


# --- Parsing de versión de Python ----------------------------------------
def parse_python_version(text: str) -> tuple[int, int, int] | None:
    """Extrae (major, minor, micro) de la salida de `python --version`."""
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_supported_python(version: tuple[int, ...] | None) -> bool:
    """True si la versión cumple el mínimo soportado."""
    return version is not None and tuple(version[:2]) >= MIN_PYTHON


# --- Constructores de comandos -------------------------------------------
def winget_install_cmd(tool: str) -> list[str]:
    """Comando winget para instalar una herramienta conocida (silencioso)."""
    if tool not in WINGET_IDS:
        raise KeyError(f"herramienta desconocida: {tool!r}")
    return [
        "winget", "install", "-e", "--id", WINGET_IDS[tool],
        "--silent",
        "--accept-package-agreements", "--accept-source-agreements",
    ]


def python_direct_install_cmd(installer_path: str | os.PathLike) -> list[str]:
    """Instalador oficial de Python en modo silencioso, por-usuario, con PATH."""
    return [
        str(installer_path),
        "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0",
    ]


def git_direct_install_cmd(installer_path: str | os.PathLike) -> list[str]:
    """Instalador de Git for Windows en modo silencioso."""
    return [str(installer_path), "/VERYSILENT", "/NORESTART", "/SP-"]


def venv_create_cmd(system_python: str | os.PathLike, venv_dir: str | os.PathLike = VENV_DIR) -> list[str]:
    """`<python> -m venv <dir>` con el Python del sistema."""
    return [str(system_python), "-m", "venv", str(venv_dir)]


def pip_upgrade_cmd(venv_python: str | os.PathLike = VENV_PY) -> list[str]:
    return [str(venv_python), "-m", "pip", "install", "--upgrade", "pip"]


def pip_requirements_cmd(
    venv_python: str | os.PathLike = VENV_PY,
    requirements: str | os.PathLike = REQUIREMENTS,
) -> list[str]:
    return [str(venv_python), "-m", "pip", "install", "-r", str(requirements)]


def patchright_drivers_cmd(
    venv_python: str | os.PathLike = VENV_PY, browser: str = "chrome"
) -> list[str]:
    """Descarga los drivers de patchright para el navegador elegido."""
    target = "msedge" if browser == "edge" else "chrome"
    return [str(venv_python), "-m", "patchright", "install", target]


def winget_available() -> bool:
    """True si winget está disponible (gestor de paquetes de Windows)."""
    import shutil
    return shutil.which("winget") is not None


def find_in_path(name: str) -> Path | None:
    """Busca un ejecutable en el PATH actual."""
    import shutil
    found = shutil.which(name)
    return Path(found) if found else None


# --- Plan de instalación (lo consume la GUI para pintar el checklist) -----
@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    # Función que devuelve la ruta instalada (o None). Se evalúa en runtime.
    detector: Callable[[], "Path | None"]
    required: bool = True
    # Texto de ayuda cuando falta.
    note: str = ""


def detect_git() -> Path | None:
    return find_in_path("git") or first_existing(candidate_git_paths())


def detect_edge() -> Path | None:
    return first_existing(candidate_edge_paths())


def detect_chrome() -> Path | None:
    return first_existing(candidate_chrome_paths())


def detect_python_path() -> Path | None:
    """
    Ruta del python.exe del sistema (no el setup.exe). Prioriza el PATH y luego
    las ubicaciones típicas. La validación de versión la hace app.py ejecutando
    `python --version` (subprocess), ya que aquí no tocamos el sistema.
    """
    candidate = find_in_path("python")
    # Evitar devolver el propio setup.exe si estuviera congelado y en PATH.
    if candidate and candidate.name.lower().startswith("python"):
        if not getattr(sys, "frozen", False) or candidate.resolve() != Path(sys.executable).resolve():
            return candidate
    return first_existing(candidate_python_paths())


# Herramientas que el instalador gestiona. Edge es required (da el bonus de
# Rewards); Chrome es opcional (alternativa). Python y git son required.
TOOLS: list[Tool] = [
    Tool("python", "Python 3.10+", detect_python_path, required=True),
    Tool("git", "Git (auto-update del bot)", detect_git, required=True),
    Tool("edge", "Microsoft Edge", detect_edge, required=True,
         note="Da el bonus de búsquedas de Microsoft Rewards."),
    Tool("chrome", "Google Chrome (opcional)", detect_chrome, required=False,
         note="Alternativa a Edge."),
]


@dataclass
class ToolStatus:
    tool: Tool
    path: Path | None = None
    extra: str = ""

    @property
    def installed(self) -> bool:
        return self.path is not None


def scan_tools(tools: list[Tool] = TOOLS) -> list[ToolStatus]:
    """Estado actual de cada herramienta (instalada o no)."""
    return [ToolStatus(t, t.detector()) for t in tools]


def venv_ready() -> bool:
    """True si el entorno virtual del bot ya existe."""
    return VENV_PY.exists()
