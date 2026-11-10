"""Tests de la ejecución discreta (quiet.py), del relanzado tras update y del
contador de fallos de sesión.

Los módulos del bot usan imports planos (`import config`), así que se añade
ms_rewards/ al FINAL de sys.path: al principio, ms_rewards/selectors.py
taparía el `selectors` de la stdlib que usa asyncio.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from pathlib import Path

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))

import quiet  # noqa: E402

from panel import core  # noqa: E402


# --- respawn con consola invisible ---------------------------------------
def test_should_respawn_solo_bajo_pythonw():
    assert quiet.should_respawn(r"C:\x\.venv\Scripts\pythonw.exe", env={})
    assert not quiet.should_respawn(r"C:\x\.venv\Scripts\python.exe", env={})


def test_should_respawn_no_en_bucle():
    env = {quiet.CHILD_ENV: "1"}
    assert not quiet.should_respawn(r"C:\x\pythonw.exe", env=env)


def test_console_python_es_hermano():
    py = quiet.console_python(r"C:\Juan Pérez\venv\Scripts\pythonw.exe")
    assert py == Path(r"C:\Juan Pérez\venv\Scripts\python.exe")


def test_child_args_garantiza_scheduled():
    assert quiet.child_args([]) == ["--scheduled"]
    assert quiet.child_args(["--scheduled"]) == ["--scheduled"]
    assert quiet.child_args(["--daily"]) == ["--daily", "--scheduled"]


def test_respawn_hidden_no_hace_nada_fuera_de_pythonw(monkeypatch):
    monkeypatch.setattr(quiet, "should_respawn", lambda: False)
    assert quiet.respawn_hidden("run.py", []) is None


def test_respawn_hidden_lanza_sin_ventana(monkeypatch, tmp_path):
    py = tmp_path / "python.exe"
    py.write_text("")
    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 7)

    monkeypatch.setattr(quiet, "should_respawn", lambda: True)
    monkeypatch.setattr(quiet, "console_python", lambda: py)
    monkeypatch.setattr(quiet.subprocess, "run", fake_run)
    assert quiet.respawn_hidden("run.py", ["--daily"]) == 7
    assert calls["cmd"] == [str(py), "run.py", "--daily", "--scheduled"]
    assert calls["kw"]["creationflags"] == quiet.NO_WINDOW
    assert calls["kw"]["env"][quiet.CHILD_ENV] == "1"


# --- pantalla completa ----------------------------------------------------
def test_busy_reason():
    assert quiet.busy_reason(quiet.QUNS_RUNNING_D3D_FULL_SCREEN)
    assert quiet.busy_reason(quiet.QUNS_BUSY)
    assert quiet.busy_reason(quiet.QUNS_PRESENTATION_MODE)
    assert quiet.busy_reason(quiet.QUNS_ACCEPTS_NOTIFICATIONS) is None
    assert quiet.busy_reason(quiet.QUNS_NOT_PRESENT) is None
    assert quiet.busy_reason(None) is None


def test_notification_state_devuelve_int_o_none():
    st = quiet.notification_state()
    assert st is None or isinstance(st, int)


def test_should_postpone_respeta_hora_limite(monkeypatch):
    monkeypatch.setenv("MSR_GAME_DEADLINE_HOUR", "21")
    tarde = dt.datetime(2026, 11, 10, 20, 59)
    noche = dt.datetime(2026, 11, 10, 21, 0)
    assert quiet.should_postpone("juego a pantalla completa", tarde)
    assert not quiet.should_postpone("juego a pantalla completa", noche)
    assert not quiet.should_postpone(None, tarde)


def test_deadline_invalida_usa_default(monkeypatch):
    monkeypatch.setenv("MSR_GAME_DEADLINE_HOUR", "xx")
    assert quiet.game_deadline_hour() == 21


# --- relanzado tras update (sin os.execv) ---------------------------------
def test_relaunch_usa_lista_y_marca_entorno(monkeypatch):
    import updater

    calls = {}

    def fake_run(cmd, **kw):
        calls["cmd"], calls["kw"] = cmd, kw
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    monkeypatch.setattr(updater.sys, "argv", [r"C:\Juan Pérez\bot\run.py", "--scheduled"])
    assert updater.relaunch() == 0
    # Lista (no string): subprocess entrecomilla la ruta con espacio.
    assert calls["cmd"][1:] == [r"C:\Juan Pérez\bot\run.py", "--scheduled"]
    assert calls["kw"]["env"][updater.RELAUNCHED_ENV] == "1"


# --- contador de fallos de sesión ------------------------------------------
def test_relogin_failures_cuenta_y_se_limpia(monkeypatch, tmp_path):
    import config
    import runstate

    monkeypatch.setattr(config, "LAST_RUN_PATH", tmp_path / "last_run.json")
    assert runstate.relogin_failures_today() == 0
    runstate.mark_needs_relogin()
    runstate.mark_needs_relogin()
    assert runstate.relogin_failures_today() == 2
    assert runstate.read()["status"] == "needs_relogin"
    runstate.clear_relogin_failures()
    assert runstate.relogin_failures_today() == 0


def test_relogin_failures_de_ayer_no_cuentan(monkeypatch, tmp_path):
    import config
    import runstate

    monkeypatch.setattr(config, "LAST_RUN_PATH", tmp_path / "last_run.json")
    runstate.write(relogin_failures={"date": "2000-01-01", "count": 9})
    assert runstate.relogin_failures_today() == 0
    runstate.mark_needs_relogin()
    assert runstate.relogin_failures_today() == 1


# --- panel: código de la última ejecución ---------------------------------
def test_task_result_text():
    assert core.task_result_text(0) == "OK"
    assert "ya había una corrida" in core.task_result_text(2147946720)
    assert "ya había una corrida" in core.task_result_text(-2147020576)  # firmado
    assert core.task_result_text(5) == "código 5"
    assert core.task_result_text(None) == "código ?"
