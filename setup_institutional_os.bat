@echo off
setlocal enabledelayedexpansion
title WUWEI CORE - INSTITUTIONAL OS OPTIMIZATION AND TASK SCHEDULER SETUP
color 0E

echo ===============================================================================
echo   WUWEI CORE - INSTITUTIONAL WINDOWS OS TUNING & TASK SCHEDULER
echo   Target: HP ProDesk / Dedicated Trading PC 24/7 Stability
echo ===============================================================================
echo.

REM --- Kiem tra quyen Administrator ---
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Ban can chay file nay bang quyen Administrator (Run as Administrator)!
    echo Hay chuot phai vao file setup_institutional_os.bat va chon "Run as administrator".
    pause
    exit /b 1
)

echo [1/4] Dang toi uu Power Plan (Chong Sleep / Standby 24/7)...
REM Thiet lap che do High Performance va cam Sleep khi cam nguon
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c >nul 2>&1
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change monitor-timeout-ac 15 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
echo       -> Power Plan: High Performance (Sleep disabled on AC).

echo.
echo [2/4] Dang tat Power Saving tren Card mang (Chong mat ket noi phien My)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { Disable-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue }; Write-Host '      -> Da vo hieu hoa che do tiet kiem nang luong tren cac Card mang hoat dong.'"

echo.
echo [3/4] Dang chan Windows tu dong Restart do Windows Update...
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "NoAutoRebootWithLoggedOnUsers" /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "AUOptions" /t REG_DWORD /d 2 /f >nul 2>&1
echo       -> Registry Windows Update: Da bat NoAutoRebootWithLoggedOnUsers.

echo.
echo [4/4] Dang dang ky Task Scheduler (Tu dong chay Bot va Watchdog khi Logon)...
set "BASE_DIR=%~dp0"
REM Bo dau gach cheo nguoc o cuoi BASE_DIR neu co
if "%BASE_DIR:~-1%"=="\" set "BASE_DIR=%BASE_DIR:~0,-1%"

set "BOT_BAT=%BASE_DIR%\start_bot.bat"
set "WATCHDOG_BAT=%BASE_DIR%\start_watchdog.bat"

REM Tao Task Scheduler cho WuWei Bot
schtasks /create /tn "WuWei_Core_Bot" /tr "\"%BOT_BAT%\"" /sc onlogon /rl highest /f >nul 2>&1
if %errorlevel% equ 0 (
    echo       -> Task Scheduler 'WuWei_Core_Bot': Da tao thanh cong (Khoi dong khi dang nhap).
) else (
    echo       -> [WARN] Khong the tao Task WuWei_Core_Bot.
)

REM Tao Task Scheduler cho WuWei Watchdog
schtasks /create /tn "WuWei_Core_Watchdog" /tr "\"%WATCHDOG_BAT%\"" /sc onlogon /rl highest /f >nul 2>&1
if %errorlevel% equ 0 (
    echo       -> Task Scheduler 'WuWei_Core_Watchdog': Da tao thanh cong (Khoi dong khi dang nhap).
) else (
    echo       -> [WARN] Khong the tao Task WuWei_Core_Watchdog.
)

echo.
echo ===============================================================================
echo   HOAN TAT TOI UU HE THONG INSTITUTIONAL TRADING!
echo.
echo   Luu y quan trong cho van hanh 24/7:
echo   1. Khi roi may: Nhan to hop phim [Win + L] de khoa man hinh (LOCK).
echo      TUYET DOI KHONG BAM "SIGN OUT" hoac "LOG OFF".
echo   2. Watchdog va Bot van chay ngam 100%% cong suat khi man hinh khoa.
echo   3. File log watchdog duoc luu tai: %BASE_DIR%\watchdog.log
echo ===============================================================================
pause
