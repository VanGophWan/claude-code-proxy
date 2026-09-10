@echo off
:: Switch to UTF-8 code page
chcp.com 65001 >nul 2>&1
:: Set console font to Consolas for better UTF-8 support
reg add HKCU\Console /v FaceName /t REG_SZ /d Consolas /f >nul 2>&1
reg add HKCU\Console /v CodePage /t REG_DWORD /d 65001 /f >nul 2>&1
title Claude Code Proxy

set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Cannot find .venv\Scripts\python.exe
    echo Please run: uv sync
    pause
    exit /b 1
)

:: Disable ANSI colors for uvicorn to fix garbled output on Windows CMD
set LANG=en_US.UTF-8
set LC_ALL=en_US.UTF-8
set TERM=dumb
set FORCE_COLOR=0
set NO_COLOR=1
set PYTHONIOENCODING=utf-8

echo [INFO] Starting Claude Code Proxy...

echo.
echo Select configuration file:
echo   1. .env (default)
echo   2. .env.nvidia
echo.
set /p choice="Enter choice (1 or 2, default=1): "

if "%choice%"=="2" (
    set ENV_FILE=.env.nvidia
) else (
    set ENV_FILE=.env
)
echo [INFO] Using: %ENV_FILE%

echo [INFO] Using: .venv\Scripts\python start_proxy.py
echo.

.venv\Scripts\python start_proxy.py

if errorlevel 1 (
    echo [ERROR] Server exited with code: %errorlevel%
    pause
)