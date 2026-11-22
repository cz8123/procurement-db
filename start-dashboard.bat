@echo off
setlocal EnableExtensions
title Procurement Dashboard - close this window to stop the service
REM ============================================================
REM  One-click launcher for /dash (portable across PCs/users)
REM  If service is up, only opens the page; otherwise starts app.py
REM  with OPEN_PATH=/dash. Close this window to stop the service.
REM ============================================================

set "APP=%~dp0app.py"
set "PORT=5000"
set "URL=http://localhost:%PORT%/dash"

if not exist "%APP%" (
  echo [ERROR] app.py not found: %APP%
  pause
  exit /b 1
)

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -c "import flask" >nul 2>&1 && set "PY=%~dp0.venv\Scripts\python.exe"
)
if not defined PY if exist "%USERPROFILE%\.workbuddy\binaries\python\envs\procurement\Scripts\python.exe" (
  "%USERPROFILE%\.workbuddy\binaries\python\envs\procurement\Scripts\python.exe" -c "import flask" >nul 2>&1 && set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\procurement\Scripts\python.exe"
)
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PY (
      "%%i" -c "import flask" >nul 2>&1 && set "PY=%%i"
    )
  )
)
if not defined PY (
  echo [ERROR] No Python with Flask found. Set it up with:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install flask openpyxl
  pause
  exit /b 1
)
echo [INFO] Python: %PY%

curl -s -o nul -m 2 "%URL%" >nul 2>&1
if not errorlevel 1 (
  echo [INFO] Service already running on port %PORT%. Opening %URL%
  start "" "%URL%"
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

set "OPEN_PATH=/dash"
echo Starting dashboard: %URL%
echo Close this window to stop the service.
echo.
cd /d "%~dp0"
"%PY%" "%APP%"
echo.
echo [STOPPED] exit code %errorlevel% - press any key to close.
pause >nul
