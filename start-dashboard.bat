@echo off
chcp 65001 >nul
title Procurement Dashboard - close this window to stop the service
setlocal
REM ============================================================
REM  One-click launcher for the procurement dashboard (/dash).
REM  - If the service is already listening on the port, it only
REM    opens the page and exits (no duplicate process).
REM  - Otherwise it starts app.py with OPEN_PATH=/dash, so the
REM    browser lands on the dashboard instead of the table list.
REM  - Close the console window to stop the service.
REM ============================================================

set "PY=C:/Users/CONG ZHU/.workbuddy/binaries/python/envs/procurement/Scripts/python.exe"
set "APP=%~dp0app.py"
set "PORT=5000"
set "URL=http://localhost:%PORT%/dash"

if not exist "%PY%" (
  echo [ERROR] Python venv not found: %PY%
  pause
  exit /b 1
)
if not exist "%APP%" (
  echo [ERROR] app.py not found: %APP%
  pause
  exit /b 1
)

REM Service already up? Then only open the page.
REM (probe with curl: netstat output spacing is not stable enough to match)
curl -s -o nul -m 2 "%URL%" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Service already running on port %PORT%. Opening %URL%
  start "" "%URL%"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

REM app.py opens this path automatically once the server is ready.
set "OPEN_PATH=/dash"
echo Starting dashboard: %URL%
echo Close this window to stop the service.
echo.
"%PY%" "%APP%"

echo.
echo [STOPPED] exit code %errorlevel% - press any key to close.
pause >nul
