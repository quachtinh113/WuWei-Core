@echo off
setlocal enabledelayedexpansion
title WUWEI CORE - INSTITUTIONAL LIVE TRADING DAEMON
color 0A

echo ===============================================================================
echo   WUWEI CORE QUANTITATIVE SYSTEM - INSTITUTIONAL LIVE DAEMON
echo   Philosophy: WuWei Trend Following x Robert Carver (pysystemtrade)
echo   Live Telemetry and Multi-Stage Quantitative Execution Pipeline
echo ===============================================================================
echo.

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%~dp0;%PYTHONPATH%

if not exist "%~dp0.env" (
    echo [ERROR] File .env not found in %~dp0
    echo Please make sure .env exists with MT5_LOGIN, MT5_PASSWORD, MT5_SERVER.
    pause
    exit /b 1
)
echo [1/3] Config .env ready.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not in PATH!
    pause
    exit /b 1
)
echo [2/3] Python environment ready.

set RESTART_COUNT=0
set MAX_RESTARTS=50
set RESTART_DELAY=15

echo [3/3] Starting WuWei Core Live Execution Loop...
echo.

:start_loop
set /a RESTART_COUNT+=1

if %RESTART_COUNT% GTR %MAX_RESTARTS% goto max_restart_reached

echo [%date% %time%] Starting trading cycle (Attempt %RESTART_COUNT% of %MAX_RESTARTS%)...
echo.

python live_trade.py %*

set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 goto graceful_exit

echo.
echo ===============================================================================
echo  [ALERT] Bot disconnected or crashed with exit code %EXIT_CODE%.
echo  Auto-restarting in %RESTART_DELAY% seconds... (Press Ctrl+C to cancel)
echo ===============================================================================
timeout /t %RESTART_DELAY% /nobreak
goto start_loop

:graceful_exit
echo.
echo ===============================================================================
echo  [INFO] Bot stopped gracefully (Exit code 0).
echo ===============================================================================
exit /b 0

:max_restart_reached
echo.
echo ===============================================================================
echo  [ALERT] Max restarts reached [%MAX_RESTARTS%]. Please check logs and fix issues.
echo ===============================================================================
pause
exit /b 1
