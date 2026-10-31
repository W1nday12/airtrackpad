@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
title AirTrackpad Server
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

rem Create the local virtual environment on first run.
if not exist "%VENV_PY%" (
    echo [*] Local environment not found. Setting up .venv ...
    if not exist "%~dp0tools\setup_env.ps1" (
        echo [X] Missing tools\setup_env.ps1 - cannot create the environment.
        pause
        exit /b 1
    )
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup_env.ps1"
    if errorlevel 1 (
        echo.
        echo [X] Environment setup failed. Check the messages above.
        pause
        exit /b 1
    )
)

if not exist "%VENV_PY%" (
    echo [X] Python interpreter not found: %VENV_PY%
    echo     Delete the .venv folder and run start.bat again.
    pause
    exit /b 1
)

"%VENV_PY%" server.py
if errorlevel 1 (
    echo.
    echo [X] Server stopped with an error.
    pause
    exit /b 1
)

endlocal
