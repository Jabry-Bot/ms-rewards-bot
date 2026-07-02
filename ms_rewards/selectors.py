"""
Carga los selectores CSS desde selectors.json y los expone como dicts.

Punto de extensión central: el resto del código (daily.py, searches.py,
login.py) consulta selectores aquí en vez de hardcodearlos. Así el healer
puede modificar selectors.json y los cambios se recogen reload()-eando.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

log = logging.getLogger("selectors")

_PATH = Path(__file__).parent / "selectors.json"
_LOCK = threading.Lock()
_DATA: dict[str, Any] = {}
_VERSION: str = "0.0.0"


def _load_from_disk() -> dict[str, Any]:
    with _PATH.open(encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    """Re-lee selectors.json. Idempotente y seguro entre hilos."""
    global _DATA, _VERSION
    with _LOCK:
        try:
            data = _load_from_disk()
        except Exception as exc:
            log.exception("no se pudo leer selectors.json: %s", exc)
            raise
        _DATA = data
        _VERSION = data.get("version", "0.0.0")
        log.info("selectores cargados (v%s)", _VERSION)


def get(path: str, default: str | None = None) -> str:
    """
    Devuelve el selector indicado por una ruta dot-separated, p.ej.
    'quiz.options'. Lanza KeyError si no existe y no se pasa default.
    """
    cur: Any = _DATA
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            if default is not None:
                return default
            raise KeyError(f"selector path no encontrado: {path}")
        cur = cur[part]
    if not isinstance(cur, str):
        raise TypeError(f"selector path {path} no es string: {type(cur).__name__}")
    return cur


def version() -> str:
    return _VERSION


def all_selectors() -> dict[str, Any]:
    """Devuelve una copia del dict completo (sin la clave 'version')."""
    out = {k: v for k, v in _DATA.items() if k != "version"}
    return json.loads(json.dumps(out))  # deep copy via JSON


def update_and_persist(path: str, new_value: str, bump: str = "patch") -> str:
    """
    Actualiza el selector en `path`, bumpea la versión y persiste.
    Devuelve la nueva versión. Pensado para uso del healer.
    """
    global _DATA, _VERSION
    with _LOCK:
        data = _load_from_disk()  # leer fresco para no perder otros cambios
        cur = data
        parts = path.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = new_value
        new_v = _bump_version(data.get("version", "0.0.0"), bump)
        data["version"] = new_v
        with _PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        _DATA = data
        _VERSION = new_v
    return new_v


def _bump_version(v: str, kind: str) -> str:
    try:
        major, minor, patch = [int(x) for x in v.split(".")]
    except Exception:
        major, minor, patch = 1, 0, 0
    if kind == "major":
        major, minor, patch = major + 1, 0, 0
    elif kind == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


# Carga inicial al importar
reload()
