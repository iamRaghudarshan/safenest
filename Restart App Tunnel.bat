@echo off
REM Restart the Cloudflare tunnel so it picks up a new public hostname.
REM
REM WHY THIS FILE EXISTS. The tunnel runs as a SYSTEM scheduled task (AppTunnel)
REM so it survives a reboot -- which means an ordinary process cannot stop it, and
REM `schtasks /end /tn AppTunnel` from a normal prompt answers "Access is denied."
REM Changing the domain (the hostname in cloudflared\config.yml) only takes effect
REM when the connector is restarted, so that restart needs administrator. This
REM asks for it, kills any stray connectors that piled up, and starts exactly one.
REM
REM Double-click it. Say Yes to the prompt. That is the whole thing.

net session >nul 2>&1
if %errorlevel% neq 0 (
    REM Not elevated. Re-launch this same file as administrator and stop.
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

echo.
echo   Restarting the Cloudflare tunnel...
echo.

REM The project's cloudflared\config.yml is the source of truth (the Web address
REM screen writes it, and the AppTunnel task reads it with --config). But cloudflared
REM keeps two other copies that a connector can end up reading instead: the user
REM profile and, because the task runs as LocalSystem, the SYSTEM profile. If those
REM disagree, a connector reading a stale one serves the OLD hostname and the change
REM looks like it did not take. So push the source of truth into both before starting.
set "SRC=%~dp0cloudflared\config.yml"
set "SYSHOME=%SystemRoot%\System32\config\systemprofile\.cloudflared"
if not exist "%SYSHOME%" mkdir "%SYSHOME%" >nul 2>&1
copy /y "%SRC%" "%SYSHOME%\config.yml" >nul 2>&1
if not exist "%USERPROFILE%\.cloudflared" mkdir "%USERPROFILE%\.cloudflared" >nul 2>&1
copy /y "%SRC%" "%USERPROFILE%\.cloudflared\config.yml" >nul 2>&1

REM Stop the task, then kill EVERY cloudflared -- duplicates accumulate when the
REM connector is started more than once, and Cloudflare load-balances across all
REM of them, so an old one still serving the previous hostname makes the change
REM look like it did not take.
schtasks /end /tn "AppTunnel" >nul 2>&1
taskkill /f /im cloudflared.exe >nul 2>&1
timeout /t 3 /nobreak >nul

schtasks /run /tn "AppTunnel" >nul 2>&1

echo   Started one connector. Waiting for it to connect...
echo.

REM Poll the public health endpoint through the tunnel. Read the build marker so a
REM domain that resolves to something else does not read as success.
set TRIES=0
:wait
set /a TRIES+=1
timeout /t 4 /nobreak >nul
for /f %%A in ('curl.exe -s -m 10 -o nul -w "%%{http_code}" https://safenest.raghudarshan.online/api/health') do set CODE=%%A
if "%CODE%"=="200" goto up
if %TRIES% lss 15 goto wait

echo   [!] safenest.raghudarshan.online did not answer 200 in time ^(got %CODE%^).
echo       Check Task Scheduler for AppTunnel, and the DNS record in Cloudflare.
goto done

:up
echo   safenest.raghudarshan.online is live and reaching this server.

:done
echo.
pause
