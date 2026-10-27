@echo off
setlocal enabledelayedexpansion
title WUWEI CORE - INSTITUTIONAL LIVE LAUNCHER
color 0A

REM ===============================================================================
REM   WUWEI CORE QUANTITATIVE SYSTEM - LIVE RUNNER SHORTCUT
REM   This launcher automatically invokes the unified Master Launcher (start_bot.bat)
REM ===============================================================================

cd /d "%~dp0"
call "%~dp0start_bot.bat" %*
