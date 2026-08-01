@echo off
REM ===========================================================================
REM  Restart the FinMate backend safely.
REM
REM  WHY THIS EXISTS: starting uvicorn in a hidden window with no output
REM  redirection eventually hangs the whole app. Its access log writes to a
REM  console buffer that nothing ever drains; once that buffer fills, every log
REM  write blocks, so every request blocks. The app looks dead while using no
REM  CPU at all.
REM
REM  The fix is both of these together:
REM    --no-access-log            stop the per-request chatter
REM    output redirected to files nothing can fill up and block on
REM ===========================================================================
title Restart FinMate
cd /d "%~dp0"

set "PY=%~dp0backend\venv\Scripts\python.exe"
if not exist "%PY%" (
    echo   Could not find %PY%
    pause
    exit /b 1
)

echo   Stopping any running FinMate backend...
powershell -NoProfile -Command ^
  "Get-NetTCPConnection -State Listen -LocalPort 8080 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 3 /nobreak >nul

echo   Starting FinMate...
powershell -NoProfile -Command ^
  "Start-Process -FilePath '%PY%' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8080','--no-access-log','--no-server-header' -WorkingDirectory '%~dp0backend' -WindowStyle Hidden -RedirectStandardOutput '%~dp0backend\server.log' -RedirectStandardError '%~dp0backend\server.err.log'"

timeout /t 12 /nobreak >nul
powershell -NoProfile -Command ^
  "try { $r = Invoke-RestMethod 'http://127.0.0.1:8080/api/health' -TimeoutSec 10; if ($r.ok) { Write-Host '   FinMate is running.' -ForegroundColor Green } else { Write-Host '   Unexpected reply.' -ForegroundColor Yellow } } catch { Write-Host '   FinMate did not come up - see backend\server.err.log' -ForegroundColor Red }"

echo.
echo   Logs: backend\server.log  and  backend\server.err.log
echo.
pause
