@echo off
setlocal enabledelayedexpansion
title WUWEI CORE - SINGLE PROCESS LIVE MASTER DAEMON
color 0A

REM ===============================================================================
REM   WUWEI CORE QUANTITATIVE SYSTEM - SINGLE PROCESS MASTER LAUNCHER
REM   Philosophy: Robert Carver Systematic Portfolio x WuWei Live Engine
REM   Chế độ: DUY NHẤT 1 BOT & 1 TIẾN TRÌNH TRỰC TIẾP (NO BACKGROUND DAEMONS)
REM ===============================================================================

cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set PYTHONPATH=%~dp0;%PYTHONPATH%

REM --- Kiem tra tham so dac biet tu dong lenh ---
if /i "%~1"=="--status" goto show_status
if /i "%~1"=="-s" goto show_status
if /i "%~1"=="--setup-os" goto run_setup_os
if /i "%~1"=="--help" goto show_help
if /i "%~1"=="-h" goto show_help

echo ===============================================================================
echo   WUWEI CORE QUANTITATIVE SYSTEM - SINGLE PROCESS MASTER LAUNCHER
echo   Architecture: Robert Carver Systematic Portfolio x WuWei Live Engine
echo   Che do: DUY NHAT 1 BOT TRUC TIEP TAI TERMINAL NAY [1 SINGLE PROCESS]
echo ===============================================================================
echo.

REM --- [1/4] Kiem tra moi truong .env va Python ---
echo [1/4] Kiem tra moi truong va file cau hinh...
if not exist "%~dp0.env" (
    echo [ERROR] Khong tim thay file .env tai %~dp0
    echo Vui long kiem tra .env voi MT5_LOGIN, MT5_PASSWORD, MT5_SERVER.
    pause
    exit /b 1
)

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python chua duoc cai dat hoac chua duoc them vao System PATH!
    pause
    exit /b 1
)
echo       -^> Moi truong Python va file .env: SAN SANG.
echo.

REM --- [2/4] Toi uu hoa Windows OS 24/7 ---
echo [2/4] Kiem tra va toi uu hoa Windows OS cho van hanh 24/7...
powercfg /setactive 8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c >nul 2>&1
powercfg /change standby-timeout-ac 0 >nul 2>&1
powercfg /change hibernate-timeout-ac 0 >nul 2>&1
powercfg /change monitor-timeout-ac 15 >nul 2>&1
echo       -^> Power Plan: High Performance - Sleep va Standby da tat khi cam nguon.

net session >nul 2>&1
if %errorlevel% equ 0 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetAdapter | Where-Object { $_.Status -eq 'Up' } | ForEach-Object { Disable-NetAdapterPowerManagement -Name $_.Name -ErrorAction SilentlyContinue }" >nul 2>&1
    reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v "NoAutoRebootWithLoggedOnUsers" /t REG_DWORD /d 1 /f >nul 2>&1
    echo       -^> Administrator: Da cau hinh chong ngat mang va chong tu dong reboot.
) else (
    echo       -^> Quyen Administrator: Standard User - Bo qua buoc sua Registry va Card mang.
)
echo.

REM --- [3/4] Kiem tra va tu dong khoi dong MetaTrader 5 Terminal ---
echo [3/4] Kiem tra MetaTrader 5 Terminal [terminal64.exe]...
tasklist /fi "imagename eq terminal64.exe" 2>nul | findstr /i "terminal64.exe" >nul
if %errorlevel% equ 0 goto mt5_ready

echo       -^> MT5 chua bat. Dang tu dong khoi dong terminal64.exe...
set "MT5_EXE=C:\Program Files\MetaTrader 5 EXNESS\terminal64.exe"
if exist "!MT5_EXE!" (
    start "" "!MT5_EXE!"
    echo       -^> Da phat lenh khoi dong MT5. Cho 25s de MT5 dang nhap Exness Server...
    timeout /t 25 /nobreak >nul 2>&1
    echo       -^> MT5 Terminal da san sang!
) else (
    echo       -^> [WARN] Khong tim thay terminal64.exe tai duong dan mac dinh Exness.
)
goto mt5_done

:mt5_ready
echo       -^> MetaTrader 5 Terminal dang hoat dong san sang.

:mt5_done
echo.

REM --- [4/4] Don dep tat ca tien trinh cu - Dam bao DUY NHAT 1 BOT ---
echo [4/4] Dam bao duy nhat 1 tien trinh Bot trong toan he thong...
python bot_status.py --kill-bot >nul 2>&1
python bot_status.py --kill-watchdog >nul 2>&1
echo       -^> Da don dep sach tien trinh cu. Khoi tao phien giao dich duy nhat!
echo.

set RESTART_COUNT=0
set MAX_RESTARTS=50
set RESTART_DELAY=15

echo ===============================================================================
echo   KHOI CHAY CHU TRINH GIAO DICH WUWEI LIVE ENGINE TRUC TIEP TAI DAY...
echo   (Nhan Ctrl+C bat cu luc nao de dung Bot an toan)
echo ===============================================================================
echo.

:execution_cycle
set /a RESTART_COUNT+=1

if %RESTART_COUNT% GTR %MAX_RESTARTS% goto max_restart_reached

echo [%date% %time%] Khoi dong Bot truc tiep (Lan %RESTART_COUNT% / %MAX_RESTARTS%)...
echo.

python live_trade.py %*

set EXIT_CODE=%ERRORLEVEL%

if %EXIT_CODE% EQU 0 goto graceful_exit

echo.
echo ===============================================================================
echo  [CANH BAO] Bot bi ngat ket noi hoac gap loi voi ma thoat: %EXIT_CODE%.
echo  Tu dong ket noi lai sau %RESTART_DELAY% giay... (Nhan Ctrl+C de huy)
echo ===============================================================================
timeout /t %RESTART_DELAY% /nobreak
goto execution_cycle

:graceful_exit
echo.
echo ===============================================================================
echo  [INFO] Bot da dung hoan toan va an toan (Exit code 0).
echo ===============================================================================
exit /b 0

:max_restart_reached
echo.
echo ===============================================================================
echo  [ALERT] Da dat gioi han %MAX_RESTARTS% lan tu dong khoi dong lai!
echo  Vui long kiem tra execution_audit.log de xu ly loi.
echo ===============================================================================
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM SUBROUTINES: Chuc nang mo rong (--status, --setup-os, --help)
REM ---------------------------------------------------------------------------

:show_status
echo Dang kiem tra toan dien trang thai tien trinh WuWei Core...
python bot_status.py
exit /b 0

:run_setup_os
call "%~dp0setup_institutional_os.bat"
exit /b %ERRORLEVEL%

:show_help
echo ===============================================================================
echo   WUWEI CORE - SINGLE PROCESS MASTER LAUNCHER HELP
echo ===============================================================================
echo.
echo Cach su dung:
echo   start_bot.bat [tuy chon]
echo.
echo Cac tuy chon:
echo   --status, -s        Kiem tra trang thai tien trinh MT5, Bot va Telemetry
echo   --setup-os          Chay toi uu hoa toan dien Windows OS 24/7 (Can Admin)
echo   --dry-run           Chay Bot o che do mo phong tin hieu truc tiep (Khong gui lenh vao MT5)
echo   --symbols [LIST]    Chon danh sach cap giao dich (Vi du: --symbols XAUUSD,EURUSD)
echo   --help, -h          Hien thi huong dan nay
echo.
exit /b 0
