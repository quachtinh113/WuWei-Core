@echo off
setlocal enabledelayedexpansion
title WUWEI CORE - WATCHDOG AND SELF-HEALING MONITOR
color 0B

echo ===============================================================================
echo   WUWEI CORE - WATCHDOG AND SELF-HEALING SYSTEM
echo   Continuous Process, Memory, and Heartbeat Telemetry Protection
echo ===============================================================================
echo.

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%~dp0;%PYTHONPATH%

python watchdog.py %*
