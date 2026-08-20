@echo off
cd /d "%~dp0\.."
title Build EndfieldCDHUD v2.3

where py >nul 2>nul
if errorlevel 1 goto TRY_PYTHON

py -3 -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    py -3 -m pip install pyinstaller
    if errorlevel 1 goto ERROR
)

echo.
echo Building EndfieldCDHUD_v2.3.exe...
py -3 -m PyInstaller --noconfirm --clean EndfieldCDHUD_v2.3.spec
if errorlevel 1 goto ERROR
goto SUCCESS

:TRY_PYTHON
where python >nul 2>nul
if errorlevel 1 goto NOPYTHON

python -m pip show pyinstaller >nul 2>nul
if errorlevel 1 (
    echo Installing PyInstaller...
    python -m pip install pyinstaller
    if errorlevel 1 goto ERROR
)

echo.
echo Building EndfieldCDHUD_v2.3.exe...
python -m PyInstaller --noconfirm --clean EndfieldCDHUD_v2.3.spec
if errorlevel 1 goto ERROR
goto SUCCESS

:SUCCESS
echo.
echo Build complete.
echo EXE: dist\EndfieldCDHUD_v2.3.exe
pause
exit /b 0

:NOPYTHON
echo.
echo Python was not found.
echo Please install Python 3 first and enable "Add Python to PATH".
pause
exit /b 1

:ERROR
echo.
echo Build failed.
pause
exit /b 1
