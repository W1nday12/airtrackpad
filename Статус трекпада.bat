@echo off
chcp 65001 >nul
title AirTrackpad - status
rem Shows whether the server is running, its address, firewall and autostart state.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0WebTrackpad\tools\status.ps1" -Qr
echo.
pause >nul
