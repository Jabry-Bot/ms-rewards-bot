@echo off
REM ms_rewards - instalador por linea de comandos (FALLBACK de setup.exe).
REM Lo normal es usar setup.exe. Esto queda como alternativa si el .exe lo
REM bloquea el antivirus o se prefiere la consola. Vive en scripts/.

setlocal ENABLEDELAYEDEXPANSION
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "REWARDS=%ROOT%\ms_rewards"

echo ============================================================
echo  Instalando ms_rewards
echo ============================================================
echo.

REM --- 1) Comprobar / instalar Python 3.10+ ---
echo [1/5] Comprobando Python...
where python >nul 2>&1
if errorlevel 1 goto :install_python

python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python encontrado pero version ^< 3.10. Reinstalando...
    goto :install_python
)
echo Python OK.
goto :have_python

:install_python
echo Python no encontrado o demasiado antiguo. Instalando...

REM Via 1: winget
where winget >nul 2>&1
if not errorlevel 1 (
    echo Probando con winget...
    winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements
    if not errorlevel 1 goto :refresh_path
)

REM Via 2: descarga directa del instalador oficial
echo Descargando instalador oficial de python.org...
set "PY_INSTALLER=%TEMP%\python-3.12.7-amd64.exe"
curl.exe -L --fail -o "%PY_INSTALLER%" https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe
if errorlevel 1 (
    echo ERROR: no se pudo descargar el instalador de Python.
    echo Instala Python 3.12 manualmente desde https://www.python.org/downloads/
    pause
    exit /b 1
)
echo Ejecutando instalador silencioso...
"%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0
if errorlevel 1 (
    echo ERROR: el instalador devolvio un codigo de error.
    pause
    exit /b 1
)
del "%PY_INSTALLER%" >nul 2>&1

:refresh_path
REM Refrescar PATH desde el registro de usuario (HKCU\Environment) para esta sesion
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul ^| find "PATH"') do set "USER_PATH=%%B"
if defined USER_PATH set "PATH=%USER_PATH%;%PATH%"

where python >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python recien instalado no esta en PATH. Reinicia esta consola.
    pause
    exit /b 1
)

:have_python
for /f "tokens=*" %%V in ('python --version') do echo Usando: %%V
echo.

REM --- 2) Crear / actualizar venv ---
echo [2/5] Creando entorno virtual...
cd /d "%REWARDS%"
if not exist .venv (
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR creando venv.
        pause
        exit /b 1
    )
)
set "VENV_PY=%REWARDS%\.venv\Scripts\python.exe"

REM --- 3) Instalar dependencias ---
echo [3/5] Instalando dependencias...
"%VENV_PY%" -m pip install --upgrade pip --quiet
"%VENV_PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo ERROR instalando dependencias.
    pause
    exit /b 1
)

REM patchright necesita drivers a veces; los instalamos sin fallar si ya estan
echo Instalando drivers de patchright...
"%VENV_PY%" -m patchright install chrome >nul 2>&1
"%VENV_PY%" -m patchright install msedge >nul 2>&1

echo.

REM --- 4) Setup interactivo (USER_ID, credenciales, login, maintainer) ---
echo [4/5] Configuracion interactiva...
echo.
"%VENV_PY%" setup_cli.py
set "SETUP_RC=%errorlevel%"
echo.

REM --- 5) Scheduled Task ---
echo [5/5] Registrando Scheduled Task de Windows...
powershell -ExecutionPolicy Bypass -File "%REWARDS%\scheduler\install_task.ps1"
if errorlevel 1 (
    echo AVISO: no se pudo registrar la Scheduled Task. Puedes lanzar manualmente:
    echo   py run.py
)
echo.

echo ============================================================
if "%SETUP_RC%"=="0" (
    echo  Instalacion completada con exito.
) else (
    echo  Instalacion completada con avisos. Revisa los mensajes anteriores.
)
echo ============================================================
echo.
pause
exit /b %SETUP_RC%
