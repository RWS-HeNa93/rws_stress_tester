@echo off
setlocal enabledelayedexpansion

:: ============================================================
::  RWS Stress Tester - Build Script
::  Compiles the application into a standalone .exe using
::  PyInstaller (--onefile mode).
:: ============================================================

set "PROJECT_DIR=%~dp0"
set "VENV_PYTHON=%PROJECT_DIR%.venv\Scripts\python.exe"
set "DIST_ROOT=%PROJECT_DIR%dist"
set "PACKAGE_DIR=%DIST_ROOT%\RWS_StressTester"
set "EXE_NAME=RWS_StressTester.exe"
set "BUILT_EXE=%DIST_ROOT%\%EXE_NAME%"

echo.
echo ============================================================
echo   RWS Stress Tester - Build Script
echo ============================================================
echo.

:: ------------------------------------------------------------------
:: 1. Check for virtual environment
:: ------------------------------------------------------------------
if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found at .venv
    echo         Please create one first:
    echo           python -m venv .venv
    echo           .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

:: ------------------------------------------------------------------
:: 2. Install / upgrade PyInstaller inside the venv
:: ------------------------------------------------------------------
echo [1/5] Ensuring PyInstaller is installed...
"%VENV_PYTHON%" -m pip install --upgrade pyinstaller >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)
echo       Done.
echo.

:: ------------------------------------------------------------------
:: 3. Locate customtkinter package directory (needed for theme data)
:: ------------------------------------------------------------------
echo [2/5] Locating customtkinter package...

:: Write a small helper script and capture its output via temp file
>"%PROJECT_DIR%_ctk_find.py" echo import customtkinter, os; print(os.path.dirname(customtkinter.__file__))
"%VENV_PYTHON%" "%PROJECT_DIR%_ctk_find.py" >"%PROJECT_DIR%_ctk_path.txt" 2>&1
set /p CTK_PATH=<"%PROJECT_DIR%_ctk_path.txt"
del /q "%PROJECT_DIR%_ctk_find.py" 2>nul
del /q "%PROJECT_DIR%_ctk_path.txt" 2>nul

if not defined CTK_PATH (
    echo [ERROR] Could not locate customtkinter package.
    pause
    exit /b 1
)
echo       Found: %CTK_PATH%
echo.

:: ------------------------------------------------------------------
:: 4. Run PyInstaller
:: ------------------------------------------------------------------
echo [3/5] Running PyInstaller...
echo       This may take a minute or two.
echo.

cd /d "%PROJECT_DIR%"
"%VENV_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --uac-admin --name "RWS_StressTester" --add-data "%CTK_PATH%;customtkinter/" --hidden-import "customtkinter" --hidden-import "psutil" --hidden-import "reportlab" --hidden-import "reportlab.lib" --hidden-import "reportlab.platypus" --hidden-import "reportlab.graphics" --hidden-import "pythonnet" --hidden-import "clr" --hidden-import "clr_loader" --collect-all "pythonnet" --collect-all "clr_loader" --distpath dist --workpath build --specpath . main.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller build failed. See output above.
    pause
    exit /b 1
)
echo.
echo       PyInstaller finished successfully.
echo.

:: ------------------------------------------------------------------
:: 5. Assemble deployable package next to the .exe
:: ------------------------------------------------------------------
echo [4/5] Assembling deployable package...

if exist "%PACKAGE_DIR%" (
    echo       Cleaning previous package folder...
    rmdir /S /Q "%PACKAGE_DIR%"
)
mkdir "%PACKAGE_DIR%"

if not exist "%BUILT_EXE%" (
    echo [ERROR] Built executable was not found:
    echo         %BUILT_EXE%
    pause
    exit /b 1
)

echo       Placing executable into package folder...
if exist "%PACKAGE_DIR%\%EXE_NAME%" del /Q "%PACKAGE_DIR%\%EXE_NAME%" >nul 2>&1
copy /Y "%BUILT_EXE%" "%PACKAGE_DIR%\%EXE_NAME%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to copy executable into package folder.
    pause
    exit /b 1
)
if not exist "%PACKAGE_DIR%\%EXE_NAME%" (
    echo [ERROR] Packaged executable was not created:
    echo         %PACKAGE_DIR%\%EXE_NAME%
    pause
    exit /b 1
)
del /Q "%BUILT_EXE%" >nul 2>&1

:: Copy the Engines directory (all stress-test binaries)
if exist "%PROJECT_DIR%Engines" (
    echo       Copying Engines...
    xcopy "%PROJECT_DIR%Engines" "%PACKAGE_DIR%\Engines" /E /I /H /Y >nul
)

:: Drop debugging symbols and LibreHardwareMonitor's saved GUI settings, which a local
:: LibreHardwareMonitor.exe run may have written next to the engines
if exist "%PACKAGE_DIR%\Engines" (
    del /S /Q "%PACKAGE_DIR%\Engines\*.pdb" >nul 2>&1
    del /Q "%PACKAGE_DIR%\Engines\LibreHardwareMonitor\LibreHardwareMonitor.config" >nul 2>&1
)

:: Copy default config files
if exist "%PROJECT_DIR%settings.json" (
    echo       Copying settings.json...
    copy /Y "%PROJECT_DIR%settings.json" "%PACKAGE_DIR%\settings.json" >nul
)
if exist "%PROJECT_DIR%presets.json" (
    echo       Copying presets.json...
    copy /Y "%PROJECT_DIR%presets.json" "%PACKAGE_DIR%\presets.json" >nul
)
if exist "%PROJECT_DIR%README_DIST.txt" (
    echo       Copying packaged README...
    copy /Y "%PROJECT_DIR%README_DIST.txt" "%PACKAGE_DIR%\README.txt" >nul
)
if exist "%PROJECT_DIR%LICENSE" (
    echo       Copying license files...
    copy /Y "%PROJECT_DIR%LICENSE" "%PACKAGE_DIR%\LICENSE.txt" >nul
    copy /Y "%PROJECT_DIR%THIRD_PARTY_NOTICES.md" "%PACKAGE_DIR%\THIRD_PARTY_NOTICES.md" >nul
    xcopy "%PROJECT_DIR%licenses" "%PACKAGE_DIR%\licenses" /E /I /H /Y >nul
)
if exist "%PROJECT_DIR%Logotypes" (
    echo       Copying Logotypes...
    xcopy "%PROJECT_DIR%Logotypes" "%PACKAGE_DIR%\Logotypes" /E /I /H /Y >nul
)

:: Create empty runtime directories
if not exist "%PACKAGE_DIR%\results" mkdir "%PACKAGE_DIR%\results"
if not exist "%PACKAGE_DIR%\reports" mkdir "%PACKAGE_DIR%\reports"
if not exist "%PACKAGE_DIR%\Logotypes" mkdir "%PACKAGE_DIR%\Logotypes"

echo       Done.
echo.

:: ------------------------------------------------------------------
:: 6. Summary
:: ------------------------------------------------------------------
echo [5/5] Build complete!
echo.
echo ============================================================
echo   Output location:
echo     %PACKAGE_DIR%\
echo.
echo   To run the application:
echo     %PACKAGE_DIR%\%EXE_NAME%
echo.
echo   Packaged support files:
echo     %PACKAGE_DIR%\README.txt
echo     %PACKAGE_DIR%\Logotypes\
echo.
echo   To deploy to another machine, copy the ENTIRE
echo   RWS_StressTester folder from dist\ to the target PC.
echo ============================================================
echo.
pause