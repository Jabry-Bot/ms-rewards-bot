"""
Almacenamiento cifrado de credenciales de cuenta Microsoft.

Usa la API DPAPI de Windows (CryptProtectData / CryptUnprotectData) vía
pywin32. El blob cifrado solo puede ser descifrado por el mismo usuario
Windows en la misma máquina — equivalente a cómo Chrome guarda contraseñas.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger("credentials")

try:
    import win32crypt  # type: ignore
    _DPAPI_AVAILABLE = True
except Exception:  # pragma: no cover - dependencia opcional
    _DPAPI_AVAILABLE = False


_DESCRIPTION = "ms_rewards credentials"


def is_supported() -> bool:
    return _DPAPI_AVAILABLE


def save(email: str, password: str) -> None:
    if not _DPAPI_AVAILABLE:
        raise RuntimeError(
            "pywin32 no instalado: no se pueden guardar credenciales cifradas"
        )
    payload = json.dumps({"email": email, "password": password}).encode("utf-8")
    blob = win32crypt.CryptProtectData(payload, _DESCRIPTION, None, None, None, 0)
    config.CREDENTIALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    config.CREDENTIALS_PATH.write_bytes(blob)
    # Restringir permisos en la medida que NTFS permite (al menos lectura)
    try:
        import os
        os.chmod(config.CREDENTIALS_PATH, 0o600)
    except Exception:
        pass
    log.info("credenciales guardadas en %s", config.CREDENTIALS_PATH)


def load() -> tuple[str, str] | None:
    if not _DPAPI_AVAILABLE:
        return None
    path: Path = config.CREDENTIALS_PATH
    if not path.exists():
        return None
    try:
        blob = path.read_bytes()
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        obj = json.loads(data.decode("utf-8"))
        return obj["email"], obj["password"]
    except Exception as exc:
        log.warning("no se pudieron descifrar credenciales: %s", exc)
        return None


def delete() -> None:
    if config.CREDENTIALS_PATH.exists():
        config.CREDENTIALS_PATH.unlink()
