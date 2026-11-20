"""Tests de app_rewards (check-in y Leer para ganar por la API de la app de Bing)."""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib

import app_rewards as app  # noqa: E402
import verify  # noqa: E402

# Promos reales del panel de la app (dapi/me), 2026-11-14, recortadas.
CHECKIN = {"name": "Gamification_Sapphire_DailyCheckIn_Promotion", "attributes": {
    "day_1_points": "5", "day_6_points": "15", "progress": "5", "type": "checkin",
    "last_updated": "2024-05-20T22:58:57.2927274+00:00", "max": "11760", "complete": "False",
    "offerid": "Gamification_Sapphire_DailyCheckIn"}}
READ = {"name": "ENUS_readarticle3_30points", "attributes": {
    "type": "msnreadearn", "offerid": "ENUS_readarticle3_30points",
    "pointprogress": "0", "pointmax": "30", "complete": "False"}}
FLYOUT = {"name": "BingFlyout_Layout_DailyCheckIn", "attributes": {
    "type": "daily_checkin", "offerid": "DailyCheckIn_Parent_PuzzleOffer", "progress": "0"}}


def _dash(*promos, balance=27402):
    return {"balance": balance, "promotions": list(promos)}


def test_code_from_url():
    ok = "https://login.live.com/oauth20_desktop.srf?code=M.C5_abc&state=x"
    assert app.code_from_url(ok) == "M.C5_abc"
    assert app.code_from_url("https://login.live.com/oauth20_authorize.srf?code=no") == ""
    assert app.code_from_url("https://login.live.com/oauth20_desktop.srf?error=access_denied") == ""
    assert app.code_from_url("no es una url") == ""


def test_authorize_url_lleva_el_cliente_de_la_app():
    url = app.authorize_url("st")
    assert url.startswith(app.AUTHORIZE_URL + "?")
    assert "client_id=0000000040170455" in url and "state=st" in url
    assert "access_type=offline_access" in url


def test_promos_reales():
    dash = _dash(FLYOUT, CHECKIN, READ)
    assert app.find_promo(dash, "checkin") is CHECKIN      # no el del flyout
    assert app.find_promo(dash, "read") is READ
    assert app.find_promo({}, "read") is None
    assert app.read_progress(READ) == (0, 30)
    assert app.read_progress(CHECKIN) is None
    assert not app.is_complete(READ)
    hecho = {"attributes": {**READ["attributes"], "pointprogress": "30"}}
    assert app.is_complete(hecho)


def test_checked_in_today():
    hoy = datetime(2026, 11, 14, 12, 0)
    assert not app.checked_in_today(CHECKIN, hoy)
    # Mediodía UTC del día 14 cae en el 14 en cualquier zona entre -11 y +11.
    p = {"attributes": {"last_updated": "2026-11-14T12:00:00+00:00"}}
    assert app.checked_in_today(p, hoy)
    assert not app.checked_in_today({"attributes": {}}, hoy)


class FakeClient:
    """Simula la API: cada dashboard() devuelve el siguiente estado."""

    country = "ES"

    def __init__(self, states):
        self.states = list(states)
        self.sent = []

    async def dashboard(self, *, ios=False):
        return self.states.pop(0) if len(self.states) > 1 else self.states[0]

    async def submit(self, payload, *, ios=False):
        self.sent.append(payload)
        return {"code": 0}


def _no_sleep(monkeypatch):
    async def fast(_s):
        return None
    monkeypatch.setattr(app.asyncio, "sleep", fast)


def _read(prog):
    return {"name": READ["name"], "attributes": {**READ["attributes"], "pointprogress": str(prog)}}


