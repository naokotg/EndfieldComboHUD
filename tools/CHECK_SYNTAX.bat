@echo off
cd /d "%~dp0\.."
title EndfieldCDHUD Syntax Check

where py >nul 2>nul
if not errorlevel 1 (
    py -3 -m py_compile src\EndfieldCDHUD.pyw src\EndfieldCDHUD_debug.py
    if errorlevel 1 goto ERROR
    goto OK
)

where python >nul 2>nul
if not errorlevel 1 (
    python -m py_compile src\EndfieldCDHUD.pyw src\EndfieldCDHUD_debug.py
    if errorlevel 1 goto ERROR
    goto OK
)

echo Python was not found.
pause
exit /b 1

:OK
echo Syntax check passed.
pause
exit /b 0

:ERROR
echo Syntax check failed.
pause
exit /b 1
