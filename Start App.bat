@echo off
REM Double-click launcher for App 2.0 (React + FastAPI).
REM Bypasses PowerShell execution policy for this one script only.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-finmate-react.ps1"
pause
