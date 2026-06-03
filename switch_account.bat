@echo off
REM ms_rewards - cambiar de cuenta Microsoft.
REM Toda la logica robusta (kill + espera + wipe con reintentos + login
REM forzado) vive en ms_rewards\switch_account.py.

setlocal
set "ROOT=%~dp0"
set "REWARDS=%ROOT%ms_rewards"
set "VENV_PY=%REWARDS%\.venv\Scripts\python.exe"

echo ============================================================
echo  Cambiar de cuenta - ms_rewards
echo ============================================================
echo.
echo  Esto DESCONECTA la cuenta actual:
echo    - Cierra el Chrome del bot y espera a que libere el perfil
echo    - Borra la sesion guardada y las credenciales
echo  Despues te pedira el email/contrasena de la cuenta NUEVA.
echo.

set /p ANS=  Continuar? [s/N]:
if /I not "%ANS%"=="s" (
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

cd /d "%REWARDS%"
"%VENV_PY%" switch_account.py
set "RC=%errorlevel%"
echo.
pause
exit /b %RC%
