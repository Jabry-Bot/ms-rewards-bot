"""Configuración del bot de Microsoft Rewards."""
from __future__ import annotations

import os
import socket
from pathlib import Path

CHROME_PATH = os.getenv(
    "MSR_CHROME_PATH",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
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
# de la cuenta en Microsoft Rewards:
#   - Nivel 1: ~10-12 búsquedas cuentan (cap ~50 pts/día en search)
#   - Nivel 2: ~30 búsquedas desktop cuentan (cap ~90 pts/día)
# Si MSR_SEARCH_COUNT está fijado por el usuario, lo respetamos. Si no,
# el run.py detecta el nivel al arrancar y elige el valor adecuado.
SEARCH_COUNT_BY_LEVEL = {
    1: int(os.getenv("MSR_SEARCH_COUNT_L1", "14")),
    2: int(os.getenv("MSR_SEARCH_COUNT_L2", "32")),
}
# Valor por defecto si no se pudo detectar el nivel: usar el de Nivel 2
# (es preferible hacer búsquedas de más que de menos — solo "sobran" las
# que superen el cap; no hay penalización por intentarlas).
SEARCH_COUNT = int(os.getenv("MSR_SEARCH_COUNT", str(SEARCH_COUNT_BY_LEVEL[2])))

# Búsquedas móviles (Bing tiene un cap separado para móvil):
#   - Nivel 1: ~7-10 búsquedas móvil cuentan
#   - Nivel 2: ~20 búsquedas móvil cuentan
# Se ejecutan desde el mismo Chrome de escritorio emulando un Pixel 8 vía CDP.
MOBILE_SEARCH_COUNT_BY_LEVEL = {
    1: int(os.getenv("MSR_MOBILE_SEARCH_COUNT_L1", "10")),
    2: int(os.getenv("MSR_MOBILE_SEARCH_COUNT_L2", "20")),
}
MOBILE_SEARCH_COUNT = int(
    os.getenv("MSR_MOBILE_SEARCH_COUNT", str(MOBILE_SEARCH_COUNT_BY_LEVEL[2]))
)
# Habilitado por defecto. Pon MSR_MOBILE_SEARCHES=0 para desactivar.
MOBILE_SEARCHES_ENABLED = os.getenv("MSR_MOBILE_SEARCHES", "1") == "1"

# Idioma de las queries que generamos.
LOCALE = os.getenv("MSR_LOCALE", "es-ES")

# Posición/tamaño de arranque de la ventana del bot. Por defecto fuera de
# pantalla para no molestar durante una partida (la página sigue "visible"
# para Bing, así que se acreditan puntos — a diferencia de minimizar).
# Pon MSR_WINDOW_POSITION="" para que Chrome use su posición normal.
WINDOW_POSITION = os.getenv("MSR_WINDOW_POSITION", "-2400,-2400")
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
