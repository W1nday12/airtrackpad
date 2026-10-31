@echo off
chcp 65001 >nul
setlocal
set "PYTHONIOENCODING=utf-8"
title AirTrackpad Server
cd /d "%~dp0"

set "VENV_PY=%~dp0.venv\Scripts\python.exe"

rem Run from the local virtual environment; create it on first run.
if exist "%VENV_PY%" goto :run

echo [*] Local environment not found. Setting up .venv ...
if not exist "%~dp0tools\setup_env.ps1" (
    echo [X] Missing tools\setup_env.ps1 - cannot create the environment.
    goto :fail
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\setup_env.ps1"
if errorlevel 1 (
    echo.
    echo [X] Environment setup failed. Check the messages above.
    goto :fail
)

if not exist "%VENV_PY%" (
    echo [X] Python interpreter not found: %VENV_PY%
    echo     Delete the .venv folder and run start.bat again.
    goto :fail
)

:run
"%VENV_PY%" server.py
if errorlevel 1 (
    echo.
    echo [X] Server stopped with an error.
    goto :fail
)
endlocal
exit /b 0

:fail
pause
endlocal
exit /b 1