def test_read_to_earn_hasta_completar(monkeypatch):
    _no_sleep(monkeypatch)
    states = [_dash(_read(0))] + [_dash(_read(3 * i)) for i in range(1, 11)]
    c = FakeClient(states)
    res = asyncio.run(app.read_to_earn(c, delay=(0, 0)))
    assert res["status"] == "ok" and res["progress"] == [30, 30] and res["points"] == 30
    assert len(c.sent) == 10 and all(p["type"] == app.TYPE_READ for p in c.sent)
    assert c.sent[0]["attributes"]["offerid"] == "ENUS_readarticle3_30points"


def test_read_to_earn_para_si_no_suma(monkeypatch):
    _no_sleep(monkeypatch)
    c = FakeClient([_dash(_read(6))])          # nunca sube
    res = asyncio.run(app.read_to_earn(c, delay=(0, 0)))
    assert res["status"] == "incomplete"
    assert len(c.sent) == app.MAX_READ_MISSES


def test_read_to_earn_ya_hecho_no_envia(monkeypatch):
    _no_sleep(monkeypatch)
    c = FakeClient([_dash(_read(30))])
    assert asyncio.run(app.read_to_earn(c))["status"] == "done"
    assert c.sent == []


def test_checkin_confirma_por_progreso(monkeypatch):
    _no_sleep(monkeypatch)
    after = {"name": CHECKIN["name"], "attributes": {**CHECKIN["attributes"], "progress": "6"}}
    c = FakeClient([_dash(CHECKIN), _dash(after)])
    res = asyncio.run(app.daily_checkin(c))
    assert res["status"] == "ok"
    assert c.sent[0]["type"] == app.TYPE_CHECKIN and c.sent[0]["channel"] == "SAIOS"


def _li(day):
    return {"name": "level_info", "attributes": {"hva_dailystreaks_mobile_days": str(day)}}


def test_checkin_confirma_por_racha_movil(monkeypatch):
    # Lo observado el 2026-11-14: la promo antigua no cambia, la racha móvil 1→2.
    _no_sleep(monkeypatch)
    c = FakeClient([_dash(CHECKIN, _li(1)), _dash(CHECKIN, _li(2))])
    res = asyncio.run(app.daily_checkin(c))
    assert res["status"] == "ok" and res["streak_day"] == 2
    c = FakeClient([_dash(CHECKIN, _li(7)), _dash(CHECKIN, _li(1))])   # reinicio de ciclo
    assert asyncio.run(app.daily_checkin(c))["status"] == "ok"


def test_checkin_ya_hecho_en_earn_no_envia(monkeypatch):
    _no_sleep(monkeypatch)
    c = FakeClient([_dash(CHECKIN, _li(2))])
    assert asyncio.run(app.daily_checkin(c, done_today=True))["status"] == "done"
    assert c.sent == []


def test_checkin_saldo_no_basta_si_hay_promo(monkeypatch):
    # Con otra actividad en marcha el saldo sube solo: no es prueba del check-in.
    _no_sleep(monkeypatch)
    c = FakeClient([_dash(CHECKIN, balance=100), _dash(CHECKIN, balance=130)])
    assert asyncio.run(app.daily_checkin(c))["status"] == "unconfirmed"


def test_en_paralelo_no_toca_el_navegador(monkeypatch):
    _no_sleep(monkeypatch)

    async def token(ctx=None):
        assert ctx is None            # sin contexto: nada de pestañas para revincular
        return "t"

    async def web(_ctx):
        raise AssertionError("no debe leer /earn mientras se busca")

    fake = FakeClient([_dash(CHECKIN, _li(3), _read(30))])
    monkeypatch.setattr(app, "access_token", token)
    monkeypatch.setattr(app, "checkin_done_on_web", web)
    monkeypatch.setattr(app, "AppClient", lambda http, tok: fake)
    res = asyncio.run(app.run_app_activities(done_today=True))
    assert res["checkin"]["status"] == "done" and res["read"]["status"] == "done"
    assert fake.sent == []


