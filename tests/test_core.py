"""Tests de lógica pura de panel/core.py.

No importan GUI ni el bot: solo `from panel import core`. Se ejecutan con
pytest desde la raíz del repo (gracias a panel/__init__.py).
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from panel import core


# --- Constantes y rutas --------------------------------------------------
def test_task_name():
    assert core.TASK_NAME == "MsRewardsBot"


def test_paths_son_path():
    for p in (
        core.ROOT,
        core.REWARDS_DIR,
        core.VENV_PY,
        core.RUN_PY,
        core.SWITCH_PY,
        core.LAST_RUN_PATH,
        core.LOG_DIR,
    ):
        assert isinstance(p, Path)


def test_rutas_relativas_al_repo():
    assert core.REWARDS_DIR == core.ROOT / "ms_rewards"
    assert core.RUN_PY == core.REWARDS_DIR / "run.py"
    assert core.SWITCH_PY == core.REWARDS_DIR / "switch_account.py"


# --- ACTIONS / Action ----------------------------------------------------
def test_actions_claves():
    assert set(core.ACTIONS) >= {"run_all", "daily", "searches", "login", "kill"}


def test_action_es_dataclass_con_campos():
    a = core.ACTIONS["run_all"]
    assert a.id == "run_all"
    assert isinstance(a.label, str) and a.label
    assert isinstance(a.flags, tuple)
    assert isinstance(a.description, str)
    assert isinstance(a.confirm, bool)


def test_action_kill_confirm():
    assert core.ACTIONS["kill"].confirm is True
    assert core.ACTIONS["run_all"].confirm is False


# --- venv_ready ----------------------------------------------------------
def test_venv_ready_devuelve_bool():
    assert isinstance(core.venv_ready(), bool)


# --- build_run_command ---------------------------------------------------
def test_build_run_command_forma():
    cmd = core.build_run_command("run_all")
    assert isinstance(cmd, list)
    assert cmd[0] == str(core.VENV_PY)
    assert cmd[1] == str(core.RUN_PY)
    assert cmd[2:] == ["--force"]


def test_build_run_command_flags_por_accion():
    assert core.build_run_command("daily")[2:] == ["--daily", "--force"]
    assert core.build_run_command("searches")[2:] == ["--searches", "--force"]
    assert core.build_run_command("login")[2:] == ["--setup"]
    assert core.build_run_command("kill")[2:] == ["--kill"]


def test_build_run_command_accion_desconocida():
    import pytest

    with pytest.raises(KeyError):
        core.build_run_command("no_existe")


def test_build_run_command_hidden_añade_flag():
    cmd = core.build_run_command("run_all", hidden=True)
    assert cmd[2:] == ["--force", "--hidden"]


def test_build_run_command_visible_por_defecto():
    assert "--hidden" not in core.build_run_command("run_all")
    assert "--hidden" not in core.build_run_command("run_all", hidden=False)


def test_prompt_visibility_solo_en_corridas_del_bot():
    # Las corridas que abren el navegador ofrecen elegir visible/oculto.
    assert core.ACTIONS["run_all"].prompt_visibility is True
    assert core.ACTIONS["daily"].prompt_visibility is True
    assert core.ACTIONS["searches"].prompt_visibility is True
    # login debe ser visible sí o sí; kill no abre navegador.
    assert core.ACTIONS["login"].prompt_visibility is False
    assert core.ACTIONS["kill"].prompt_visibility is False


# --- build_switch_command ------------------------------------------------
def test_build_switch_command():
    # Modo navegador: borra la sesión y abre el login visible; sin credenciales.
    assert core.build_switch_command() == [str(core.VENV_PY), str(core.SWITCH_PY),
                                           "--browser"]


# --- WINUTIL_PY ----------------------------------------------------------
def test_winutil_py_ruta():
    assert isinstance(core.WINUTIL_PY, Path)
    assert core.WINUTIL_PY.name == "winutil.py"
    assert core.WINUTIL_PY == core.REWARDS_DIR / "winutil.py"


# --- build_task_command --------------------------------------------------
def test_build_task_command_install():
    assert core.build_task_command(True) == [
        str(core.VENV_PY),
        str(core.WINUTIL_PY),
        "task-install",
    ]


def test_build_task_command_uninstall():
    assert core.build_task_command(False) == [
        str(core.VENV_PY),
        str(core.WINUTIL_PY),
        "task-uninstall",
    ]


# --- build_task_query_command --------------------------------------------
def test_build_task_query_command():
    assert core.build_task_query_command() == [
        str(core.VENV_PY),
        str(core.WINUTIL_PY),
        "task-status",
    ]


# --- icon_path -----------------------------------------------------------
def test_icon_path_returns_path_or_none():
    result = core.icon_path()
    assert result is None or isinstance(result, Path)


# --- parse_task_query ----------------------------------------------------
def test_parse_task_query_vacio():
    assert core.parse_task_query("") == {}
    assert core.parse_task_query("{}") == {}


def test_parse_task_query_basura():
    assert core.parse_task_query("esto no es json") == {}


def test_parse_task_query_json_valido():
    data = {"state": "Ready", "next_run": "mañana"}
    assert core.parse_task_query(json.dumps(data)) == data


# --- read_last_run -------------------------------------------------------
def test_read_last_run_inexistente(tmp_path, monkeypatch):
    monkeypatch.setattr(core, "LAST_RUN_PATH", tmp_path / "last_run.json")
    assert core.read_last_run() == {}


def test_read_last_run_valido(tmp_path, monkeypatch):
    p = tmp_path / "last_run.json"
    payload = {"status": "ok", "points_after": 100}
    p.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(core, "LAST_RUN_PATH", p)
    assert core.read_last_run() == payload


def test_read_last_run_corrupto(tmp_path, monkeypatch):
    p = tmp_path / "last_run.json"
    p.write_text("{ json roto", encoding="utf-8")
    monkeypatch.setattr(core, "LAST_RUN_PATH", p)
    assert core.read_last_run() == {}


# --- status_style --------------------------------------------------------
def test_status_style_mapeos():
    assert core.status_style({"status": "ok"})[0] == "Completado"
    assert core.status_style({"status": "needs_relogin"})[0] == "Requiere login"
    assert core.status_style({"status": "error"})[0] == "Error"
    assert core.status_style({})[0] == "Desconocido"
    assert core.status_style({"status": "loquesea"})[0] == "Desconocido"


def test_status_style_color_hex():
    for ls in ({"status": "ok"}, {"status": "error"}, {}):
        label, color = core.status_style(ls)
        assert color.startswith("#")


# --- completed_today -----------------------------------------------------
def test_completed_today_hoy():
    assert core.completed_today({"last_completed": date.today().isoformat()}) is True


def test_completed_today_ayer_o_ausente():
    ayer = (date.today() - timedelta(days=1)).isoformat()
    assert core.completed_today({"last_completed": ayer}) is False
    assert core.completed_today({}) is False


# --- format_status -------------------------------------------------------
def test_format_status_incluye_etiqueta():
    out = core.format_status({"status": "ok"})
    assert "Completado" in out


def test_format_status_puntos_con_delta():
    out = core.format_status({"points_before": 100, "points_after": 130})
    assert "100" in out and "130" in out
    assert "+30" in out


def test_format_status_tarea_no_registrada():
    out = core.format_status({"status": "ok"}, task=None)
    assert "NO registrada" in out


def test_format_status_tarea_registrada():
    out = core.format_status({"status": "ok"}, task={"state": "Ready"})
    assert "registrada" in out
    assert "NO registrada" not in out


# --- today_log_path ------------------------------------------------------
def test_today_log_path():
    p = core.today_log_path()
    assert p.name == f"{date.today():%Y%m%d}.log"
    assert p.parent == core.LOG_DIR


# --- UNINSTALL_OPTIONS ---------------------------------------------------
def test_uninstall_options_estructura():
    assert isinstance(core.UNINSTALL_OPTIONS, list)
    keys = set()
    for item in core.UNINSTALL_OPTIONS:
        assert isinstance(item, tuple) and len(item) == 3
        key, label, default = item
        assert isinstance(key, str) and key
        assert isinstance(label, str) and label
        assert isinstance(default, bool)
        keys.add(key)
    assert {"task", "state", "credentials", "profile", "env"} <= keys


# --- UNINSTALL_PY --------------------------------------------------------
def test_uninstall_py_ruta():
    assert isinstance(core.UNINSTALL_PY, Path)
    assert core.UNINSTALL_PY.name == "uninstall.py"
    assert core.UNINSTALL_PY == core.REWARDS_DIR / "uninstall.py"


# --- build_uninstall_command ---------------------------------------------
def test_build_uninstall_command_vacio():
    assert core.build_uninstall_command([]) == [
        str(core.VENV_PY),
        str(core.UNINSTALL_PY),
    ]


def test_build_uninstall_command_con_flags():
    cmd = core.build_uninstall_command(["task", "state"])
    assert cmd[0] == str(core.VENV_PY)
    assert cmd[1] == str(core.UNINSTALL_PY)
    assert cmd[-2:] == ["--task", "--state"]


def test_build_uninstall_command_opcion_invalida():
    import pytest

    with pytest.raises(ValueError):
        core.build_uninstall_command(["bogus"])


# --- build_update_command ------------------------------------------------
def test_build_update_command():
    cmd = core.build_update_command()
    assert cmd == [str(core.VENV_PY), str(core.RUN_PY), "--update-only"]
    assert cmd[0] == str(core.VENV_PY)
    assert cmd[-1] == "--update-only"


# --- auto-actualización del .exe -----------------------------------------
def test_vtuple_ordering():
    assert core._vtuple("2.10.0") > core._vtuple("2.9.9")
    assert core._vtuple("2.6.0") < core._vtuple("2.6.1")
    assert core._vtuple("") == (0,)


def test_self_update_false_when_not_frozen(monkeypatch):
    monkeypatch.setattr(core, "is_frozen", lambda: False)
    assert core.self_update_available() is False


def test_self_update_true_when_disk_newer(monkeypatch, tmp_path):
    vf = tmp_path / "VERSION"
    vf.write_text("9.9.9", encoding="utf-8")
    monkeypatch.setattr(core, "is_frozen", lambda: True)
    monkeypatch.setattr(core, "build_version", lambda: "1.0.0")
    monkeypatch.setattr(core, "VERSION_FILE", vf)
    assert core.available_version() == "9.9.9"
    assert core.self_update_available() is True
    vf.write_text("1.0.0", encoding="utf-8")
    assert core.self_update_available() is False


def test_panel_exe_url():
    assert core.PANEL_EXE_URL.startswith("https://")
    assert core.PANEL_EXE_URL.endswith("MsRewardsPanel.exe")


def test_swap_exe(tmp_path, monkeypatch):
    exe = tmp_path / "MsRewardsPanel.exe"
    exe.write_bytes(b"OLD")
    new = tmp_path / "MsRewardsPanel.new.exe"
    new.write_bytes(b"NEW")
    monkeypatch.setattr(core.sys, "executable", str(exe))
    core.swap_exe(new)
    assert exe.read_bytes() == b"NEW"
    assert (tmp_path / "MsRewardsPanel.old.exe").exists()
    assert not new.exists()


# --- App de Bing (check-in + Leer para ganar) ----------------------------
def test_accion_vincular_app():
    a = core.ACTIONS["link_app"]
    assert a.flags == ("--link-app",)
    assert a.prompt_visibility is False and a.confirm is False
    assert core.build_run_command("link_app")[-1] == "--link-app"


def _app_last_run(**app):
    return {"status": "ok", "app": {"date": "2026-11-20", "linked": True, **app}}


def test_app_status_sin_vincular(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    st = core.app_status({}, linked=False, today="2026-11-20")
    assert st["state"] == "unlinked" and st["label"] == "Sin vincular"


def test_app_status_pendiente_si_no_hay_datos_de_hoy(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    assert core.app_status({}, linked=True, today="2026-11-20")["state"] == "pending"
    viejo = {"app": {"date": "2026-11-19", "linked": True, "checkin": "ok",
                     "read": [30, 30], "read_status": "ok"}}
    st = core.app_status(viejo, linked=True, today="2026-11-20")
    assert st["state"] == "pending" and st["read"] is None


def test_app_status_hecho_hoy(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    lr = _app_last_run(checkin="ok", streak_day=7, read=[30, 30], read_status="ok")
    st = core.app_status(lr, linked=True, today="2026-11-20")
    assert st["state"] == "done" and st["read"] == (30, 30) and st["streak_day"] == 7
    assert any("racha día 7" in line for line in st["lines"])
    assert any("30/30 ✓" in line for line in st["lines"])


def test_app_status_incompleto(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    lr = _app_last_run(checkin="unconfirmed", read=[12, 30], read_status="incomplete")
    assert core.app_status(lr, linked=True, today="2026-11-20")["state"] == "partial"


def test_app_status_revincular_si_el_permiso_fallo(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    lr = {"app": {"date": "2026-11-20", "linked": False}}
    assert core.app_status(lr, linked=True, today="2026-11-20")["state"] == "relink"


def test_app_status_desactivada(monkeypatch):
    monkeypatch.setenv("MSR_APP_ACTIVITIES", "0")
    assert core.app_status({}, linked=True, today="2026-11-20")["state"] == "disabled"


def test_app_linked_mira_el_permiso(tmp_path, monkeypatch):
    tok = tmp_path / "app_token.bin"
    monkeypatch.setattr(core, "APP_TOKEN_PATH", tok)
    assert core.app_linked() is False
    tok.write_bytes(b"x")
    assert core.app_linked() is True


def test_format_status_incluye_app(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "APP_TOKEN_PATH", tmp_path / "no.bin")
    assert "App de Bing: Sin vincular" in core.format_status({"status": "ok"})


# --- Presentación del dashboard -------------------------------------------
from datetime import datetime  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _entorno_limpio(monkeypatch):
    """Que las variables del usuario (MSR_*) no cambien el resultado."""
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    monkeypatch.delenv("MSR_BROWSER", raising=False)


HOY = "2026-11-20"
AHORA = datetime(2026, 11, 20, 11, 30)


def _lr_completo(**extra):
    lr = {
        "status": "ok", "level": 3, "points_before": 28901, "points_after": 29268,
        "last_completed": HOY, "searches_done": 42, "daily_done": 12,
        "visual_search_done": True,
        "verification": {"pc_search": [120, 120], "daily_set": [3, 3], "visual": [1, 1],
                         "edge": [30, 30], "app_streak": [1, 1], "retry": [],
                         "pending_punch": 8, "ok": True, "extra_searches": 0},
        "app": {"date": HOY, "linked": True, "checkin": "ok", "streak_day": 7,
                "read": [30, 30], "read_status": "ok"},
        "updated_at": f"{HOY}T10:04:30+01:00",
    }
    lr.update(extra)
    return lr


def test_action_text_quita_el_emoji():
    assert core.action_text(core.ACTIONS["run_all"]) == "Ejecutar ahora"
    assert core.action_text("📱  Vincular app de Bing") == "Vincular app de Bing"
    assert core.action_text("Sin icono") == "Sin icono"
    assert all(core.action_text(a)[0].isalnum() for a in core.ACTIONS.values())


def test_split_option_label():
    assert core.split_option_label("Quitar la tarea (deja de ejecutarse solo)") == (
        "Quitar la tarea", "deja de ejecutarse solo")
    assert core.split_option_label("Borrar las credenciales cifradas") == (
        "Borrar las credenciales cifradas", "")
    for _, label, _ in core.UNINSTALL_OPTIONS:
        main, _hint = core.split_option_label(label)
        assert main and "(" not in main


def test_format_points_y_delta():
    assert core.format_points(29268) == "29.268"
    assert core.format_points(1234567) == "1.234.567"
    assert core.format_points(0) == "0"
    assert core.format_points(None) == "—"
    assert core.format_points("x") == "—"
    assert core.format_delta(367) == "+367"
    assert core.format_delta(1234) == "+1.234"
    assert core.format_delta(-12) == "-12"
    assert core.format_delta(None) == "—"


def test_level_name():
    assert core.level_name(3) == "Oro"
    assert core.level_name(2) == "Plata"
    assert core.level_name(1) == "Miembro"
    assert core.level_name(None) == "—"
    assert core.level_name(7) == "Nivel 7"


def test_parse_when_formatos():
    assert core.parse_when("2026-11-20T10:04:30+01:00") == datetime(2026, 11, 20, 10, 4, 30)
    # pywin32 etiqueta como UTC la hora local del Programador: se ignora la zona.
    assert core.parse_when("2026-11-20 12:02:00+00:00") == datetime(2026, 11, 20, 12, 2)
    assert core.parse_when("1999-11-30 00:00:00") is None    # "nunca"
    assert core.parse_when("") is None
    assert core.parse_when(None) is None
    assert core.parse_when("basura") is None


def test_format_when_relativo():
    assert core.format_when("2026-11-20 12:02:00", AHORA) == "hoy, 12:02"
    assert core.format_when("2026-11-21 09:00:00", AHORA) == "mañana, 09:00"
    assert core.format_when("2026-11-19 22:00:00", AHORA) == "ayer, 22:00"
    assert core.format_when("2026-11-18 10:04:00", AHORA) == "18 nov, 10:04"
    assert core.format_when(None, AHORA) == "—"


def test_ago_text():
    assert core.ago_text("2026-11-20T11:29:40", AHORA) == "ahora mismo"
    assert core.ago_text("2026-11-20T11:25:00", AHORA) == "hace 5 min"
    assert core.ago_text("2026-11-20T09:00:00", AHORA) == "hace 2 h"
    assert core.ago_text("2026-11-19T11:00:00", AHORA) == "hace 1 día"
    assert core.ago_text("2026-11-17T11:00:00", AHORA) == "hace 3 días"
    assert core.ago_text(None, AHORA) == ""


def test_format_elapsed():
    assert core.format_elapsed(42) == "0:42"
    assert core.format_elapsed(754) == "12:34"
    assert core.format_elapsed(3723) == "1:02:03"
    assert core.format_elapsed(-5) == "0:00"


def test_today_label():
    from datetime import date
    assert core.today_label(date(2026, 11, 20)) == "Viernes, 20 nov"
    assert core.today_label(date(2026, 1, 5)) == "Lunes, 5 ene"


def test_ran_today_y_fecha_de_corrida():
    lr = _lr_completo()
    assert core.last_run_date(lr) == HOY
    assert core.ran_today(lr, HOY)
    assert not core.ran_today(lr, "2026-11-21")
    assert not core.ran_today({}, HOY)
    assert core.ran_today({"last_completed": HOY}, HOY)


def test_today_checklist_dia_completo():
    items = core.today_checklist(_lr_completo(), browser="edge", linked=True, today=HOY)
    assert [i["key"] for i in items] == ["pc_search", "daily_set", "visual", "edge", "app"]
    assert all(i["state"] == "ok" for i in items)
    assert items[0]["value"] == "120 / 120 pts"
    assert items[3]["value"] == "30 / 30 min"
    assert core.checklist_counts(items) == (5, 5)


def test_today_checklist_incompleto_con_progreso():
    lr = _lr_completo(status="incomplete", verification={
        "pc_search": [84, 120], "daily_set": [2, 3], "visual": [0, 1], "edge": [12, 30],
        "app_streak": [0, 1], "retry": ["búsquedas de PC"], "pending_punch": 0})
    items = {i["key"]: i for i in core.today_checklist(lr, browser="edge", linked=True,
                                                         today=HOY)}
    assert items["pc_search"]["state"] == "todo"
    assert items["pc_search"]["progress"] == 0.7
    assert items["visual"]["progress"] == 0.0


def test_today_checklist_edge_no_aplica_con_chrome():
    lr = _lr_completo()
    lr["verification"] = {**lr["verification"], "edge": [0, 30]}
    items = core.today_checklist(lr, browser="chrome", linked=True, today=HOY)
    edge = items[3]
    assert edge["state"] == "na" and edge["value"] == "Solo con Edge"
    assert core.checklist_counts(items) == (4, 4)     # "na" no cuenta


def test_today_checklist_sin_corrida_hoy_queda_pendiente():
    lr = _lr_completo(updated_at="2026-11-19T10:00:00+01:00", last_completed="2026-11-19")
    items = core.today_checklist(lr, browser="edge", linked=True, today=HOY)
    assert all(i["state"] == "pending" for i in items)
    vacio = core.today_checklist({}, browser="chrome", linked=False, today=HOY)
    assert vacio[3]["state"] == "na" and vacio[4]["state"] == "na"
    assert vacio[4]["value"] == "Sin vincular"


def test_today_checklist_ignora_verificacion_de_corrida_caida():
    # needs_relogin no reescribe la verificación: la que hay es de otro día.
    lr = _lr_completo(status="needs_relogin")
    items = core.today_checklist(lr, browser="edge", linked=True, today=HOY)
    assert all(i["state"] in ("pending",) for i in items[:4])
    assert core.checklist_notes(lr, HOY) == []


def test_today_checklist_sin_verificacion_usa_contadores():
    lr = _lr_completo()
    del lr["verification"]
    items = core.today_checklist(lr, browser="edge", linked=True, today=HOY)
    assert items[0]["state"] == "ok" and items[0]["value"] == "42"
    assert items[2]["value"] == "Hecho"
    assert items[3]["state"] == "unknown"


def test_today_checklist_app_revincular():
    lr = _lr_completo(app={"date": HOY, "linked": False})
    app = core.today_checklist(lr, browser="edge", linked=True, today=HOY)[4]
    assert app["state"] == "todo" and app["value"] == "Revincular"


def test_checklist_notes_y_subtitulo():
    lr = _lr_completo()
    lr["verification"] = {**lr["verification"], "retry": ["conjunto diario"],
                          "pending_punch": 1}
    notes = core.checklist_notes(lr, HOY)
    assert notes[0] == ("warn", "Se reintentará: conjunto diario")
    assert notes[1][0] == "info" and notes[1][1].startswith("1 promoción con tareas sin hacer")
    assert core.checklist_subtitle(lr, HOY) == "Comprobado en Rewards a las 10:04"
    assert core.checklist_subtitle({}, HOY) == "Se rellenará tras la primera corrida"


def test_day_summary_casos():
    task = {"state": "Ready", "next_run": "2026-11-21 09:00:00+00:00"}
    ok = core.day_summary(_lr_completo(), task, today=HOY, now=AHORA)
    assert ok["tone"] == "ok" and ok["title"] == "Todo hecho por hoy"
    assert "Mañana se ejecutará solo" in ok["subtitle"]
    assert core.day_summary({}, None, venv_ok=False, today=HOY)["tone"] == "error"
    assert core.day_summary({}, None, today=HOY)["title"] == "Todavía no se ha ejecutado"
    relog = core.day_summary(_lr_completo(status="needs_relogin"), task, today=HOY)
    assert relog["tone"] == "error" and "iniciar sesión" in relog["title"]
    viejo = _lr_completo(updated_at="2026-11-19T10:00:00", last_completed="2026-11-19")
    pend = core.day_summary(viejo, task, today=HOY, now=AHORA)
    assert pend["tone"] == "info" and "mañana, 09:00" in pend["subtitle"]


def test_day_summary_incompleto_dice_que_falta():
    lr = _lr_completo(status="incomplete", last_completed="2026-11-19")
    lr["verification"] = {**lr["verification"], "retry": ["búsquedas de PC"]}
    task = {"state": "Ready", "next_run": "2026-11-20 12:02:00+00:00"}
    s = core.day_summary(lr, task, today=HOY, now=AHORA)
    assert s["tone"] == "warn" and s["title"] == "Faltan cosas por hacer hoy"
    assert "Falta: búsquedas de PC" in s["subtitle"] and "hoy, 12:02" in s["subtitle"]


def test_kpi_tiles():
    tiles = {t["key"]: t for t in core.kpi_tiles(_lr_completo(), HOY, AHORA)}
    assert tiles["points"]["value"] == "29.268"
    assert tiles["gained"]["value"] == "+367" and tiles["gained"]["tone"] == "ok"
    assert tiles["gained"]["sub"] == "28.901 → 29.268"
    assert tiles["level"]["value"] == "Oro" and tiles["level"]["tone"] == "gold"
    assert tiles["last"]["value"] == "10:04" and tiles["last"]["sub"].startswith("hoy")
    vacio = core.kpi_tiles({}, HOY, AHORA)
    assert [t["value"] for t in vacio] == ["—", "—", "—", "—"]
    assert vacio[3]["sub"] == "Nunca"


def test_kpi_ganados_solo_si_la_corrida_es_de_hoy():
    viejo = _lr_completo(updated_at="2026-11-18T10:00:00", last_completed="2026-11-18")
    tiles = {t["key"]: t for t in core.kpi_tiles(viejo, HOY, AHORA)}
    assert tiles["gained"]["value"] == "—"
    assert tiles["last"]["value"] == "18 nov"


def test_task_summary():
    assert core.task_summary({})["state"] == "missing"
    ts = core.task_summary({"state": "Ready", "last_run": "2026-11-20 10:00:12+00:00",
                            "last_result": 0, "next_run": "2026-11-21 09:00:00+00:00"},
                           AHORA)
    assert ts["state"] == "ready" and ts["tone"] == "ok"
    assert ts["lines"] == ["Próxima: mañana, 09:00", "Última: hoy, 10:00 · OK"]
    nunca = core.task_summary({"state": "Ready", "last_run": "1999-11-30 00:00:00",
                               "last_result": 0x41303}, AHORA)
    assert "Última: nunca" in nunca["lines"]
    assert core.task_summary({"state": "Disabled"})["state"] == "disabled"
    assert core.task_summary({"state": "Running"})["tone"] == "info"


def test_app_rows(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    st = core.app_status(_lr_completo(), linked=True, today=HOY)
    checkin, read = core.app_rows(st)
    assert checkin["state"] == "ok" and checkin["value"] == "Racha: día 7"
    assert read["value"] == "30 / 30" and read["progress"] == 1.0
    pend = core.app_rows({"state": "pending"})
    assert [r["state"] for r in pend] == ["pending", "pending"]


def test_clean_log_line():
    assert core.clean_log_line("2026-11-20 10:04:30,123 [run] INFO: hola mundo") == "hola mundo"
    assert core.clean_log_line("2026-11-20 10:04:30,123 run INFO hola") == "hola"
    assert core.clean_log_line("2026-11-20 10:04:30 verify WARNING: x y") == "x y"
    assert core.clean_log_line("sin prefijo") == "sin prefijo"


def test_log_line_kind():
    assert core.log_line_kind("$ python run.py") == "cmd"
    assert core.log_line_kind("--- Ejecutar ahora ---") == "header"
    assert core.log_line_kind("2026 [x] ERROR: falló") == "error"
    assert core.log_line_kind("Traceback (most recent call last):") == "error"
    assert core.log_line_kind("[WARN] cuidado") == "warn"
    assert core.log_line_kind("sesión confirmada ✓") == "ok"
    assert core.log_line_kind("normal") == "normal"


def test_asset_path():
    assert core.asset_path("no-existe.png") is None
    logo = core.asset_path("logo.png")
    assert logo is None or logo.name == "logo.png"


# --- Historial de puntos (logs) --------------------------------------------
_LOG_DIA = (
    "11:06:37 [rewards_info] INFO: nivel=3 (Nivel Oro) puntos=27364 [API]\n"
    "11:06:37 [run] INFO: estado cuenta: nivel=3 puntos=27364 fuente=api\n"
    "11:07:00 [search] INFO: búsqueda 3/30: puntos de interés en Roma\n"
    "12:07:36 [rewards_info] INFO: nivel=3 (Nivel Oro) puntos=27589 [API]\n"
    "12:07:36 [run] INFO: puntos: 27364 → 27589 (ganados hoy en esta sesión: +225)\n"
    "12:07:36 [daily] INFO: puntos pendientes: 1 lote(s) cobrados\n"
)


def test_parse_log_balances():
    assert core.parse_log_balances(_LOG_DIA) == [27364, 27364, 27589, 27589]
    assert core.parse_log_balances("") == []
    assert core.parse_log_balances("10:00:00 [x] INFO: puntos=0 [API]\n") == []


def _hist(balances, today=date(2026, 11, 20), days=5):
    return core.points_history(balances, today=today, days=days)


def test_points_history_diferencia_entre_lecturas_consecutivas():
    h = _hist({"2026-11-18": [1000, 1200], "2026-11-19": [1250, 1500],
               "2026-11-20": [1500, 1800]})
    assert [x["date"] for x in h] == ["2026-11-16", "2026-11-17", "2026-11-18",
                                      "2026-11-19", "2026-11-20"]
    # El 19 cuenta también los 50 que llegaron de madrugada (1200 → 1250).
    assert [x["gained"] for x in h] == [None, None, 200, 300, 300]
    assert [x["balance"] for x in h] == [None, None, 1200, 1500, 1800]


def test_points_history_lecturas_repetidas_del_log_real_no_inflan():
    # "puntos=A", "puntos=B" y luego "puntos: A → B" en el mismo log: B − A no
    # debe contar dos veces ni aparecer un canje fantasma.
    h = _hist({"2026-11-20": core.parse_log_balances(_LOG_DIA)})
    assert h[-1]["gained"] == 225 and h[-1]["redeemed"] == 0


def test_points_history_usa_el_saldo_anterior_a_la_ventana():
    h = _hist({"2026-11-01": [900], "2026-11-19": [1000]})
    assert h[3]["gained"] == 100 and h[4]["gained"] is None
    assert len(h) == 5


def test_points_history_canje_no_resta_y_cambio_de_cuenta_se_ignora():
    h = _hist({"2026-11-18": [20000, 20300], "2026-11-19": [15300, 15500],
               "2026-11-20": [90000, 90100]})
    assert [x["gained"] for x in h[2:]] == [300, 200, 100]
    assert [x["redeemed"] for x in h[2:]] == [0, 5000, 0]


def test_points_history_bonus_y_dia_sin_puntos():
    b = {"2026-11-16": [1000, 1300], "2026-11-17": [1300, 1620],
         "2026-11-18": [1620, 2900], "2026-11-19": [2900, 2900],
         "2026-11-20": [2900, 3210]}
    h = _hist(b)
    assert [x["gained"] for x in h] == [300, 320, 1280, 0, 310]
    assert [x["bonus"] for x in h] == [False, False, True, False, False]
    assert core.points_history({}, today=date(2026, 11, 20), days=3) == [
        {"date": d, "gained": None, "balance": None, "redeemed": 0, "bonus": False}
        for d in ("2026-11-18", "2026-11-19", "2026-11-20")]


def test_history_summary():
    today = date(2026, 11, 2)
    h = core.points_history({"2026-10-30": [1000, 1300], "2026-10-31": [1300, 1500],
                             "2026-11-01": [1500, 1900], "2026-11-02": [1900, 2000]},
                            today=today, days=10)
    s = core.history_summary(h, today)
    assert s["month_label"] == "noviembre"
    assert s["month_total"] == 500          # solo 1 y 2 de noviembre
    assert s["average"] == 250 and s["days"] == 4 and s["has_data"]
    assert s["pace"] == 7500                # 1000 en 4 días → ×30
    assert s["best"] == {"date": "2026-11-01", "gained": 400}
    assert s["streak"] == 4
    vacio = core.history_summary(core.points_history({}, today=today, days=10), today)
    assert vacio["has_data"] is False and vacio["best"] is None
    assert vacio["month_total"] == 0 and vacio["pace"] == 0 and vacio["streak"] == 0


def test_history_summary_racha_no_se_rompe_si_hoy_aun_no_hay_corrida():
    today = date(2026, 11, 20)
    h = _hist({"2026-11-17": [100, 400], "2026-11-18": [400, 700],
               "2026-11-19": [700, 900]}, today=today)
    assert core.history_summary(h, today)["streak"] == 3
    h = _hist({"2026-11-17": [100, 400], "2026-11-19": [400, 900]}, today=today)
    assert core.history_summary(h, today)["streak"] == 1


def test_history_day_text():
    today = date(2026, 11, 20)
    assert core.history_day_text({"date": "2026-11-20", "gained": 367}, today) \
        == "Hoy · +367 pts"
    assert core.history_day_text({"date": "2026-11-12", "gained": 1240, "bonus": True},
                                 today) == "12 nov · +1.240 pts · bonus"
    assert core.history_day_text({"date": "2026-11-03", "gained": None}, today) \
        == "3 nov · sin corrida"
    assert core.history_day_text({"date": "2026-11-19", "gained": 210, "redeemed": 5000},
                                 today) == "Ayer · +210 pts · canje de 5.000"
    assert core.history_day_text(None) == ""


def test_read_log_balances(tmp_path):
    (tmp_path / "20261001.log").write_text("x puntos=500 [API]\n", encoding="utf-8")
    (tmp_path / "20261015.log").write_text("x puntos=900 [API]\n", encoding="utf-8")
    (tmp_path / "20261101.log").write_text("corrida sin saldos\n", encoding="utf-8")
    (tmp_path / "20261119.log").write_text(_LOG_DIA, encoding="utf-8")
    (tmp_path / "20261120.log").write_text("sin saldos\n", encoding="utf-8")
    (tmp_path / "notas.log").write_text("puntos=1\n", encoding="utf-8")
    got = core.read_log_balances(days=5, today=date(2026, 11, 20), log_dir=tmp_path)
    # El último log con saldo anterior a la ventana (15 oct) da el punto de partida.
    assert got == {"2026-10-15": [900],
                   "2026-11-19": [27364, 27364, 27589, 27589]}
    assert core.read_log_balances(days=5, today=date(2026, 11, 20),
                                  log_dir=tmp_path / "no_existe") == {}


def test_short_date():
    assert core.short_date("2026-11-12") == "12 nov"
    assert core.short_date(date(2026, 1, 3)) == "3 ene"
    assert core.short_date("ayer") == "" and core.short_date(None) == ""


# --- App de Bing desde el log de hoy (si la corrida no guardó el resumen) ---
_APP_LOG = """\
09:16:46 [app] INFO: === APP DE BING (check-in + Leer para ganar) ===
09:16:52 [app] INFO: check-in de la app: hecho ✓ (racha día 6→7)
09:16:56 [app] INFO: Leer para ganar: 0/30 — empiezo (offer ENUS_readarticle3_30points)
09:17:00 [app] INFO: lectura 1: 3/30
09:25:24 [app] INFO: Leer para ganar: 30/30 ✓ (+30)
"""


def test_app_desde_el_log():
    app = core.app_from_log(_APP_LOG, today="2026-11-20")
    assert app == {"linked": True, "checkin": "ok", "streak_day": 7, "read": [30, 30],
                   "read_status": "ok", "date": "2026-11-20"}
    ya = core.app_from_log("10:00:00 [app] INFO: check-in de la app: ya hecho hoy (racha de /earn) ✓\n"
                           "10:00:04 [app] INFO: Leer para ganar: ya completo (30/30) ✓\n",
                           today="2026-11-20")
    assert ya["checkin"] == "done" and ya["read"] == [30, 30]
    assert core.app_from_log("12:00:00 [run] INFO: nada de la app\n") is None
    sin = core.app_from_log("09:00:00 [app] WARNING: app de Bing: sin permiso válido — vincula\n",
                            today="2026-11-20")
    assert sin["linked"] is False


def test_app_status_usa_el_log_si_no_hay_resumen_de_hoy(monkeypatch):
    monkeypatch.delenv("MSR_APP_ACTIVITIES", raising=False)
    lr = {"status": "ok"}                       # corrida de hoy sin "app"
    log_app = core.app_from_log(_APP_LOG, today="2026-11-20")
    st = core.app_status(lr, linked=True, today="2026-11-20", log_app=log_app)
    assert st["state"] == "done" and st["streak_day"] == 7 and st["read"] == (30, 30)
    # El resumen guardado manda sobre el log.
    lr = {"app": {"date": "2026-11-20", "linked": True, "checkin": "unconfirmed",
                  "read": [12, 30], "read_status": "incomplete"}}
    assert core.app_status(lr, linked=True, today="2026-11-20",
                           log_app=log_app)["state"] == "partial"


# --- Fila de detalle: fichas, app y tarea en palabras ----------------------
def test_activity_status_text():
    f = core.activity_status_text
    assert f({"state": "ok", "value": "120 / 120 pts"}) == "Hecho"
    assert f({"state": "todo", "value": "84 / 120 pts"}) == "Faltan 36 pts"
    assert f({"state": "todo", "value": "119 / 120 pts"}) == "Falta 1 pt"
    assert f({"state": "todo", "value": "2 / 3"}) == "Falta 1 de 3"
    assert f({"state": "todo", "value": "0 / 3"}) == "Faltan 3 de 3"
    assert f({"state": "todo", "value": "12 / 30 min"}) == "Faltan 18 min"
    assert f({"state": "todo", "value": "0 / 1"}) == "Sin hacer"
    assert f({"state": "todo", "value": "No"}) == "Sin hacer"
    assert f({"state": "todo", "value": "Revincular"}) == "Revincular"
    assert f({"state": "pending", "value": "Pendiente"}) == "Pendiente"
    assert f({"state": "na", "value": "Solo con Edge"}) == "Solo con Edge"
    assert f({"state": "unknown", "value": "Sin datos"}) == "Sin datos"


def test_activity_tiles_no_repite_la_app_y_resume():
    hoy = date.today().isoformat()
    lr = {"status": "incomplete", "updated_at": f"{hoy}T10:00:00+01:00",
          "verification": {"pc_search": [84, 120], "daily_set": [3, 3],
                           "visual": [0, 1], "edge": [30, 30], "app_streak": [1, 1]}}
    tiles = core.activity_tiles(core.today_checklist(lr, browser="edge", linked=True))
    assert [x["key"] for x in tiles] == ["pc_search", "daily_set", "visual", "edge"]
    assert [x["status"] for x in tiles] == ["Faltan 36 pts", "Hecho", "Sin hacer", "Hecho"]
    assert tiles[0]["label"] == "Búsquedas" and tiles[0]["progress"] == 0.7
    assert (tiles[0]["done"], tiles[0]["total"], tiles[0]["unit"]) == (84, 120, "pts")
    assert (tiles[1]["done"], tiles[1]["total"], tiles[1]["unit"]) == (3, 3, "")
    pend = core.activity_tiles(core.today_checklist({}, browser="edge", linked=True))
    assert pend[0]["done"] is None and pend[0]["status"] == "Pendiente"
    assert core.activities_verdict(tiles) == ("Faltan 2", "warn")
    hechas = [{"state": "ok"}, {"state": "ok"}, {"state": "na"}]
    assert core.activities_verdict(hechas) == ("Todo hecho", "ok")
    assert core.activities_verdict([{"state": "pending"}, {"state": "ok"}]) \
        == ("Pendiente", "neutral")
    assert core.activities_verdict([{"state": "todo"}, {"state": "ok"}]) \
        == ("Falta 1", "warn")


def test_app_headline_y_racha():
    assert core.app_headline({"state": "done"}) == "Check-in y lectura del día, hechos"
    st = {"state": "partial", "checkin_ok": True, "read": (12, 30)}
    assert core.app_headline(st) == "Falta leer para ganar (12 de 30)"
    st = {"state": "partial", "checkin_ok": False, "read": (30, 30)}
    assert core.app_headline(st) == "Falta el check-in"
    assert core.app_headline({"state": "unlinked", "lines": ["Vincúlala"]}) == "Vincúlala"
    assert core.streak_cycle(3) == {"day": 3, "position": 3, "text": "Racha · día 3"}
    assert core.streak_cycle(7)["position"] == 7 and core.streak_cycle(8)["position"] == 1
    assert core.streak_cycle(None) is None and core.streak_cycle(0) is None
    assert core.streak_cycle(True) is None and core.streak_cycle("x") is None


def test_task_headline():
    from datetime import datetime
    now = datetime(2026, 11, 20, 13, 40)
    h = core.task_headline({"state": "Ready", "next_run": "2026-11-20 14:02:00+00:00",
                            "last_run": "2026-11-20 13:35:00+00:00", "last_result": 0}, now)
    assert h["state"] == "ready" and h["label"] == "Activa"
    assert h["headline"] == "Se ejecuta sola cada día"
    assert (h["next"], h["last"]) == ("hoy, 14:02", "hoy, 13:35") and h["result"]
    m = core.task_headline({}, now)
    assert m["state"] == "missing" and m["headline"] == "El bot no se ejecuta solo"
    assert m["next"] == "" and m["detail"]
    d = core.task_headline({"state": "Disabled"}, now)
    assert d["state"] == "disabled" and "Desactivada" in d["headline"]
    r = core.task_headline({"state": "Running", "last_run": "1999-11-30 00:00:00"}, now)
    assert r["headline"] == "Ejecutándose ahora mismo" and r["last"] == "nunca"


# --- Rachas de 7 días y sellos -----------------------------------------------
_VER_RACHAS = {"streak_days": {"search": [1, 7], "daily_set": [3, 7], "edge": [3, 7],
                               "app": [1, 7], "visual": [5, 7]},
               "stamps": [8, 12]}


def test_activity_streaks_tolera_que_falten():
    got = core.activity_streaks({"verification": _VER_RACHAS})
    assert got == {"pc_search": (1, 7), "daily_set": (3, 7), "visual": (5, 7),
                   "edge": (3, 7), "app": (1, 7)}
    assert core.activity_streaks({}) == {}
    assert core.activity_streaks({"verification": {"pc_search": [1, 2]}}) == {}
    assert core.activity_streaks({"verification": {"streak_days": "roto"}}) == {}
    raro = {"verification": {"streak_days": {"search": [9, 7], "edge": None,
                                             "visual": [2, 0], "otro": [1, 7]}}}
    assert core.activity_streaks(raro) == {"pc_search": (7, 7)}


def test_streak_text():
    assert core.streak_text((3, 7)) == "Racha · día 3 de 7"
    assert core.streak_text((0, 7)) == "Racha sin empezar"
    assert core.streak_text((7, 7)) == "Racha completa · 7 de 7"
    assert core.streak_text(None) == ""


def test_activity_tiles_con_rachas():
    hoy = date.today().isoformat()
    lr = {"status": "ok", "updated_at": f"{hoy}T10:00:00+01:00",
          "verification": {"pc_search": [120, 120], "daily_set": [3, 3],
                           "visual": [1, 1], "edge": [30, 30], **_VER_RACHAS}}
    items = core.today_checklist(lr, browser="edge", linked=True)
    tiles = core.activity_tiles(items, core.activity_streaks(lr))
    assert [x["streak"] for x in tiles] == [(1, 7), (3, 7), (5, 7), (3, 7)]
    assert tiles[2]["streak_text"] == "Racha · día 5 de 7"
    sin = core.activity_tiles(items)
    assert sin[0]["streak"] is None and sin[0]["streak_text"] == ""


def test_stamps_summary():
    s = core.stamps_summary({"verification": _VER_RACHAS})
    assert (s["known"], s["done"], s["total"], s["left"]) == (True, 8, 12, 4)
    assert s["text"] == "+1.000 pts al completar · faltan 4"
    assert core.stamps_summary({"verification": {"stamps": [11, 12]}})["text"] \
        == "+1.000 pts al completar · falta 1"
    lleno = core.stamps_summary({"verification": {"stamps": [14, 12]}})
    assert lleno["done"] == 12 and lleno["text"] == "Completo · +1.000 pts por cobrar"
    for roto in ({}, {"verification": {}}, {"verification": {"stamps": "x"}},
                 {"verification": {"stamps": [3, 0]}}):
        v = core.stamps_summary(roto)
        assert v["known"] is False and v["done"] is None and v["text"]


# --- Objetivo de compra -------------------------------------------------------
def test_parse_price_y_format_eur():
    assert core.parse_price("56") == 56.0
    assert core.parse_price("56,99") == 56.99 and core.parse_price("56.99") == 56.99
    assert core.parse_price(" 56,99 € ") == 56.99 and core.parse_price("1.299,99") == 1299.99
    assert core.parse_price(70) == 70.0
    for malo in ("", "abc", "0", "-5", "5,5,5", None, True, "99999", "nan", "1e3"):
        assert core.parse_price(malo) is None
    assert core.format_eur(56) == "56 €" and core.format_eur(56.99) == "56,99 €"
    assert core.format_eur(1299.5) == "1.299,50 €" and core.format_eur("x") == "—"


def test_custom_points_precios_medidos():
    # Medidos en la cuenta real el 20/11/2026.
    assert core.custom_points(15) == 15440 and core.custom_points(25) == 25735
    assert core.custom_points(69) == 71020 and core.custom_points(100) == 102930
    assert core.custom_points(5) == 5145 and core.custom_points(10) == 10295
    assert core.custom_points(70) == 72050 and core.custom_points(56) == 57640
    for malo in (4, 101, 0, -5, 5.0, True, None):
        assert core.custom_points(malo) is None
    assert (core.CUSTOM_CARD_MIN, core.CUSTOM_CARD_MAX, core.CUSTOM_RATE) == (5, 100, 1029.3)


def test_points_for_price():
    assert core.GIFT_CARDS == ((2, 2675), (5, 5660), (10, 11320))
    p = core.points_for_price(69)
    assert p == {"points": 71020, "covered_eur": 69, "cards": [("custom", 69)],
                 "breakdown": "1 tarjeta personalizada de 69 €", "uses_custom": True}
    assert core.points_for_price(70)["points"] == 72050
    assert core.points_for_price(56)["points"] == 57640
    assert core.points_for_price(56)["breakdown"] == "1 tarjeta personalizada de 56 €"
    assert core.points_for_price(100)["points"] == 102930
    # 3 €: la personalizada mínima (5 €, 5.145) gana a 2 × 2 € (5.350).
    tres = core.points_for_price(3)
    assert tres["points"] == 5145 and tres["covered_eur"] == 5
    assert tres["cards"] == [("custom", 5)]
    # 56,99 € necesita cubrir 57 €.
    q = core.points_for_price("56,99")
    assert q["covered_eur"] == 57 and q["points"] == core.custom_points(57) == 58670
    # Por debajo de 5 € a veces gana la fija de 2 €.
    uno = core.points_for_price(1)
    assert uno == {"points": 2675, "covered_eur": 2, "cards": [("fixed", 2)],
                   "breakdown": "1 × 2 €", "uses_custom": False}
    # Más de 100 €: varias personalizadas; a igualdad de puntos, menos tarjetas.
    g = core.points_for_price(150)
    # (30 tarjetas de 5 € ahorrarían 45 puntos de redondeo: no compensa.)
    assert g["covered_eur"] == 150 and g["uses_custom"]
    assert g["cards"] == [("custom", 100), ("custom", 50)]
    assert g["points"] == 102930 + 51465 == 154395
    assert g["breakdown"] == "Personalizadas: 100 € + 50 €"
    assert len(core.points_for_price(1000)["cards"]) == 10
    assert core.points_for_price(101)["covered_eur"] == 101
    assert core.points_for_price(0) is None and core.points_for_price("x") is None
    assert core._cards_text([("custom", 100), ("fixed", 2)]) \
        == "1 tarjeta personalizada de 100 € · fijas: 1 × 2 €"


def test_goal_io(tmp_path):
    ruta = tmp_path / "state" / "goal.json"
    assert core.read_goal(ruta) is None
    g = core.save_goal("  CoD MW4 ", "56,99", ruta)
    assert g["name"] == "CoD MW4" and g["price_eur"] == 56.99 and g["set_at"]
    assert core.read_goal(ruta) == g
    assert core.save_goal("x", "cero", ruta) is None and core.read_goal(ruta) == g
    ruta.write_text("{roto", encoding="utf-8")
    assert core.read_goal(ruta) is None
    ruta.write_text('{"name": "x", "price_eur": -1}', encoding="utf-8")
    assert core.read_goal(ruta) is None
    assert core.clear_goal(ruta) is True and not ruta.exists()
    assert core.clear_goal(ruta) is True
    assert core.GOAL_PATH == core.STATE_DIR / "goal.json"


def test_current_balance():
    assert core.current_balance({"points_after": 29268}) == 29268
    h = [{"balance": 100}, {"balance": 250}, {"balance": None}]
    assert core.current_balance({}, h) == 250
    assert core.current_balance({"points_after": None}, []) is None
    assert core.current_balance({"points_after": True}) is None


def test_goal_progress():
    hoy = date(2026, 11, 20)
    goal = {"name": "CoD MW4", "price_eur": 56.0}
    g = core.goal_progress(goal, 29268, 10346, hoy)
    assert g["name"] == "CoD MW4" and g["price_text"] == "56 €" and g["points"] == 57640
    assert g["left"] == 57640 - 29268 == 28372 and g["percent"] == 50
    assert g["card_hint"] == core.CUSTOM_CARD_HINT
    assert core.goal_progress({"price_eur": 1}, 0, 0, hoy)["card_hint"] == ""
    assert not g["reached"] and g["surplus"] == 0
    assert g["pace_text"] == "a ~10.346 pts/mes"
    # 10.346/mes → 344,87/día → 83 días
    assert g["days"] == 83 and g["eta_date"] == date(2027, 2, 11)
    assert g["eta_text"] == "≈ 2,8 meses · hacia el 11 de febrero"
    cerca = core.goal_progress(goal, 53000, 9000, hoy)      # faltan 4.640 a 300/día
    assert cerca["days"] == 16 and cerca["eta_text"] == "≈ 16 días · hacia el 6 de diciembre"
    assert core.goal_progress(goal, 57340, 9000, hoy)["eta_text"] \
        == "≈ 1 día · hacia el 21 de noviembre"
    ya = core.goal_progress(goal, 70000, 9000, hoy)
    assert ya["reached"] and ya["left"] == 0 and ya["percent"] == 100
    assert ya["fraction"] == 1.0 and ya["eta_text"] == "¡Ya lo tienes!"
    assert ya["surplus"] == 70000 - 57640
    assert core.goal_progress(goal, 100, 0, hoy)["pace_text"] == ""
    for sin in (core.goal_progress(goal, 29268, 0, hoy),
                core.goal_progress(goal, None, 9000, hoy),
                core.goal_progress(goal, 29268, None, hoy)):
        assert sin["days"] is None and sin["eta_text"] == "Sin datos suficientes"
    assert core.goal_progress(goal, None, 0, hoy)["left"] == 57640
    lejos = core.goal_progress({"price_eur": 500}, 0, 9000, hoy)
    assert lejos["name"] == "Mi objetivo" and lejos["eta_text"][-8:-4] == " de " and lejos["eta_text"][-4:].isdigit()
    assert core.goal_progress(None, 100, 100) is None
    assert core.goal_progress({"price_eur": "x"}, 100, 100) is None


# --- Desglose de un día pasado (log) -----------------------------------------
_LOG_VERIFY = """\
09:58:01 [verify] INFO: === VERIFICACIÓN FINAL ===
09:58:02 [verify] INFO:   búsquedas de PC: 90/120
09:58:02 [verify] INFO: VERIFICACIÓN: falta algo
10:04:03 [verify] INFO: === VERIFICACIÓN FINAL ===
10:04:16 [verify] INFO:   búsquedas de PC: 120/120 ✓
10:04:16 [verify] INFO:   conjunto diario: 3/3 ✓
10:04:16 [verify] INFO:   Ganar (más actividades): 1 sin acreditar: Decora las paredes
10:04:16 [verify] INFO:   tarjetas de sellos: 8 pendientes (el bot no puede acreditarlas visitándolas)
10:04:16 [verify] INFO:   búsqueda por imagen: 1/1 ✓
10:04:16 [verify] INFO:   racha de Edge (minutos): 30/30 ✓
10:04:16 [verify] INFO:   racha de búsqueda: 1/1 ✓
10:04:16 [verify] INFO:   app de Bing — check-in: ✓
10:04:16 [verify] INFO:   app de Bing — Leer para ganar: 30/30 ✓
10:04:16 [verify] INFO:   racha de la app de Bing: 1/1 ✓
10:04:16 [verify] INFO: VERIFICACIÓN: lo principal está acreditado ✓ (lo que queda pendiente no se puede completar desde el bot)
10:04:30 [run] INFO: puntos: 28901 → 29268 (ganados hoy en esta sesión: +367)
"""


def test_parse_verification_log_usa_el_ultimo_bloque():
    d = core.parse_verification_log(_LOG_VERIFY, "2026-11-19")
    assert d["status"] == "ok" and d["last_completed"] == "2026-11-19" and d["from_log"]
    assert d["updated_at"] == "2026-11-19T10:04:16" and d["browser"] == "edge"
    v = d["verification"]
    assert v["pc_search"] == [120, 120] and v["daily_set"] == [3, 3]
    assert v["visual"] == [1, 1] and v["edge"] == [30, 30] and v["app_streak"] == [1, 1]
    assert v["pending_punch"] == 8 and v["ok"] is True
    assert "streak_days" not in v and "stamps" not in v
    assert d["app"] == {"date": "2026-11-19", "linked": True, "checkin": "ok",
                        "read": [30, 30], "read_status": "ok"}
    assert (d["points_before"], d["points_after"]) == (28901, 29268)
    assert core.parse_verification_log("10:00:00 [run] INFO: hola\n", "2026-11-01") is None
    assert core.parse_verification_log("", "2026-11-01") is None


def test_parse_verification_log_variantes_nuevas():
    texto = (
        "11:00:00 [verify] INFO: === VERIFICACIÓN FINAL ===\n"
        "11:00:01 [verify] INFO:   búsquedas de PC: 84/120\n"
        "11:00:01 [verify] INFO:   conjunto diario: 3/3 ✓\n"
        "11:00:01 [verify] INFO:   promociones con tareas (Spotify, Duolingo…): 5 pendientes (opcionales)\n"
        "11:00:01 [verify] INFO:   racha de Edge: omitida (navegador Chrome; requiere Edge)\n"
        "11:00:01 [verify] INFO:   app de Bing — check-in: ✗ (unconfirmed)\n"
        "11:00:01 [verify] INFO:   app de Bing — Leer para ganar: 12/30\n"
        "11:00:01 [verify] INFO:   rachas: búsqueda día 1/7, conjunto diario día 3/7, "
        "Edge día 3/7, app de Bing día 1/7, búsqueda visual día 5/7\n"
        "11:00:01 [verify] INFO:   sellos: 8/12 (al completarlos, bonificación de puntos)\n"
        "11:00:02 [verify] INFO: VERIFICACIÓN: faltan cosas: búsquedas de PC\n")
    d = core.parse_verification_log(texto, "2026-11-20")
    v = d["verification"]
    assert d["status"] == "incomplete" and "last_completed" not in d
    assert d["browser"] == "chrome" and "edge" not in v
    assert v["pending_punch"] == 5 and v["stamps"] == [8, 12]
    assert v["streak_days"] == {"search": [1, 7], "daily_set": [3, 7], "edge": [3, 7],
                                "app": [1, 7], "visual": [5, 7]}
    assert d["app"]["checkin"] == "fail" and d["app"]["read"] == [12, 30]
    assert d["app"]["read_status"] == "partial" and "points_after" not in d


def test_desglose_de_un_dia_se_pinta_con_las_funciones_de_hoy():
    dia = "2026-11-19"
    d = core.parse_verification_log(_LOG_VERIFY, dia)
    tiles = core.activity_tiles(
        core.today_checklist(d, browser=d["browser"], linked=True, today=dia),
        core.activity_streaks(d))
    assert [x["status"] for x in tiles] == ["Hecho"] * 4
    assert (tiles[0]["done"], tiles[0]["total"]) == (120, 120)
    st = core.app_status(d, linked=True, today=dia)
    assert st["state"] == "done" and st["read"] == (30, 30)
    assert core.checklist_subtitle(d, dia) == "Comprobado en Rewards a las 10:04"
    assert core.stamps_summary(d)["known"] is False


def test_read_day_detail_y_textos(tmp_path):
    (tmp_path / "20261119.log").write_text(_LOG_VERIFY, encoding="utf-8")
    (tmp_path / "20261101.log").write_text("10:00:00 [run] INFO: nada\n", encoding="utf-8")
    assert core.read_day_detail("2026-11-19", tmp_path)["verification"]["visual"] == [1, 1]
    assert core.read_day_detail("2026-11-01", tmp_path) is None      # sin bloque
    assert core.read_day_detail("2026-10-01", tmp_path) is None      # sin log
    assert core.read_day_detail("ayer", tmp_path) is None
    hoy = date(2026, 11, 20)
    assert core.day_title("2026-11-20", hoy) == "Actividades de hoy"
    assert core.day_title("2026-11-19", hoy) == "Actividades de ayer"
    assert core.day_title("2026-11-03", hoy) == "Actividades del martes 3 nov"
    assert core.day_title("x", hoy) == "Actividades de hoy"
    titulo, sub = core.day_empty_text({"gained": 373})
    assert titulo == "Sin desglose guardado para este día" and "+373 pts" in sub
    assert "no hubo corrida" in core.day_empty_text({"gained": None})[1]
    assert "no hubo corrida" in core.day_empty_text(None)[1]
    assert "no sumó puntos" in core.day_empty_text({"gained": 0})[1]


def test_sin_dato_no_es_cero():
    assert core.no_data_text("Sellos", past=True) \
        == "Sellos: sin dato ese día · se guardan desde el 20 nov"
    assert core.no_data_text("Racha") \
        == "Racha: sin dato todavía · se sabrá tras la próxima corrida"
    viejo = core.stamps_summary({"verification": {"pc_search": [1, 1]}}, past=True)
    assert viejo["known"] is False and viejo["done"] is None
    assert viejo["text"] == core.no_data_text("Sellos", past=True)
    assert core.stamps_summary({})["text"] == core.no_data_text("Sellos")
    assert core.TRACKING_SINCE == date(2026, 11, 20)
