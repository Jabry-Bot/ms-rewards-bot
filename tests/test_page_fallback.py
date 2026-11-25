"""Respaldo cuando /api/getuserinfo no responde (cuentas en las que la API ya no
va): los datos se leen del payload RSC de la página /earn."""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib

import config  # noqa: E402
import dashboard_api  # noqa: E402

from panel import core  # noqa: E402


def _rsc_html(rows: dict[str, object]) -> str:
    """HTML con un payload RSC como el de Next.js: filas "id:JSON" repartidas
    en varios self.__next_f.push (como en la web real)."""
    text = "".join(f"{k}:{json.dumps(v, ensure_ascii=False)}\n" for k, v in rows.items())
    half = len(text) // 2
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(part)}])</script>"
                     for part in (text[:half], text[half:]))
    return f"<html><body><div>Ganar</div>{pushes}</body></html>"


def _daily(n, done, date="11/25/2026"):
    return {"date": date, "offerId": f"Global_DailySet_20261125_Child{n}",
            "destination": f"https://www.bing.com/search?q=q{n}&rnoreward=1",
            "hash": "h" * 8, "isCompleted": done, "isLocked": "$undefined",
            "points": 10, "title": f"Diario {n}"}


EARN = _rsc_html({
    "49": ["$", "$L75", None, {"balance": 4321, "level": 1}],
    "82": ["$", "$L9e", None, {"type": "pointbreakdown", "model": {
        "pointsCounters": {"dailyOffer": 10, "pc": {"max": 60, "progress": 15,
                                                   "originalMax": 30}}}}],
    "5a": ["$", "div", None, {"children": [_daily(1, True), _daily(2, False),
                                           _daily(3, "$undefined")]}],
    # El conjunto diario sale repetido en otra parte de la página.
    "5b": {"offerId": "Global_DailySet_20261125_Child1", "isCompleted": True, "hash": "x"},
    "6a": {"offerId": "ENES_more_1", "title": "Cita del día", "points": 5,
           "destination": "https://www.bing.com/search?q=cita", "isCompleted": False},
    "6b": {"offerId": "ENES_locked", "title": "Se desbloquea mañana", "points": 10,
           "destination": "https://www.bing.com/search?q=x", "isLocked": True,
           "isCompleted": False},
    "6c": {"offerId": "WW_banner", "title": "Sorteo", "ctaUrl": "https://aka.ms/x",
           "hash": "y"},
})


def test_dashboard_from_rsc_tiene_la_forma_de_la_api():
    d = dashboard_api.dashboard_from_rsc(EARN)
    ui = dashboard_api.parse_user_info(d)
    assert ui["points"] == 4321 and ui["level"] == 1 and ui["source"] == "page"
    assert ui["pc_search"] == (15, 60, 3)
    assert set(d["dailySetPromotions"]) == {"11/25/2026"}
    acts = dashboard_api.pending_activities(d)
    assert [(a.source, a.title) for a in acts] == [
        ("daily", "Diario 2"), ("daily", "Diario 3"), ("more", "Cita del día")]
    assert acts[0].destination_url.startswith("https://www.bing.com/search?q=q2")
    # La bloqueada no se intenta; el banner sin destino ni aparece.
    titles = [m["title"] for m in d["morePromotions"]]
    assert "Sorteo" not in titles
    locked = next(m for m in d["morePromotions"] if m["offerId"] == "ENES_locked")
    assert locked["pointProgressMax"] == 0


def test_dashboard_from_rsc_sin_datos():
    assert dashboard_api.dashboard_from_rsc("<html>sin sesión</html>") == {}
    assert dashboard_api.dashboard_from_rsc("") == {}
    roto = '<script>self.__next_f.push([1,"0:{no es json\\n"])</script>'
    assert dashboard_api.dashboard_from_rsc(roto) == {}


# --- fetch_dashboard: API caída → página ---------------------------------
class _Page:
    url = "https://rewards.bing.com/dashboard"

    def __init__(self, res):
        self.res = res

    async def evaluate(self, _js):
        return self.res


class _Resp:
    ok = True

    def __init__(self, text):
        self._text = text

    async def text(self):
        return self._text


class _Req:
    def __init__(self, text):
        self.text, self.calls = text, 0

    async def get(self, url, **_k):
        self.calls += 1
        assert url == dashboard_api.EARN_URL
        return _Resp(self.text)


class _Ctx:
    def __init__(self, res, html):
        self.pages = [_Page(res)]
        self.request = _Req(html)


def test_fetch_dashboard_usa_la_api_si_responde():
    ctx = _Ctx({"status": 200, "data": {"dashboard": {"userStatus": {"availablePoints": 5}}}},
               EARN)
    d = asyncio.run(dashboard_api.fetch_dashboard(ctx, navigate=False))
    assert d["userStatus"]["availablePoints"] == 5 and ctx.request.calls == 0


def test_fetch_dashboard_si_la_api_falla_lee_la_pagina(monkeypatch, caplog):
    monkeypatch.setattr(dashboard_api, "_fallback_noted", False)
    ctx = _Ctx({"status": 404, "data": None}, EARN)
    with caplog.at_level(logging.INFO, logger="dashboard_api"):
        d = asyncio.run(dashboard_api.fetch_dashboard(ctx, navigate=False))
        asyncio.run(dashboard_api.fetch_dashboard(ctx, navigate=False))
    assert d["userStatus"]["availablePoints"] == 4321 and d["source"] == "page"
    msgs = [r.getMessage() for r in caplog.records]
    assert len(msgs) == 1 and "HTTP 404" in msgs[0] and "/earn" in msgs[0]


def test_fetch_dashboard_sin_api_ni_pagina_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(dashboard_api, "_fallback_noted", False)

    async def nada(_ctx):
        return {}
    monkeypatch.setattr(dashboard_api, "fetch_dashboard_from_page", nada)
    ctx = _Ctx({"status": 0, "data": None, "error": "TypeError"}, "")
    assert asyncio.run(dashboard_api.fetch_dashboard(ctx, navigate=False)) == {}


# --- Perfil del navegador --------------------------------------------------
def test_perfil_no_salta_a_uno_vacio(tmp_path):
    legacy = tmp_path / "MsRewardsCDP"
    # Instalación nueva: el perfil por usuario se crea DENTRO de la carpeta.
    assert config.default_profile(legacy, "PC1") == legacy / "PC1"
    (legacy / "PC1").mkdir(parents=True)
    (legacy / "PC1" / "Local State").write_text("{}")
    assert config.default_profile(legacy, "PC1") == legacy / "PC1"   # antes: legacy
    # Instalación antigua: la carpeta ya es un perfil y se sigue usando.
    (legacy / "Local State").write_text("{}")
    assert config.default_profile(legacy, "PC1") == legacy


# --- Panel: sin datos no es "Todo hecho" -----------------------------------
def test_panel_no_dice_todo_hecho_si_no_pudo_comprobar():
    today = "2026-11-25"
    lr = {"status": "ok", "last_completed": today, "updated_at": f"{today}T17:10:54+01:00",
          "verification": {"retry": [], "unknown": ["búsquedas de PC", "conjunto diario"],
                           "ok": True}}
    s = core.day_summary(lr, {"state": "Ready", "next_run": None}, today=today)
    assert s["tone"] == "warn" and s["title"] == "Hecho, sin poder comprobarlo todo"
    assert "búsquedas de PC, conjunto diario" in s["subtitle"]
    lr["verification"]["unknown"] = []
    assert core.day_summary(lr, None, today=today)["title"] == "Todo hecho por hoy"
