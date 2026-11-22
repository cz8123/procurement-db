@echo off
setlocal EnableExtensions
title Procurement DB Admin - close this window to stop the service
REM ============================================================
REM  Procurement DB Admin launcher (portable across PCs/users)
REM  Python lookup: project .venv -> WorkBuddy user venv -> PATH
REM  Kill old process on port 5000, then start Flask.
REM  Set BROWSER=0 to disable auto-opening the browser.
REM  Close this window to stop the service.
REM ============================================================

set "APP=%~dp0app.py"
set "PORT=5000"

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

for /f "tokens=5" %%a in ('netstat -ano 2^>nul ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
  echo [CLEAN] Killing old process on port %PORT%  PID=%%a
  taskkill /F /PID %%a >nul 2>&1
)
ping -n 2 127.0.0.1 >nul

echo Starting Procurement DB Admin at http://localhost:%PORT% ...
echo Close this window to stop the service.
echo.
cd /d "%~dp0"
"%PY%" "%APP%"
echo.
echo [STOPPED exit code %errorlevel%] Press any key to close.
pause >nul
