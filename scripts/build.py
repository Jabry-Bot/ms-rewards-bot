"""
Construye los ejecutables (setup.exe y MsRewardsPanel.exe) en Python puro.

Reemplaza los antiguos .bat de build. Uso (desde la raíz del repo o desde
cualquier sitio):

    python scripts/build.py            # construye ambos
    python scripts/build.py panel      # solo el panel
    python scripts/build.py setup      # solo el instalador

Qué hace:
  - Usa el python del venv del bot si existe (tiene customtkinter/pywin32);
    si no, el python actual.
  - Instala pyinstaller / customtkinter / pillow si faltan.
  - Convierte assets/logo.png -> assets/icon.ico (si hay logo y falta el ico).
  - Construye con --icon y empaqueta el .ico para el icono de ventana.
  - Copia los .exe resultantes a la raíz del repo.

No usa shell ni .bat: solo Python (subprocess para invocar pyinstaller/pip).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / "ms_rewards" / ".venv" / "Scripts" / "python.exe"
ASSETS = ROOT / "assets"
LOGO = ASSETS / "logo.png"
ICON = ASSETS / "icon.ico"
SPLASH = ASSETS / "splash.png"

TARGETS = {
    "panel": {"name": "MsRewardsPanel", "entry": ROOT / "scripts" / "run_panel.py", "uac": False},
    "setup": {"name": "setup", "entry": ROOT / "scripts" / "run_setup.py", "uac": True},
}


def _py() -> str:
    return str(VENV_PY) if VENV_PY.exists() else sys.executable


def _run(cmd: list[str]) -> None:
    print(">", " ".join(cmd))
    subprocess.run(cmd, check=True)


def ensure_deps() -> None:
    print("== Instalando dependencias de build (pyinstaller, customtkinter, pillow) ==")
    _run([_py(), "-m", "pip", "install", "--upgrade",
          "pyinstaller", "customtkinter", "pillow"])


def make_icon() -> Path | None:
    """Convierte logo.png -> icon.ico. Devuelve la ruta del ico o None."""
    if ICON.exists():
        return ICON
    if not LOGO.exists():
        print(f"[aviso] No hay {LOGO}; se construye SIN icono. "
              "Coloca el logo ahí y vuelve a ejecutar para añadirlo.")
        return None
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print("[aviso] Pillow no disponible; se construye sin icono.")
        return None
    print(f"== Generando {ICON.name} desde {LOGO.name} ==")
    img = Image.open(LOGO).convert("RGBA")
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    ASSETS.mkdir(exist_ok=True)
    img.save(ICON, format="ICO", sizes=sizes)
    return ICON


def make_splash() -> None:
    """Genera assets/splash.png (logo centrado sobre fondo oscuro) para el splash."""
    src = LOGO if LOGO.exists() else (ICON if ICON.exists() else None)
    if src is None:
        return
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        print("[aviso] Pillow no disponible; sin splash.png.")
        return
    print(f"== Generando {SPLASH.name} desde {src.name} ==")
    im = Image.open(src).convert("RGBA")
    box = 132
    scale = box / max(im.width, im.height)
    im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))),
                   Image.LANCZOS)
    canvas = Image.new("RGBA", (box, box), (14, 17, 22, 255))  # #0e1116
    canvas.paste(im, ((box - im.width) // 2, (box - im.height) // 2), im)
    ASSETS.mkdir(exist_ok=True)
    canvas.convert("RGB").save(SPLASH)


def build_one(key: str, icon: Path | None) -> None:
    t = TARGETS[key]
    print(f"\n== Construyendo {t['name']}.exe ==")
    cmd = [
        _py(), "-m", "PyInstaller", "--noconfirm", "--clean",
        "--onefile", "--windowed", "--name", t["name"],
        "--collect-all", "customtkinter", "--paths", str(ROOT),
    ]
    if t["uac"]:
        cmd.append("--uac-admin")
    if icon:
        cmd += ["--icon", str(icon)]
    # Empaquetar la carpeta assets (icon.ico para iconbitmap + splash.png).
    if ASSETS.exists():
        cmd += ["--add-data", f"{ASSETS};assets"]
    # Empaquetar VERSION: el panel compara esta (versión de compilación) con la
    # del disco (git pull) para auto-actualizar su propio .exe.
    version_file = ROOT / "ms_rewards" / "VERSION"
    if version_file.exists():
        cmd += ["--add-data", f"{version_file};."]
    cmd.append(str(t["entry"]))
    _run(cmd)

    out = ROOT / "dist" / f"{t['name']}.exe"
    if not out.exists():
        raise SystemExit(f"[error] no se generó {out}")
    dest = ROOT / f"{t['name']}.exe"
    try:
        shutil.copy2(out, dest)
        print(f"  copiado a {dest}")
    except OSError as exc:
        print(f"  [aviso] no se pudo copiar a la raíz ({exc}); "
              f"está en {out}. Cierra la app si la tienes abierta.")


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    keys = list(TARGETS) if which == "both" else [which]
    bad = [k for k in keys if k not in TARGETS]
    if bad:
        print(f"objetivo desconocido: {bad}. Usa: panel | setup | both")
        return 2

    ensure_deps()
    icon = make_icon()
    make_splash()
    # Construir desde la raíz para que dist/ y build/ queden ahí.
    import os
    os.chdir(ROOT)
    for k in keys:
        build_one(k, icon)
    print("\nListo. Los .exe están en la raíz del repo y en dist\\.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
