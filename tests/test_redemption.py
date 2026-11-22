"""Canjes hechos a mano (también a mitad de una corrida): el bot los apunta en
el log y el panel los separa de lo ganado."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import date, datetime, timezone
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib

import config  # noqa: E402
import dashboard_api  # noqa: E402
import rewards_info  # noqa: E402

from panel import core  # noqa: E402

# userStatus.lastOrder real (22/11/2026), recortado.
_ORDER = {"id": "a1b2c3d4", "price": 10095, "status": "Shipped", "sku": "000413000025",
          "timestamp": "2026-11-22T12:45:24.3060731+00:00",
          "catalogItem": {"price": 10295, "title":
                          "Tarjeta regalo de XBOX en Microsoft Store por valor de 10 €"}}


def _dash(points=20070, order=_ORDER):
    us = {"availablePoints": points, "levelInfo": {"activeLevel": "newLevel3"}}
    if order is not None:
        us["lastOrder"] = order
    return {"userStatus": us}


def test_parse_last_order():
    o = dashboard_api.parse_last_order(_dash())
    assert o["price"] == 10095 and o["id"] == "a1b2c3d4"          # lo pagado, no catálogo
    assert o["title"].startswith("Tarjeta regalo de XBOX")
    assert o["at"] == datetime(2026, 11, 22, 12, 45, 24, 306073, tzinfo=timezone.utc)
    assert dashboard_api.parse_last_order(_dash(order=None)) is None
    assert dashboard_api.parse_last_order(_dash(order={"price": 0})) is None
    assert dashboard_api.parse_last_order(_dash(order={"price": 5, "timestamp": "x"})) is None
    assert dashboard_api.parse_user_info(_dash())["last_order"]["price"] == 10095


def test_redemption_since():
    o = dashboard_api.parse_last_order(_dash())
    antes = datetime(2026, 11, 22, 12, 18, tzinfo=timezone.utc)
    despues = datetime(2026, 11, 22, 13, 11, tzinfo=timezone.utc)
    assert rewards_info.redemption_since(o, antes) is o
    assert rewards_info.redemption_since(o, despues) is None       # ya contado
    assert rewards_info.redemption_since(o, None) is None          # sin lectura previa
    assert rewards_info.redemption_since(o, antes, logged_id="a1b2c3d4") is None
    assert rewards_info.redemption_since(None, antes) is None


def test_get_user_info_apunta_el_canje_antes_del_saldo(monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)

    async def fetch(_ctx, navigate=True):
        return _dash()
    monkeypatch.setattr(dashboard_api, "fetch_dashboard", fetch)
    (tmp_path / rewards_info.SEEN_FILE).write_text(
        json.dumps({"at": "2026-11-22T12:18:21+00:00"}), encoding="utf-8")
    with caplog.at_level(logging.INFO, logger="rewards_info"):
        info = asyncio.run(rewards_info.get_user_info(None))
    assert info["redeemed"]["price"] == 10095
    msgs = [r.getMessage() for r in caplog.records]
    assert msgs[0].startswith("canje en Rewards: 10095 pts (Tarjeta regalo de XBOX")
    assert "puntos=20070" in msgs[1]
    # La siguiente lectura no lo vuelve a apuntar.
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="rewards_info"):
        again = asyncio.run(rewards_info.get_user_info(None))
    assert again["redeemed"] is None
    assert not any("canje" in r.getMessage() for r in caplog.records)


def test_primera_lectura_no_inventa_canjes(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)

    async def fetch(_ctx, navigate=True):
        return _dash()
    monkeypatch.setattr(dashboard_api, "fetch_dashboard", fetch)
    assert asyncio.run(rewards_info.get_user_info(None))["redeemed"] is None
    assert (tmp_path / rewards_info.SEEN_FILE).is_file()


# --- Panel ----------------------------------------------------------------
_LOG_CANJE = (
    "13:18:21 [rewards_info] INFO: nivel=3 (Nivel Oro) puntos=29728 [API]\n"
    "13:18:21 [run] INFO: estado cuenta: nivel=3 puntos=29728 fuente=api\n"
    "14:11:15 [rewards_info] INFO: canje en Rewards: 10095 pts (Tarjeta regalo de XBOX"
    " en Microsoft Store por valor de 10 €, 22/11 13:45)\n"
    "14:11:15 [rewards_info] INFO: nivel=3 (Nivel Oro) puntos=20070 [API]\n"
    "14:11:15 [run] INFO: puntos: 29728 → 20070 (ganados hoy en esta sesión: +437;"
    " canje de 10095)\n"
)


def test_log_con_canje_separa_ganado_y_canjeado():
    assert core.parse_log_balances(_LOG_CANJE) == [29728, 29728, -10095, 20070, 20070]
    h = core.points_history({"2026-11-21": [29728],
                             "2026-11-22": core.parse_log_balances(_LOG_CANJE)},
                            today=date(2026, 11, 22), days=3)
    assert h[-1]["gained"] == 437 and h[-1]["redeemed"] == 10095
    assert h[-1]["balance"] == 20070
    assert core.history_day_text(h[-1], today=date(2026, 11, 22)) == \
        f"Hoy · {core.format_delta(437)} pts · canje de 10.095"


def test_log_sin_linea_de_canje_sigue_como_antes():
    h = core.points_history({"2026-11-22": [29728, 20070]}, today=date(2026, 11, 22), days=1)
    assert h[-1]["gained"] == 0 and h[-1]["redeemed"] == 9658


def test_session_gain_suma_el_canje():
    lr = {"points_before": 29728, "points_after": 20070, "redeemed": 10095}
    assert core.session_gain(lr) == 437
    assert core.session_gain({"points_before": 100, "points_after": 150}) == 50
    assert core.session_gain({"points_after": 150}) is None
    today = date.today().isoformat()
    kpi = core.kpi_tiles({**lr, "last_completed": today,
                          "updated_at": datetime.now().astimezone().isoformat()},
                         today=today)
    gained = next(t for t in kpi if t["key"] == "gained")
    assert gained["value"] == core.format_delta(437)
