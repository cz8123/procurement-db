@echo off
chcp 65001 >nul
title Export Dashboard Snapshot
setlocal
REM 重新生成离线单文件看板（exports\采购看板_单文件_*.html），完成后打开所在文件夹
set "PY=C:/Users/CONG ZHU/.workbuddy/binaries/python/envs/procurement/Scripts/python.exe"

cd /d "%~dp0"
if not exist "%PY%" (
  echo [ERROR] Python venv not found: %PY%
  pause
  exit /b 1
)

"%PY%" board_export.py %*
echo.
if errorlevel 1 (
  echo [FAILED] see messages above.
  pause
  exit /b 1
)
echo Done. Opening the exports folder...
start "" explorer "%~dp0exports"
