# scripts/

Utilidades de desarrollo y *fallbacks* de línea de comandos. **El usuario normal no necesita nada de aquí** — usa `setup.exe` para instalar y `MsRewardsPanel.exe` para el día a día (ambos en la raíz).

| Archivo | Para qué |
|---------|----------|
| `build_setup_exe.bat` | Construye `setup.exe` con PyInstaller (solo maintainer). |
| `build_panel_exe.bat` | Construye `MsRewardsPanel.exe` con PyInstaller (solo maintainer). |
| `run_setup.py` / `run_panel.py` | Entry scripts de PyInstaller; también sirven para lanzar las GUIs en dev (`python scripts/run_panel.py`). |
| `setup.bat` | Instalador por consola — *fallback* de `setup.exe` (p.ej. si el antivirus bloquea el .exe). |
| `uninstall.bat` | Desinstalador por consola — *fallback* del botón 🧹 Desinstalar del panel. |

Tras construir un `.exe`, cópialo desde `dist\` a la raíz del repo (debe vivir junto a `ms_rewards\`).
