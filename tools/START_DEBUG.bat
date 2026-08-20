@echo off
cd /d "%~dp0\.."
title Endfield CD HUD v2.3 DEBUG

where py >nul 2>nul
if not errorlevel 1 (
    py -3 src\EndfieldCDHUD_debug.py
    if not errorlevel 1 exit /b 0
)

where python >nul 2>nul
if not errorlevel 1 (
    python src\EndfieldCDHUD_debug.py
    if not errorlevel 1 exit /b 0
)

echo.
echo Python exited with an error, or Python was not found.
pause
