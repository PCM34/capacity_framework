@echo off
setlocal
cd /d "%~dp0"

echo === Capacity Hub: one-time setup ===

where python >nul 2>nul
if errorlevel 1 (
    echo ERROR: Python was not found on PATH.
    echo Install Python 3.10+ for Windows, then re-run this script.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv ...
    python -m venv .venv
) else (
    echo Virtual environment already exists, skipping creation.
)

echo Installing dependencies ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

echo.
echo Setup complete. Use launch.bat to start Capacity Hub.
pause
