@echo off
title WUWEI CORE - WEB TELEMETRY DASHBOARD
color 0B

echo ===============================================================================
echo   WUWEI CORE — REAL-TIME QUANTITATIVE WEB TELEMETRY
echo   Dashboard URL: http://localhost:8080
echo ===============================================================================
echo.

cd /d "%~dp0"
python -m http.server 8080
pause
