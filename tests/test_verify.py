"""Tests de la verificación final (verify.py) y helpers de la API que usa."""
from __future__ import annotations

import sys
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib

import dashboard_api  # noqa: E402
import verify  # noqa: E402
from queries import generate_queries  # noqa: E402

from panel import core  # noqa: E402

# Texto real de rewards.bing.com/earn (sección Rachas), 2026-11-12.
EARN_TEXT = """
Racha diaria
154
Más información
Rachas
Reinicio de las rachas diarias dentro de 3 h
BONIFICACIÓN de 1000
Racha de Búsqueda de Bing
Día 7 de una racha de 7 días completada.
100
Día 7/7
Búsqueda: 1/1
Racha de conjunto diario
Día 2 de una racha de 7 días completada.
Completa el siguiente día para ganar 30 puntos
30
Día 2/7
Actividad: 3/3
Racha de navegación en Edge
Día 2 de una racha de 7 días completada.
Completa el siguiente día para ganar 20 puntos
10
Día 2/7
Minutos: 30/30
Racha de la aplicación Bing
Día 0 de una racha de 7 días completada.
Completa el siguiente día para ganar 5 puntos
+5
Día 1/7
Check-in: 0/1
Racha de búsqueda visual
Día 4 de una racha de 7 días completada.
Completa el siguiente día para ganar 5 puntos
5
Día 4/7
Actividad: 0/1
Actividades de subida de nivel
5/2
"""


def test_parse_streaks_texto_real():
    s = verify.parse_streaks(EARN_TEXT)
    assert s["search"] == (1, 1)
    assert s["daily_set"] == (3, 3)
    assert s["edge"] == (30, 30)
    assert s["app"] == (0, 1)        # móvil: se lee pero no se verifica
    assert s["visual"] == (0, 1)


def test_parse_streaks_vacio():
    assert verify.parse_streaks("") == {}
    assert verify.parse_streaks("Día 7/7\n5/2") == {}


def test_searches_needed():
    assert verify.searches_needed((108, 120, 3)) == 5        # 4 + 1 de margen
    assert verify.searches_needed((0, 120, 3), margin=2) == 42
    assert verify.searches_needed((120, 120, 3)) == 0
    assert verify.searches_needed(None) is None


def _dash(prog, mx, desc="Gana hasta 120 puntos por día, 3 puntos por búsqueda"):
    return {"userStatus": {"counters": {"pcSearch": [
        {"pointProgress": prog, "pointProgressMax": mx, "attributes": {"description": desc}}
    ]}}}


def test_parse_pc_search():
    assert dashboard_api.parse_pc_search(_dash(108, 120)) == (108, 120, 3)
    assert dashboard_api.parse_pc_search(_dash(10, 50, "5 puntos por búsqueda")) == (10, 50, 5)
    assert dashboard_api.parse_pc_search({}) is None
    assert dashboard_api.parse_pc_search(_dash(0, 0)) is None


def test_daily_set_status():
    from datetime import date
    t = date.today()
    key = f"{t.month:02d}/{t.day:02d}/{t.year}"
    dash = {"dailySetPromotions": {key: [
        {"complete": True}, {"complete": False}, {"complete": True}]}}
    assert dashboard_api.daily_set_status(dash) == (2, 3)
    assert dashboard_api.daily_set_status({}) is None


def test_busquedas_extra_no_repiten_consultas():
    # Las extra se piden con offset: continúan la lista del día, no la repiten.
    primeras = generate_queries(34)
    extra = generate_queries(34 + 6)[34:]
    assert generate_queries(40)[:34] == primeras
    assert len(extra) == 6
    assert not set(extra) & set(primeras)


def test_incomplete_runs(monkeypatch, tmp_path):
    import config
    import runstate

    monkeypatch.setattr(config, "LAST_RUN_PATH", tmp_path / "last_run.json")
    assert runstate.incomplete_runs_today() == 0
    runstate.mark_incomplete(30, 3)
    runstate.mark_incomplete(5, 1)
    assert runstate.incomplete_runs_today() == 2
    assert runstate.read()["status"] == "incomplete"
    assert not runstate.completed_today()


def test_panel_muestra_verificacion():
    ok = core.format_status({"status": "ok", "verification": {"ok": True}})
    assert "todo acreditado" in ok
    mal = core.format_status({"status": "incomplete",
                              "verification": {"ok": False, "retry": ["búsquedas de PC"]}})
    assert "Incompleto" in mal and "búsquedas de PC" in mal
