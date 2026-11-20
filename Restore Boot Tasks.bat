@echo off
REM Double-click this to make SafeNest come back after a reboot again.
REM It re-launches itself elevated, so just accept the Windows UAC prompt.
REM
REM Registering a SYSTEM scheduled task and changing a service's start type
REM both need administrator. Nothing in here can be done without it, which is
REM why it could not be done for you.

net session >nul 2>&1
if %errorlevel%==0 goto :elevated

echo Requesting administrator rights...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:elevated
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore-boot-tasks.ps1"
