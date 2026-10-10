@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
    py -3 start_demo.py
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo Python is missing. Install Python 3.12 from https://www.python.org/downloads/
        echo Enable "Add python.exe to PATH" during installation.
        pause
        exit /b 1
    )
    python start_demo.py
)
if errorlevel 1 (
    echo.
    echo Demo startup failed. Please read the error above.
    pause
    exit /b 1
)
