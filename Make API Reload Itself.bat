@echo off
REM ==========================================================================
REM  RUN THIS ONCE. Then code changes never need a restart again.
REM ==========================================================================
REM
REM  WHY IT EXISTS. The API runs as a SYSTEM scheduled task, so stopping it
REM  needs administrator -- which means every single deploy has needed somebody
REM  to walk over and double-click something. A fix that is written, tested and
REM  pushed but not running is not a fix, and that gap has cost this project
REM  days: the two videos that would not upload were repaired in the morning
REM  and were still failing that evening because nobody had restarted anything.
REM
REM  WHAT IT CHANGES. One thing: the task's command gains --reload, so uvicorn
REM  watches app/ and restarts its own worker the moment a .py file changes.
REM  Deploying becomes `git pull` and nothing else.
REM
REM  THE TRADE, stated plainly rather than buried: --reload keeps a file
REM  watcher running and costs a little memory, and a worker restarting
REM  mid-request drops that request. On a home server with one user that is
REM  nothing. On a machine serving customers it would be the wrong choice, so
REM  if this box ever becomes that, run "Restart App API.bat" and take the
REM  --reload back out.
REM
REM  Double-click it. Say Yes to the prompt. That is the whole thing.

net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo   Teaching the API to reload itself...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$t = Get-ScheduledTask -TaskName 'AppAPI' -ErrorAction Stop;" ^
  "$a = $t.Actions[0];" ^
  "if ($a.Arguments -match '--reload') { Write-Host '  Already reloading. Nothing to do.'; exit 0 };" ^
  "$new = $a.Arguments + ' --reload --reload-dir app';" ^
  "$act = New-ScheduledTaskAction -Execute $a.Execute -Argument $new -WorkingDirectory $a.WorkingDirectory;" ^
  "Set-ScheduledTask -TaskName 'AppAPI' -Action $act | Out-Null;" ^
  "Write-Host '  Done. Restarting it once so the change takes effect.'"

if %errorlevel% neq 0 (
    echo.
    echo   That did not work. Nothing was changed.
    echo   Use "Restart App API.bat" for now and say so.
    echo.
    pause
    exit /b 1
)

schtasks /end /tn "AppAPI" >nul 2>&1
timeout /t 3 /nobreak >nul
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:"TCP .*:8080 .*LISTENING"') do (
    tasklist /FI "PID eq %%P" /FI "IMAGENAME eq python.exe" 2>nul | findstr /i "python.exe" >nul && taskkill /F /PID %%P /T >nul 2>&1
)
timeout /t 2 /nobreak >nul
schtasks /run /tn "AppAPI" >nul 2>&1

echo.
echo   The API now picks up code changes by itself.
echo   You should not need to run either of these files again.
echo.
timeout /t 6 /nobreak >nul
