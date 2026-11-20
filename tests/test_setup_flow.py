"""Instalación sin consola: login en el navegador, .env local y accesos directos."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

_BOT = Path(__file__).resolve().parent.parent / "ms_rewards"
if str(_BOT) not in sys.path:
    sys.path.append(str(_BOT))  # al final: ms_rewards/selectors.py no tapa la stdlib

import config  # noqa: E402
import login  # noqa: E402
import setup_cli  # noqa: E402
import winutil  # noqa: E402

from installer import bootstrap as boot  # noqa: E402


# --- .env local -----------------------------------------------------------
def test_dotenv_carga_sin_pisar_el_entorno(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# comentario\nMSR_T_A=1\nMSR_T_B = 'hola'\nbasura\nMSR_T_C=del_env\n",
                   encoding="utf-8")
    monkeypatch.delenv("MSR_T_A", raising=False)
    monkeypatch.delenv("MSR_T_B", raising=False)
    monkeypatch.setenv("MSR_T_C", "ya_estaba")
    config.load_dotenv(env)
    assert os.environ["MSR_T_A"] == "1" and os.environ["MSR_T_B"] == "hola"
    assert os.environ["MSR_T_C"] == "ya_estaba"
    config.load_dotenv(tmp_path / "no_existe.env")       # no falla


def test_env_local_no_va_a_git():
    root = Path(__file__).resolve().parent.parent
    gitignore = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in gitignore
    assert (root / ".env.example").exists()


# --- setup_cli: sin preguntas ---------------------------------------------
def test_setup_cli_no_pregunta_nada(monkeypatch):
    calls = []
    monkeypatch.setattr(setup_cli, "_setx", lambda k, v: calls.append((k, v)))
    monkeypatch.setattr(setup_cli.importlib, "reload", lambda m: m)
    monkeypatch.setattr(setup_cli.switch, "reset_session", lambda: True)

    async def ok():
        return True
    monkeypatch.setattr(setup_cli.switch, "browser_fresh_login", ok)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("no debe pedir nada"))
    monkeypatch.delenv("MSR_USER_ID", raising=False)
    assert setup_cli.main(["--browser", "edge"]) == 0
    assert ("MSR_BROWSER", "edge") in calls
    assert any(k == "MSR_USER_ID" for k, _ in calls)
    assert not any("MAINTAINER" in k or "OLLAMA" in k for k, _ in calls)


def test_setup_cli_login_sin_detectar_devuelve_2(monkeypatch):
    monkeypatch.setattr(setup_cli, "_setx", lambda k, v: None)
    monkeypatch.setattr(setup_cli.importlib, "reload", lambda m: m)
    monkeypatch.setattr(setup_cli.switch, "reset_session", lambda: True)

    async def no():
        return False
    monkeypatch.setattr(setup_cli.switch, "browser_fresh_login", no)
    assert setup_cli.main(["--browser", "chrome"]) == 2
    monkeypatch.setattr(setup_cli.switch, "reset_session", lambda: False)
    assert setup_cli.main(["--browser", "chrome"]) == 1


def test_bootstrap_setup_cli_cmd():
    cmd = boot.setup_cli_cmd("py.exe", "setup_cli.py", "Edge")
    assert cmd == ["py.exe", "setup_cli.py", "--browser", "edge"]
    with pytest.raises(ValueError):
        boot.setup_cli_cmd("py.exe", "setup_cli.py", "firefox")


# --- Login en el navegador --------------------------------------------------
class _Resp:
    def __init__(self, data, ok=True):
        self._data, self.ok = data, ok

    async def json(self):
        return self._data


class _Req:
    def __init__(self, answers):
        self.answers = list(answers)

    async def get(self, *_a, **_k):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class _Page:
    url = "https://login.live.com/"

    async def query_selector(self, _sel):
        return None


class _Ctx:
    def __init__(self, answers):
        self.request = _Req(answers)
        self.pages = [_Page()]


def _signed(points=100):
    return _Resp({"dashboard": {"userStatus": {"availablePoints": points}}})


def test_browser_login_espera_a_que_entre_el_usuario(monkeypatch):
    async def goto(*_a, **_k):
        return True
    monkeypatch.setattr(login, "safe_goto", goto)
    nada = _Resp({"dashboard": {}})
    ctx = _Ctx([nada, nada, nada, _signed()])
    assert asyncio.run(login.browser_login(ctx, timeout_s=5, poll_s=0)) is True


def test_browser_login_se_rinde_si_no_entra(monkeypatch):
    async def goto(*_a, **_k):
        return True
    monkeypatch.setattr(login, "safe_goto", goto)
    ctx = _Ctx([_Resp({}, ok=False)])
    assert asyncio.run(login.browser_login(ctx, timeout_s=0.05, poll_s=0.01)) is False


# --- Accesos directos -----------------------------------------------------
def test_accesos_directos_en_escritorio_y_menu_inicio(monkeypatch, tmp_path):
    made = []
    monkeypatch.setattr(winutil, "special_folder", lambda name: str(tmp_path / name))
    monkeypatch.setattr(winutil, "create_shortcut", lambda t, lnk, w: made.append(lnk))
    out = winutil.create_panel_shortcuts("C:/x/MsRewardsPanel.exe", "C:/x")
    assert [Path(p).parent.name for p in out] == ["Desktop", "Programs"]
    assert all(Path(p).name == "ms_rewards Panel.lnk" for p in out) and out == made


def test_bootstrap_panel_shortcuts_cmd():
    cmd = boot.create_panel_shortcuts_cmd("py.exe", "winutil.py", "C:/x/Panel.exe", "C:/x")
    assert cmd == ["py.exe", "winutil.py", "panel-shortcuts", "C:/x/Panel.exe", "C:/x"]


def test_run_crea_los_accesos_una_sola_vez(monkeypatch, tmp_path):
    import logging

    import run
    calls = []
    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setattr(winutil, "create_panel_shortcuts",
                        lambda t, w: calls.append(t) or ["a.lnk", "b.lnk"])
    # El panel "existe"; el resto de rutas, solo si son archivos de verdad.
    monkeypatch.setattr(Path, "exists",
                        lambda self: self.name == "MsRewardsPanel.exe" or Path.is_file(self))
    log = logging.getLogger("t")
    run._ensure_panel_shortcuts(log)
    run._ensure_panel_shortcuts(log)
    assert len(calls) == 1 and (tmp_path / "shortcuts.done").is_file()