def test_run_lanza_la_app_en_paralelo_con_las_busquedas(monkeypatch):
    import contextlib

    import config
    import run

    events: list = []
    real_sleep = asyncio.sleep

    async def quick(*_a, **_k):
        await real_sleep(0)

    @contextlib.asynccontextmanager
    async def fake_launch(visible=True):
        yield object()

    async def true(*_a, **_k):
        return True

    async def none(*_a, **_k):
        return None

    async def user_info(_ctx):
        return {"level": 3, "points": 100, "pc_search": (0, 6, 3)}

    async def daily(_ctx):
        events.append("daily")
        return 3

    async def searches(_ctx, count=None, **_k):
        events.append("searches-start")
        for _ in range(5):
            await real_sleep(0)
        events.append("searches-end")
        return count

    async def verify_and_fix(_ctx, **kw):
        events.append(("verify", kw.get("app")))
        return {"extra_searches": 0}

    async def web(_ctx):
        events.append("web-check")
        return True

    async def app_run(context=None, *, done_today=None):
        assert context is None and done_today is True
        events.append("app-start")
        await real_sleep(0)
        events.append("app-end")
        return {"linked": True}

    monkeypatch.setattr(run.asyncio, "sleep", quick)
    monkeypatch.setattr(run, "sleep_jitter", none)
    monkeypatch.setattr(run, "launch", fake_launch)
    monkeypatch.setattr(run, "_ensure_session", true)
    monkeypatch.setattr(run.rewards_info, "get_user_info", user_info)
    monkeypatch.setattr(run, "run_daily", daily)
    monkeypatch.setattr(run, "run_searches", searches)
    monkeypatch.setattr(run, "run_visual_search", true)
    monkeypatch.setattr(run, "run_edge_minutes", none)
    monkeypatch.setattr(run, "claim_ready_points", none)
    monkeypatch.setattr(run.verify, "verify_and_fix", verify_and_fix)
    monkeypatch.setattr(app, "checkin_done_on_web", web)
    monkeypatch.setattr(app, "run_app_activities", app_run)
    monkeypatch.setattr(config, "APP_ACTIVITIES", True)
    monkeypatch.delenv("MSR_SEARCH_COUNT", raising=False)

    *_, info = asyncio.run(run._run(do_daily=True, do_searches=True, visible=False))

    assert events.index("web-check") < events.index("searches-start")   # /earn antes de buscar
    assert events.index("app-start") < events.index("searches-end")     # a la vez
    assert events.index("app-end") < events.index("searches-end")
    assert info["app"] == {"linked": True}
    assert ("verify", {"linked": True}) in events                         # llega al resumen


def test_desinstalar_borra_el_permiso_de_la_app(monkeypatch, tmp_path):
    import config
    import uninstall
    tok = tmp_path / "app_token.bin"
    tok.write_bytes(b"x")
    monkeypatch.setattr(config, "APP_TOKEN_PATH", tok)
    monkeypatch.setattr(config, "CREDENTIALS_PATH", tmp_path / "credentials.bin")
    uninstall._remove_credentials()
    assert not tok.exists()


def test_resumen_de_la_app_en_la_verificacion():
    assert "sin vincular" in verify.app_lines({"linked": False}, None)[0]
    lines = verify.app_lines({"linked": True, "checkin": {"status": "ok"},
                              "read": {"status": "ok", "progress": [30, 30]}}, (1, 1))
    assert lines[0].endswith("✓") and "30/30 ✓" in lines[1] and "1/1 ✓" in lines[2]


def test_resumen_para_el_panel():
    assert app.summarize(None) is None
    assert app.summarize({"linked": False}, today="2026-11-20") == {
        "date": "2026-11-20", "linked": False}
    res = {"linked": True,
           "checkin": {"status": "ok", "points": 0, "streak_day": 7},
           "read": {"status": "ok", "progress": [30, 30], "points": 30}}
    assert app.summarize(res, today="2026-11-20") == {
        "date": "2026-11-20", "linked": True, "checkin": "ok", "streak_day": 7,
        "read": [30, 30], "read_status": "ok"}
