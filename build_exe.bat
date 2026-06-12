@echo off
setlocal

set "ROOT=%~dp0"
set "VENV_PY=%ROOT%ms_rewards\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo No se encontro el entorno virtual: %VENV_PY%
    echo Ejecuta setup.bat primero
    pause
    exit /b 1
)

echo Instalando dependencias de build (pyinstaller + GUI)...
"%VENV_PY%" -m pip install --upgrade pyinstaller -r "%ROOT%panel\requirements-gui.txt"
if errorlevel 1 (
    echo Fallo la instalacion de dependencias de build.
    pause
    exit /b 1
)

cd /d "%ROOT%"
echo Construyendo el ejecutable con PyInstaller...
"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name MsRewardsPanel --collect-all customtkinter --paths "%ROOT%" run_panel.py
if errorlevel 1 (
    echo Fallo la construccion del ejecutable.
    pause
    exit /b 1
)

echo.
echo Listo. El ejecutable esta en: dist\MsRewardsPanel.exe
pause
exit /b 0
