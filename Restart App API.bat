@echo off
REM Restart the API so it picks up new code.
REM
REM WHY THIS FILE EXISTS. The API runs as a SYSTEM scheduled task so it
REM survives a logout and a reboot -- which is right, and means an ordinary
REM process cannot stop it. `schtasks /end /tn AppAPI` from a normal prompt
REM answers "ERROR: Access is denied." So a restart needs administrator, and
REM this asks for it, rather than leaving somebody to work out that it was
REM needed at all.
REM
REM Double-click it. Say Yes to the prompt. That is the whole thing.

net session >nul 2>&1
if %errorlevel% neq 0 (
    REM Not elevated. Re-launch this same file as administrator and stop.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo   Restarting the API...
echo.

schtasks /end /tn "AppAPI" >nul 2>&1
timeout /t 4 /nobreak >nul
schtasks /run /tn "AppAPI" >nul 2>&1

echo   Started. Waiting for it to answer...
echo.

REM Poll rather than sleep a fixed amount: a cold start loads the AI models and
REM takes a good deal longer than a warm one.
set TRIES=0
:wait
set /a TRIES+=1
timeout /t 3 /nobreak >nul
curl.exe -s -o nul -w "" http://127.0.0.1:8080/api/health >nul 2>&1
if %errorlevel% equ 0 goto up
if %TRIES% lss 25 goto wait

echo   [!] It did not answer in time. Check Task Scheduler for AppAPI.
goto done

:up
echo   The API is up.
echo.

REM The point of the restart: this route only exists in the new code. A 404
REM here means the old build is somehow still serving, and saying so is more
REM use than a cheerful "done".
for /f %%A in ('curl.exe -s -o nul -w "%%{http_code}" http://127.0.0.1:8080/api/auth/2fa') do set CODE=%%A
if "%CODE%"=="401" (
    echo   New code confirmed - two-step sign-in and video are live.
) else (
    echo   [!] Still serving the old build ^(got %CODE%, expected 401^).
)

:done
echo.
pause
