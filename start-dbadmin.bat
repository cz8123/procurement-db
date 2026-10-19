@echo off
chcp 65001 >nul
setlocal
REM ============================================================
REM  Procurement DB Admin - launcher
REM  Kill any old process on port 5000, then start Flask.
REM  Your browser opens http://localhost:5000 automatically
REM  (set BROWSER=0 before launching to disable auto-open).
REM  Close this window to stop the service.
REM ============================================================

set "PY=C:/Users/CONG ZHU/.workbuddy/binaries/python/envs/procurement/Scripts/python.exe"
set "APP=%~dp0app.py"
set "PORT=5000"

if not exist "%PY%" (
  echo [ERROR] Python venv not found: %PY%
  echo         Create it:  "C:/Users/CONG ZHU/.workbuddy/binaries/python/versions/3.13.12/python.exe" -m venv "%PY%"
  echo         Then install:  "%PY%" -m pip install flask
  pause
  exit /b 1
)

if not exist "%APP%" (
  echo [ERROR] app.py not found: %APP%
  pause
  exit /b 1
)

REM Kill any old Flask process occupying the port (avoid Address already in use)
for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo [CLEAN] Killing old process on port %PORT%  PID=%%a
  taskkill /F /PID %%a >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo Starting Procurement DB Admin at http://localhost:%PORT% ...
echo Close this window to stop the service.
echo.
"%PY%" "%APP%"
echo.
echo [STOPPED exit code %errorlevel%]  Press any key to close.
pause >nul
