"""
Lectura/escritura del estado de ejecución diario (idempotencia).

state/last_run.json sigue este formato:
    {
        "user_id": "jabri",
        "last_completed": "2026-06-01",
        "searches_done": 32,
        "daily_done": 5,
        "status": "ok" | "needs_relogin" | "selectors_broken" | "error",
        "updated_at": "2026-06-01T11:42:18+02:00"
    }
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

import config

log = logging.getLogger("runstate")


def _today() -> str:
    return _dt.date.today().isoformat()


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def read() -> dict[str, Any]:
    if not config.LAST_RUN_PATH.exists():
        return {}
    try:
        with config.LAST_RUN_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("no se pudo leer %s: %s", config.LAST_RUN_PATH, exc)
        return {}


def write(**fields: Any) -> None:
    data = read()
    data.update(fields)
    data["user_id"] = config.USER_ID
    data["updated_at"] = _now()
    try:
        with config.LAST_RUN_PATH.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.warning("no se pudo escribir %s: %s", config.LAST_RUN_PATH, exc)


def completed_today() -> bool:
    return read().get("last_completed") == _today()


def mark_completed(searches_done: int, daily_done: int, status: str = "ok") -> None:
    write(
        last_completed=_today(),
        searches_done=searches_done,
        daily_done=daily_done,
        status=status,
    )


def mark_needs_relogin() -> None:
    write(status="needs_relogin")


def mark_selectors_broken(broken_keys: list[str]) -> None:
    write(status="selectors_broken", broken_keys=broken_keys)
