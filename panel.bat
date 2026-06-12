@echo off
setlocal

set "ROOT=%~dp0"
set "VENV_PYW=%ROOT%ms_rewards\.venv\Scripts\pythonw.exe"
set "VENV_PY=%ROOT%ms_rewards\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo No se encontro el entorno virtual: %VENV_PY%
    echo Ejecuta setup.bat primero
    pause
    exit /b 1
)

REM Asegurar que customtkinter esta instalado en el venv
"%VENV_PY%" -c "import customtkinter" 2>nul
if errorlevel 1 (
    echo Instalando dependencias de la GUI...
    "%VENV_PY%" -m pip install -r "%ROOT%panel\requirements-gui.txt"
)

REM Lanzar la GUI sin consola. cwd = raiz del repo para que 'python -m panel.app' encuentre el paquete.
cd /d "%ROOT%"
start "" "%VENV_PYW%" -m panel.app

exit /b 0
