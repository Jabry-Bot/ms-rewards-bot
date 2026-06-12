@echo off
REM Construye setup.exe (autocontenido, requiere admin) con PyInstaller.
REM Vive en scripts/; el repo es la carpeta padre.
setlocal

for %%I in ("%~dp0..") do set "ROOT=%%~fI"

rem --- Elegir interprete de build (con PyInstaller) ---
set "BUILD_PY="
if exist "%ROOT%\ms_rewards\.venv\Scripts\python.exe" (
    set "BUILD_PY=%ROOT%\ms_rewards\.venv\Scripts\python.exe"
) else (
    where python >nul 2>nul
    if not errorlevel 1 set "BUILD_PY=python"
)

if not defined BUILD_PY (
    echo [ERROR] No se encontro un Python para construir.
    echo         Crea el venv del bot ^(setup.exe^) o instala Python en el PATH.
    pause
    exit /b 1
)

echo [build] Usando interprete: %BUILD_PY%

rem --- Dependencias de build ---
"%BUILD_PY%" -m pip install --upgrade pyinstaller customtkinter
if errorlevel 1 (
    echo [ERROR] Fallo instalando dependencias de build.
    pause
    exit /b 1
)

cd /d "%ROOT%"

rem --- Construir setup.exe (autocontenido, requiere admin) ---
"%BUILD_PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name setup --collect-all customtkinter --paths "%ROOT%" "%ROOT%\scripts\run_setup.py"
if errorlevel 1 (
    echo [ERROR] PyInstaller fallo.
    pause
    exit /b 1
)

if exist "%ROOT%\dist\setup.exe" (
    echo.
    echo [OK] Generado: dist\setup.exe
    echo.
    echo IMPORTANTE: copia dist\setup.exe a la raiz del repo ^(junto a ms_rewards\^)
    echo para distribuirlo. El bootstrap ancla el repo a la carpeta del .exe.
) else (
    echo [ERROR] No se genero dist\setup.exe.
    pause
    exit /b 1
)

pause
endlocal
