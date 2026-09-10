@echo off
chcp 65001 >nul 2>&1
echo [INFO] Stopping Claude Code Proxy...

set "FOUND="
for /f "tokens=1 delims= " %%a in ('tasklist /fi "imagename eq python.exe" /fo csv /nh 2^>nul ^| findstr /i "start_proxy main"') do set FOUND=1

if defined FOUND (
    taskkill /f /im python.exe >nul 2>&1
    echo [OK] Proxy stopped
) else (
    echo [INFO] No running proxy found
)

timeout /t 2 /nobreak >nul