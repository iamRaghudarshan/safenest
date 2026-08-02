@echo off
REM ===========================================================================
REM  Double-click this to package App into a portable bundle you can copy
REM  to another Windows PC or a Mac.
REM
REM  It asks whether to include your existing photos and records, then writes
REM  a folder (and optionally a .zip) you can carry on a USB drive.
REM ===========================================================================
title Create App Bundle
cd /d "%~dp0"

set "VENVPY=%~dp0backend\venv\Scripts\python.exe"

if not exist "%VENVPY%" (
    echo.
    echo   Could not find the App Python environment at:
    echo     %VENVPY%
    echo.
    echo   Run this from the machine App is installed on.
    echo.
    pause
    exit /b 1
)

"%VENVPY%" "%~dp0make_bundle.py"
