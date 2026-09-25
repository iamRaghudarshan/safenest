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
timeout /t 3 /nobreak >nul

REM  schtasks /end ends the TASK; it does not reliably take the task's process
REM  tree with it. The uvicorn parent survives, the task still reports Running,
REM  the /run below is then a no-op, and this script reports success while the
REM  OLD code is still serving every request. That failure is invisible and
REM  costs an afternoon, so kill whatever still holds the port before starting.
REM
REM  ONLY python.exe, AND ONLY THAT. This machine has a SECOND thing listening
REM  on 8080: the AI IPQC project serves PHP on ::1:8080 while this API has
REM  0.0.0.0:8080, and Windows allows both because the addresses differ. The
REM  unfiltered loop matched both netstat lines and killed the PHP server too,
REM  which nothing here restarts -- so restarting SafeNest quietly took an
REM  unrelated project down, and the only symptom was that project being off.
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:"TCP .*:8080 .*LISTENING"') do (
    tasklist /FI "PID eq %%P" /FI "IMAGENAME eq python.exe" 2>nul | findstr /i "python.exe" >nul && taskkill /F /PID %%P /T >nul 2>&1
)
timeout /t 2 /nobreak >nul

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

REM The point of the restart: probe a route that exists ONLY in the newest
REM code, so "it answered" cannot be confused with "it restarted". A 404 here
REM means the old build is still serving, which is more use than a cheerful
REM "done". Move this probe on whenever it stops being the newest thing.
REM
REM THIS PROBE WENT STALE ONCE AND WOULD HAVE COST THE AFTERNOON THE COMMENT
REM ABOVE WARNS ABOUT. It used to ask for /ai-bit-latest.json and expect 200 --
REM but AI BIT was deliberately removed from this server in September 2026, so
REM a perfectly good restart reported "still serving the old build" for ever
REM after.
REM
REM An authenticated route is a better probe than a public one, because the
REM answer distinguishes the two cases that matter without needing a token:
REM   401 = the route is THERE and wants a sign-in  -> new code
REM   404 = the route does not exist at all         -> old code still running
for /f %%A in ('curl.exe -s -o nul -w "%%{http_code}" http://127.0.0.1:8080/api/gallery/suggestions') do set CODE=%%A
if "%CODE%"=="401" (
    echo   New code confirmed - the suggestions route is live.
) else if "%CODE%"=="404" (
    echo   [!] Still serving the OLD build ^(got 404 - that route does not exist yet^).
) else (
    echo   [?] Unexpected answer %%CODE%% from the probe - check the API log.
)

:done
echo.
pause
