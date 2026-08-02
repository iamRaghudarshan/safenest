@echo off
REM ===========================================================================
REM  Double-click this to remove the App auto-start services.
REM  Your data (database files, backend\private, .env) is never touched.
REM  It re-launches itself elevated, so just accept the Windows UAC prompt.
REM ===========================================================================

net session >nul 2>&1
if %errorlevel%==0 goto :elevated

echo Requesting administrator rights...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "Start-Process -FilePath '%~f0' -Verb RunAs"
exit /b

:elevated
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0uninstall-services.ps1"
