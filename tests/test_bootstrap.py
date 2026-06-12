"""Tests de la lógica pura del instalador (installer/bootstrap.py)."""
from __future__ import annotations

from pathlib import Path

import pytest

from installer import bootstrap as boot


# --- Constantes ----------------------------------------------------------
def test_min_python_constant():
    assert boot.MIN_PYTHON == (3, 10)


def test_python_version_and_url():
    assert isinstance(boot.PYTHON_VERSION, str) and boot.PYTHON_VERSION
    assert boot.PYTHON_INSTALLER_URL.startswith("https://")
    assert boot.PYTHON_VERSION in boot.PYTHON_INSTALLER_URL


def test_git_version_and_url():
    assert isinstance(boot.GIT_VERSION, str) and boot.GIT_VERSION
    assert boot.GIT_INSTALLER_URL.startswith("https://")


def test_winget_ids_have_expected_keys():
    for key in ("git", "python", "edge", "chrome"):
        assert key in boot.WINGET_IDS
    for val in boot.WINGET_IDS.values():
        assert isinstance(val, str) and val


# --- parse_python_version ------------------------------------------------
def test_parse_python_version_ok():
    assert boot.parse_python_version("Python 3.12.7") == (3, 12, 7)
    assert boot.parse_python_version("Python 3.9.1") == (3, 9, 1)


def test_parse_python_version_none():
    assert boot.parse_python_version("no version here") is None
    assert boot.parse_python_version("") is None


# --- is_supported_python -------------------------------------------------
def test_is_supported_python():
    assert boot.is_supported_python((3, 12, 7)) is True
    assert boot.is_supported_python((3, 10, 0)) is True
    assert boot.is_supported_python((3, 9, 18)) is False
    assert boot.is_supported_python(None) is False


# --- winget_install_cmd --------------------------------------------------
def test_winget_install_cmd_python():
    cmd = boot.winget_install_cmd("python")
    for token in ("winget", "install", "--id", boot.WINGET_IDS["python"], "--silent"):
        assert token in cmd


def test_winget_install_cmd_unknown_raises():
    with pytest.raises(KeyError):
        boot.winget_install_cmd("nope")


# --- instaladores directos -----------------------------------------------
def test_python_direct_install_cmd(tmp_path):
    path = tmp_path / "python-installer.exe"
    cmd = boot.python_direct_install_cmd(path)
    assert str(path) in cmd
    for token in ("/quiet", "InstallAllUsers=0", "PrependPath=1"):
        assert token in cmd


def test_git_direct_install_cmd(tmp_path):
    path = tmp_path / "git-installer.exe"
    cmd = boot.git_direct_install_cmd(path)
    assert str(path) in cmd
    assert "/VERYSILENT" in cmd


# --- comandos de venv / pip ----------------------------------------------
def test_venv_create_cmd(tmp_path):
    py = tmp_path / "python.exe"
    venv_dir = tmp_path / "venv"
    assert boot.venv_create_cmd(py, venv_dir) == [str(py), "-m", "venv", str(venv_dir)]


def test_pip_upgrade_cmd(tmp_path):
    py = tmp_path / "python.exe"
    assert boot.pip_upgrade_cmd(py) == [str(py), "-m", "pip", "install", "--upgrade", "pip"]


def test_pip_requirements_cmd(tmp_path):
    py = tmp_path / "python.exe"
    req = tmp_path / "requirements.txt"
    cmd = boot.pip_requirements_cmd(py, req)
    for token in (str(py), "-m", "pip", "install", "-r", str(req)):
        assert token in cmd


# --- patchright ----------------------------------------------------------
def test_patchright_drivers_cmd_edge(tmp_path):
    py = tmp_path / "python.exe"
    cmd = boot.patchright_drivers_cmd(py, "edge")
    assert "patchright" in cmd and "install" in cmd
    assert cmd[-1] == "msedge"


def test_patchright_drivers_cmd_chrome(tmp_path):
    py = tmp_path / "python.exe"
    cmd = boot.patchright_drivers_cmd(py, "chrome")
    assert "patchright" in cmd and "install" in cmd
    assert cmd[-1] == "chrome"


# --- first_existing ------------------------------------------------------
def test_first_existing_positive(tmp_path):
    real = tmp_path / "real.txt"
    real.write_text("x", encoding="utf-8")
    missing = tmp_path / "missing.txt"
    assert boot.first_existing([missing, real]) == real


def test_first_existing_none(tmp_path):
    paths = [tmp_path / "a", tmp_path / "b", tmp_path / "c"]
    assert boot.first_existing(paths) is None


# --- candidatos ----------------------------------------------------------
@pytest.mark.parametrize(
    "fn",
    [
        boot.candidate_python_paths,
        boot.candidate_git_paths,
        boot.candidate_edge_paths,
        boot.candidate_chrome_paths,
    ],
)
def test_candidate_paths_non_empty_paths(fn):
    paths = fn()
    assert isinstance(paths, list) and len(paths) > 0
    assert all(isinstance(p, Path) for p in paths)


# --- detectores: no lanzan, devuelven Path|None --------------------------
@pytest.mark.parametrize(
    "fn",
    [boot.detect_git, boot.detect_edge, boot.detect_chrome, boot.detect_python_path],
)
def test_detectors_return_path_or_none(fn):
    result = fn()
    assert result is None or isinstance(result, Path)


# --- TOOLS ---------------------------------------------------------------
def test_tools_keys_and_required():
    by_key = {t.key: t for t in boot.TOOLS}
    for key in ("python", "git", "edge", "chrome"):
        assert key in by_key
    assert by_key["python"].required is True
    assert by_key["git"].required is True
    for t in boot.TOOLS:
        assert callable(t.detector)
        assert isinstance(t.label, str) and t.label


# --- scan_tools ----------------------------------------------------------
def test_scan_tools_installed_flags(tmp_path):
    not_installed = boot.Tool("x", "X", lambda: None)
    installed = boot.Tool("y", "Y", lambda: tmp_path)
    statuses = boot.scan_tools([not_installed, installed])
    assert len(statuses) == 2
    assert statuses[0].installed is False
    assert statuses[0].tool is not_installed
    assert statuses[1].installed is True
    assert statuses[1].tool is installed
