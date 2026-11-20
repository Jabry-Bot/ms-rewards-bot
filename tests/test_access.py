"""Código de acceso por minuto y activación del equipo (common/access.py)."""
from __future__ import annotations

import hashlib
import logging
import secrets
import sys
from datetime import datetime
from pathlib import Path

import pytest

from common import access

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib


@pytest.fixture
def table(monkeypatch):
    """Tabla de 60 códigos de prueba con scrypt barato (los reales no se conocen)."""
    monkeypatch.setattr(access, "_N", 2 ** 4)
    codes, rows = [], []
    for _ in range(60):
        code = "".join(secrets.choice("ABCDEFGHJKMNPQRSTUVWXYZ23456789") for _ in range(6))
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(code.encode(), salt=salt, n=2 ** 4, r=8, p=1, dklen=32)
        codes.append(code)
        rows.append(salt.hex() + dk.hex())
    return codes, tuple(rows)


def test_tabla_real_bien_formada():
    assert len(access._TABLE) == 60
    assert all(len(r) == 96 and int(r, 16) >= 0 for r in access._TABLE)


def test_normalize():
    assert access.normalize(" ab-c 12x ") == "ABC12X"
    assert access.normalize("") == ""


def test_cada_codigo_vale_en_su_minuto(table):
    codes, rows = table
    at = datetime(2026, 11, 20, 13, 7)
    assert access.check(codes[7], at, rows)
    assert access.check(codes[7].lower(), at, rows)            # da igual mayúsculas
    assert access.check(codes[6], at, rows)                     # margen: minuto anterior
    assert access.check(codes[8], at, rows)                     # margen: minuto siguiente
    assert not access.check(codes[9], at, rows)                 # otro minuto: no
    assert access.check(codes[59], datetime(2026, 11, 20, 14, 0), rows)   # 13:59 → 14:00
    assert not access.check("ZZZZZ", at, rows)                  # longitud incorrecta


def test_linea_de_estado():
    line = access.status_line(datetime(2026, 11, 20, 9, 5))
    assert line.startswith("20/11/2026 09:05")
    assert "No tienes activada la contraseña de los servidores" in line


def test_activacion_ida_y_vuelta(tmp_path):
    assert not access.is_activated(tmp_path)
    access.activate(tmp_path)
    assert access.is_activated(tmp_path)


def test_activacion_manipulada_o_de_otro_equipo(tmp_path, monkeypatch):
    (tmp_path / access.ACCESS_FILE).write_bytes(b"no es dpapi")
    assert not access.is_activated(tmp_path)
    access.activate(tmp_path)
    monkeypatch.setattr(access, "_host", lambda: "otro-pc|otro")
    assert not access.is_activated(tmp_path)


def test_el_bot_no_corre_sin_activar(tmp_path, monkeypatch):
    import config
    import run
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    log = logging.getLogger("t")
    assert run.access_granted(log) is False
    access.activate(tmp_path)
    assert run.access_granted(log) is True
