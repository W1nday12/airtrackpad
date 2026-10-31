@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title AirTrackpad Server
cd /d "%~dp0"

rem Pick the Python launcher if available, otherwise fall back to python.exe.
set "PY=python"
py -3 -c "import sys" >nul 2>nul && set "PY=py -3"

%PY% --version >nul 2>nul
if errorlevel 1 (
    echo [X] Python not found. Install Python 3.10+ from https://python.org
    echo     and enable "Add Python to PATH" during installation.
    pause
    exit /b 1
)

%PY% -c "import websockets, qrcode, PIL" >nul 2>nul
if errorlevel 1 (
    echo [*] Installing required packages...
    %PY% -m pip install --disable-pip-version-check -r requirements.txt
    if errorlevel 1 (
        echo [X] Could not install dependencies. Check your internet connection.
        pause
        exit /b 1
    )
)

%PY% server.py
pause
