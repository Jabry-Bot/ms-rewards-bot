@echo off
REM ms_rewards - cambiar de cuenta Microsoft.
REM Desconecta la cuenta actual (borra sesion de Chrome + credenciales) y
REM lanza el login para una cuenta nueva.

setlocal ENABLEDELAYEDEXPANSION
set "ROOT=%~dp0"
set "REWARDS=%ROOT%ms_rewards"
set "VENV_PY=%REWARDS%\.venv\Scripts\python.exe"

echo ============================================================
echo  Cambiar de cuenta - ms_rewards
echo ============================================================
echo.
echo  Esto va a DESCONECTAR la cuenta actual:
echo    - Cierra el Chrome del bot
echo    - Borra la sesion guardada (perfil de Chrome)
echo    - Borra las credenciales cifradas
echo  Despues te pedira el email/contrasena de la cuenta NUEVA.
echo.

set /p ANS=  Continuar? [s/N]:
if /I not "!ANS!"=="s" (
    echo Cancelado.
    pause
    exit /b 0
)
echo.

if not exist "%VENV_PY%" (
    echo ERROR: no encuentro el entorno virtual. Ejecuta setup.bat primero.
    pause
    exit /b 1
)

REM --- 1) Cerrar Chrome del bot ---
echo [1/4] Cerrando Chrome del bot...
cd /d "%REWARDS%"
"%VENV_PY%" run.py --kill
REM Por si acaso, matar cualquier chrome.exe sobre el perfil del bot
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | Where-Object { $_.CommandLine -like '*MsRewardsCDP*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" 2>nul
echo.

REM --- 2) Borrar la sesion (perfil de Chrome) ---
echo [2/4] Borrando sesion de Chrome...
REM Resolver la ruta real del perfil preguntando a config.py (respeta MSR_USER_DATA_DIR)
for /f "usebackq delims=" %%P in (`"%VENV_PY%" -c "import config; print(config.USER_DATA_DIR)"`) do set "PROFILE=%%P"
if defined PROFILE (
    if exist "!PROFILE!" (
        rmdir /S /Q "!PROFILE!"
        echo   perfil eliminado: !PROFILE!
    ) else (
        echo   no habia perfil en !PROFILE!
    )
) else (
    echo   AVISO: no se pudo determinar la ruta del perfil.
)
echo.

REM --- 3) Borrar credenciales + state ---
echo [3/4] Borrando credenciales y estado...
if exist "%REWARDS%\state\credentials.bin" del /Q "%REWARDS%\state\credentials.bin"
if exist "%REWARDS%\state\last_run.json" del /Q "%REWARDS%\state\last_run.json"
echo   credenciales y state borrados.
echo.

REM --- 4) Login con la cuenta nueva ---
echo [4/4] Configurando cuenta nueva...
echo.
"%VENV_PY%" setup_cli.py
set "RC=%errorlevel%"
echo.

echo ============================================================
if "%RC%"=="0" (
    echo  Cuenta cambiada correctamente.
) else (
    echo  El login no se confirmo. Revisa los mensajes de arriba.
    echo  Puedes reintentar ejecutando este mismo switch_account.bat.
)
echo ============================================================
echo.
pause
exit /b %RC%
