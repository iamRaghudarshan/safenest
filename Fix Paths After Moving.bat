@echo off
REM ===========================================================================
REM  Run this once after copying the app to another computer or drive.
REM  It works out where everything now lives and repairs the config files that
REM  record full paths (MySQL's my.ini, the tunnel config, the helper scripts).
REM  Safe to run more than once.
REM ===========================================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0fix-paths.ps1"
pause
