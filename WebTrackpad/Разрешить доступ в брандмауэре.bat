@echo off
chcp 65001 >nul
title AirTrackpad — брандмауэр
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0allow_firewall.ps1"
