@echo off
setlocal
set "LAST_RUN=%~dp0ms_rewards\state\last_run.json"

if not exist "%LAST_RUN%" (
    echo No existe last_run.json - el bot todavia no ha completado ninguna corrida.
    echo Ruta esperada: %LAST_RUN%
    pause
    exit /b 1
)

echo === Estado de la ultima corrida (last_run.json) ===
echo.
type "%LAST_RUN%"
echo.
echo.
pause
