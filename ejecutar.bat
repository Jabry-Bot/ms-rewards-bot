@echo off
REM ms_rewards - ejecucion manual FORZADA.
REM Ignora el "ya completado hoy" y corre todo: daily + busquedas desktop +
REM busquedas movil. Muestra el log en vivo en esta ventana.

setlocal
set "ROOT=%~dp0"
set "REWARDS=%ROOT%ms_rewards"
set "VENV_PY=%REWARDS%\.venv\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo ERROR: no encuentro el entorno virtual. Ejecuta setup.bat primero.
    pause
    exit /b 1
)

echo ============================================================
echo  Ejecucion FORZADA de ms_rewards
echo  (daily + busquedas desktop + movil)
echo ============================================================
echo.

cd /d "%REWARDS%"
"%VENV_PY%" run.py --force %*
set "RC=%errorlevel%"

echo.
echo ============================================================
echo  Terminado (codigo %RC%). Log en: %REWARDS%\logs
echo ============================================================
pause
exit /b %RC%
