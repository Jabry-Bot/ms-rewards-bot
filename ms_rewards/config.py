"""Configuración del bot de Microsoft Rewards."""
from __future__ import annotations

import os
import socket
from pathlib import Path

# Navegador a usar: "chrome" o "edge". Ambos son Chromium, así que el bot
# funciona igual; Edge además otorga el bonus de búsquedas de Microsoft Rewards.
VALID_BROWSERS = ("chrome", "edge")
BROWSER = os.getenv("MSR_BROWSER", "chrome").strip().lower()
if BROWSER not in VALID_BROWSERS:
    BROWSER = "chrome"

# Canal que usa patchright/Playwright en launch_persistent_context. Es lo que
# decide qué navegador real del sistema se lanza.
CHANNEL = "msedge" if BROWSER == "edge" else "chrome"

# Nombre del proceso del navegador (para matar/contar instancias del bot).
BROWSER_PROC = "msedge.exe" if BROWSER == "edge" else "chrome.exe"


def _first_existing(paths: list[str], fallback: str) -> str:
    for p in paths:
        if os.path.exists(p):
            return p
    return fallback


_CHROME_DEFAULT = _first_existing(
    [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ],
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
)
_EDGE_DEFAULT = _first_existing(
    [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)

# Ruta al ejecutable del navegador (se usa en el spawn standalone del modo
# --setup; launch_persistent_context resuelve el ejecutable vía CHANNEL).
# MSR_CHROME_PATH la sobreescribe si el usuario instaló el navegador en otra ruta.
CHROME_PATH = os.getenv("MSR_CHROME_PATH") or (
    _EDGE_DEFAULT if BROWSER == "edge" else _CHROME_DEFAULT
)

# Identificador del usuario que ejecuta esta instancia. Se usa para:
#   - separar perfiles de Chrome cuando varias cuentas viven en un mismo PC,
#   - etiquetar logs / state / telemetría.
# Por defecto, el hostname. setup_cli.py puede hacer `setx MSR_USER_ID ...`.
USER_ID = os.getenv("MSR_USER_ID", socket.gethostname()).strip() or socket.gethostname()

# Perfil dedicado por usuario. Si existe el legacy `C:\Temp\MsRewardsCDP`
# (instalaciones previas a la separación por usuario), se sigue usando para
# no romper sesiones ya iniciadas.
_LEGACY_PROFILE = Path(r"C:\Temp\MsRewardsCDP")
_DEFAULT_PROFILE = _LEGACY_PROFILE if _LEGACY_PROFILE.exists() else _LEGACY_PROFILE / USER_ID
USER_DATA_DIR = os.getenv("MSR_USER_DATA_DIR", str(_DEFAULT_PROFILE))

CDP_PORT = int(os.getenv("MSR_CDP_PORT", "9333"))

# Búsquedas a realizar. El tope diario que Bing acredita depende del nivel
# de la cuenta en Microsoft Rewards. Tras el rediseño 2026-07 hay 3 tramos
# (activeLevel "newLevel1/2/3") y pointsPerSearch=3:
#   - Nivel 1 (Miembro): cap ~25 pts/día  → ~9 búsquedas cuentan
#   - Nivel 2 (Plata):   cap ~50 pts/día  → ~17 búsquedas cuentan
#   - Nivel 3 (Oro):     cap ~100 pts/día → ~34 búsquedas cuentan
# Si MSR_SEARCH_COUNT está fijado por el usuario, lo respetamos. Si no,
# el run.py detecta el nivel al arrancar y elige el valor adecuado.
SEARCH_COUNT_BY_LEVEL = {
    1: int(os.getenv("MSR_SEARCH_COUNT_L1", "14")),
    2: int(os.getenv("MSR_SEARCH_COUNT_L2", "24")),
    3: int(os.getenv("MSR_SEARCH_COUNT_L3", "34")),
}
# Valor por defecto si no se pudo detectar el nivel: usar el del tramo superior
# (es preferible hacer búsquedas de más que de menos — solo "sobran" las
# que superen el cap; no hay penalización por intentarlas).
SEARCH_COUNT = int(os.getenv("MSR_SEARCH_COUNT", str(SEARCH_COUNT_BY_LEVEL[3])))

# Idioma de las queries que generamos.
LOCALE = os.getenv("MSR_LOCALE", "es-ES")

# Posición/tamaño de arranque de la ventana del bot.
#   - WINDOW_POSITION: posición cuando la corrida es INTERACTIVA (setup,
#     switch_account, ejecutar.bat) — visible en 0,0 para poder ver/usar el CDP.
#   - WINDOW_POSITION_HIDDEN: posición en la corrida AUTOMÁTICA programada —
#     fuera de pantalla (-2400,-2400) para no molestar. Posicionar fuera de
#     pantalla NO cambia visibilityState (sigue "visible"), así que Bing
#     acredita igual — a diferencia de minimizar, que la pondría "hidden".
# Pon MSR_WINDOW_POSITION="" para que Chrome use su posición por defecto.
WINDOW_POSITION = os.getenv("MSR_WINDOW_POSITION", "0,0")
WINDOW_POSITION_HIDDEN = os.getenv("MSR_WINDOW_POSITION_HIDDEN", "-2400,-2400")
WINDOW_SIZE = os.getenv("MSR_WINDOW_SIZE", "900,700")

# Rangos de delays — todos en segundos.
# Configuración "intermedia": ~2× más rápida que la conservadora original,
# pero respeta los umbrales mínimos por debajo de los cuales Bing deja de
# acreditar puntos (entre búsquedas <8s, en card de artículo <6-8s, etc.).
DELAYS = {
    "between_searches": (8.0, 22.0),
    "long_pause": (45.0, 120.0),
    "long_pause_prob": 0.08,
    "after_typing": (0.3, 1.2),
    "read_serp": (2.0, 5.0),
    "click_result_prob": 0.30,
    "on_result": (4.0, 10.0),
    "between_cards": (3.0, 8.0),
    "on_card": (7.0, 14.0),   # mínimo ~6-8s para que cards de artículo cuenten
    "before_pick": (1.2, 3.0),  # quizzes necesitan >1s para registrar
}

_HERE = Path(__file__).parent
LOG_DIR = _HERE / "logs"
LOG_DIR.mkdir(exist_ok=True)

STATE_DIR = _HERE / "state"
STATE_DIR.mkdir(exist_ok=True)
LAST_RUN_PATH = STATE_DIR / "last_run.json"
CREDENTIALS_PATH = STATE_DIR / "credentials.bin"

# --- Modo maintainer ---
# Solo la máquina del maintainer (tú) ejecuta el flujo de auto-heal con LLM.
# El resto de usuarios recibe los selectores actualizados vía git pull.
MAINTAINER = os.getenv("MSR_MAINTAINER", "0") == "1"
OLLAMA_URL = os.getenv("MSR_OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("MSR_OLLAMA_MODEL", "qwen2.5-coder:7b")
