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


# --- build_switch_command ------------------------------------------------
def test_build_switch_command():
    assert core.build_switch_command() == [str(core.VENV_PY), str(core.SWITCH_PY)]


# --- build_task_command --------------------------------------------------
def test_build_task_command_install():
    cmd = core.build_task_command(True)
    assert "powershell" in cmd
    assert "-File" in cmd
    assert any("install_task.ps1" in part for part in cmd)


def test_build_task_command_uninstall():
    cmd = core.build_task_command(False)
    assert "powershell" in cmd
    assert "-File" in cmd
    assert any("uninstall_task.ps1" in part for part in cmd)


# --- build_task_query_command --------------------------------------------
def test_build_task_query_command():
    cmd = core.build_task_query_command()
    assert "powershell" in cmd
    assert any(core.TASK_NAME in part for part in cmd)


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
