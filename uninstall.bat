@echo off
REM ms_rewards - desinstalador.
REM Doble-click este archivo. Te pregunta antes de borrar cosas que dolerian.

setlocal ENABLEDELAYEDEXPANSION
set "ROOT=%~dp0"
set "REWARDS=%ROOT%ms_rewards"

echo ============================================================
echo  Desinstalando ms_rewards
echo ============================================================
echo.

REM --- 1) Scheduled Task (siempre se quita) ---
echo [1/5] Eliminando Scheduled Task...
powershell -ExecutionPolicy Bypass -File "%REWARDS%\scheduler\uninstall_task.ps1"
echo.

REM --- 2) State / last_run.json (siempre) ---
echo [2/5] Eliminando estado de ejecucion (last_run.json)...
if exist "%REWARDS%\state\last_run.json" (
    del /Q "%REWARDS%\state\last_run.json"
    echo   state/last_run.json eliminado.
) else (
    echo   no habia state que eliminar.
)
echo.

REM --- 3) Credenciales (con confirmacion) ---
echo [3/5] Credenciales cifradas (state\credentials.bin)
if exist "%REWARDS%\state\credentials.bin" (
    set /p ANS=  Borrar credenciales guardadas? [s/N]:
    if /I "!ANS!"=="s" (
        del /Q "%REWARDS%\state\credentials.bin"
        echo   credentials.bin eliminado.
    ) else (
        echo   conservadas.
    )
) else (
    echo   no hay credenciales guardadas.
)
echo.

REM --- 4) Perfil de Chrome con la sesion (con confirmacion) ---
echo [4/5] Perfil de Chrome del bot ^(sesion iniciada, cookies, etc.^)
echo   Ubicacion: C:\Temp\MsRewardsCDP
if exist "C:\Temp\MsRewardsCDP" (
    set /p ANS=  Borrar el perfil de Chrome ^(perderas la sesion^)? [s/N]:
    if /I "!ANS!"=="s" (
        REM Asegurar que no quede ningun chrome abierto sobre el perfil
        powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*MsRewardsCDP*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
        rmdir /S /Q "C:\Temp\MsRewardsCDP"
        echo   perfil eliminado.
    ) else (
        echo   conservado.
    )
) else (
    echo   no existe el perfil.
)
echo.

REM --- 5) Variables de entorno persistentes MSR_* (con confirmacion) ---
echo [5/5] Variables de entorno del usuario ^(MSR_USER_ID, MSR_MAINTAINER, MSR_OLLAMA_*^)
set /p ANS=  Quitar variables de entorno MSR_* persistentes? [s/N]:
if /I "!ANS!"=="s" (
    for %%V in (MSR_USER_ID MSR_MAINTAINER MSR_OLLAMA_URL MSR_OLLAMA_MODEL MSR_USER_DATA_DIR MSR_SEARCH_COUNT MSR_SEARCH_COUNT_L1 MSR_SEARCH_COUNT_L2 MSR_LOCALE MSR_CDP_PORT MSR_CHROME_PATH) do (
        reg delete "HKCU\Environment" /F /V %%V >nul 2>&1
    )
    echo   variables MSR_* eliminadas ^(afecta a nuevas sesiones de CMD^).
) else (
    echo   conservadas.
)
echo.

REM --- 6) venv y logs (con confirmacion conjunta) ---
echo [Extra] Entorno virtual ^(.venv^) y logs ^(logs\^)
set /p ANS=  Borrar .venv y logs/ tambien? [s/N]:
if /I "!ANS!"=="s" (
    if exist "%REWARDS%\.venv" rmdir /S /Q "%REWARDS%\.venv"
    if exist "%REWARDS%\logs"  rmdir /S /Q "%REWARDS%\logs"
    echo   .venv y logs eliminados.
) else (
    echo   conservados.
)
echo.

echo ============================================================
echo  Desinstalacion completada.
echo  Para borrar el proyecto entero, elimina manualmente:
echo    %ROOT%
echo ============================================================
echo.
pause
