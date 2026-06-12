"""Entry script del instalador para PyInstaller / ejecución directa en dev.

Vive en scripts/, así que añadimos la raíz del repo a sys.path para que
`from installer.app import App` resuelva al ejecutarlo como `python scripts/run_setup.py`.
(En el build de PyInstaller el import se resuelve además vía --paths <repo>.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from installer.app import App

if __name__ == "__main__":
    App().mainloop()
