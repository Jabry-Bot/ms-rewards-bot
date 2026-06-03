"""
Auto-update vía git pull condicional.

Política:
  - Antes de cada ejecución, comparamos VERSION local con VERSION remoto.
  - Si difieren, hacemos git pull --ff-only y, si entraron cambios en .py o
    en selectors.json, relanzamos el proceso (os.execv) para que el código
    nuevo entre en vigor antes de continuar.
  - Si algo falla (sin red, repo no es git, conflicto local) NO abortamos:
    el bot tiene que seguir ejecutándose con la versión que tenga.

Asumimos que el repo se clonó con git y que `origin` está configurado al
upstream. Si no es así, log warning y skip silencioso.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("updater")

_REPO_ROOT = Path(__file__).parent
_VERSION_FILE = _REPO_ROOT / "VERSION"


def _run_git(*args: str, timeout: int = 30) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return -1, "", "git no está en PATH"
    except subprocess.TimeoutExpired:
        return -1, "", "timeout"
    except Exception as exc:
        return -1, "", str(exc)


def current_version() -> str:
    try:
        return _VERSION_FILE.read_text(encoding="utf-8").strip() or "0.0.0"
    except FileNotFoundError:
        return "0.0.0"


def _is_git_repo() -> bool:
    code, out, _ = _run_git("rev-parse", "--is-inside-work-tree")
    return code == 0 and out.strip() == "true"


def _has_remote() -> bool:
    code, out, _ = _run_git("remote")
    return code == 0 and bool(out.strip())


def _version_repo_path() -> str:
    """
    Ruta de VERSION relativa a la raíz del repo git. El repo puede tener su
    raíz en el directorio padre (scrp/) mientras VERSION vive en ms_rewards/;
    `git show <ref>:<path>` exige el path relativo a la raíz, no a cwd.
    """
    code, prefix, _ = _run_git("rev-parse", "--show-prefix")
    prefix = (prefix or "").strip()  # p.ej. "ms_rewards/" o "" si raíz==cwd
    return f"{prefix}VERSION"


def remote_version() -> str | None:
    """Devuelve la versión publicada en origin/HEAD o None si no se pudo."""
    if not _is_git_repo() or not _has_remote():
        return None
    # Fetch para tener refs actualizadas
    code, _, err = _run_git("fetch", "--quiet", "origin", timeout=45)
    if code != 0:
        log.warning("git fetch falló: %s", err)
        return None
    # Determinar la rama remota por defecto
    code, branch, _ = _run_git("symbolic-ref", "refs/remotes/origin/HEAD")
    if code != 0 or not branch:
        # fallback a origin/main
        branch_ref = "origin/main"
    else:
        branch_ref = branch.replace("refs/remotes/", "")
    version_path = _version_repo_path()
    code, content, err = _run_git("show", f"{branch_ref}:{version_path}")
    if code != 0:
        log.warning("no se pudo leer VERSION remoto en %s:%s: %s",
                    branch_ref, version_path, err)
        return None
    return content.strip() or None


def update_if_needed() -> bool:
    """
    Si la versión remota difiere de la local, hace git pull. Devuelve True
    si entraron cambios (y por tanto puede tener sentido relanzar el proceso).
    """
    if not _is_git_repo():
        log.info("no es un repo git, saltando auto-update")
        return False
    local = current_version()
    remote = remote_version()
    if remote is None:
        log.info("no se obtuvo versión remota, saltando update")
        return False
    if local == remote:
        log.info("VERSION al día (v%s)", local)
        return False
    log.info("nueva versión remota detectada: local=%s remoto=%s", local, remote)
    code, out, err = _run_git("pull", "--ff-only", "--quiet", timeout=60)
    if code != 0:
        log.warning("git pull falló: %s", err or out)
        return False
    log.info("git pull OK, ahora en VERSION=%s", current_version())
    return True


def relaunch() -> None:
    """Relanza el proceso actual con el mismo intérprete y mismos args."""
    log.info("relanzando proceso para cargar el código nuevo")
    python = sys.executable
    args = [python] + sys.argv
    os.execv(python, args)
