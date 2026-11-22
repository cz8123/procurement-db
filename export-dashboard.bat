@echo off
setlocal EnableExtensions
title Export Dashboard Snapshot
REM Regenerate the offline single-file dashboard (exports\).
REM Python lookup: project .venv -> WorkBuddy user venv -> PATH

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

cd /d "%~dp0"
"%PY%" board_export.py %*
echo.
if errorlevel 1 (
  echo [FAILED] see messages above.
  pause
  exit /b 1
)
echo Done. Opening the exports folder...
start "" explorer "%~dp0exports"
