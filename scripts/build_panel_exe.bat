@echo off
REM Construye MsRewardsPanel.exe con PyInstaller.
REM Vive en scripts/; el repo es la carpeta padre.
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "VENV_PY=%ROOT%\ms_rewards\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo No se encontro el entorno virtual: %VENV_PY%
    echo Ejecuta setup.exe (o scripts\setup.bat) primero.
    pause
    exit /b 1
)

echo Instalando dependencias de build (pyinstaller + GUI)...
"%VENV_PY%" -m pip install --upgrade pyinstaller -r "%ROOT%\panel\requirements-gui.txt"
if errorlevel 1 (
    echo Fallo la instalacion de dependencias de build.
    pause
    exit /b 1
)

cd /d "%ROOT%"
echo Construyendo el ejecutable con PyInstaller...
"%VENV_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name MsRewardsPanel --collect-all customtkinter --paths "%ROOT%" "%ROOT%\scripts\run_panel.py"
if errorlevel 1 (
    echo Fallo la construccion del ejecutable.
    pause
    exit /b 1
)

echo.
echo Listo. El ejecutable esta en: dist\MsRewardsPanel.exe
echo Copialo a la raiz del repo (junto a ms_rewards\) para distribuirlo.
pause
exit /b 0
