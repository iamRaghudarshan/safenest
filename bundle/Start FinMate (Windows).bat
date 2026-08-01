@echo off
REM ===========================================================================
REM  FinMate launcher for Windows. Double-click this file.
REM
REM  The first run installs everything and asks a few questions; later runs
REM  just start the app. Nothing is installed outside this folder except
REM  Python itself, if you don't already have it.
REM
REM  Written flat, with goto rather than nested if-blocks: cmd expands every
REM  %VAR% in a parenthesised block before running any of it, so a variable set
REM  inside a block reads as empty later in that same block.
REM ===========================================================================
title FinMate
cd /d "%~dp0"

REM Prefer the py launcher (handles multiple Python versions), then plain python.
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if defined PY goto :run
where python >nul 2>&1 && set "PY=python"
if defined PY goto :run

REM ---------------------------------------------------------- install Python
REM winget ships with Windows 10 (1809+) and 11, needs no admin rights for this
REM package, and adds python to PATH — the step people miss installing by hand.
where winget >nul 2>&1
if errorlevel 1 goto :nopython

echo.
echo   Python is not installed. Installing it now — this takes a few minutes.
echo.
winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements

REM winget only updates PATH for processes started afterwards, so re-read it from
REM the registry rather than making someone close and reopen the window.
REM
REM "call set" rather than "set": the stored PATH holds literal %SystemRoot%
REM entries, and a plain assignment copies them across unexpanded — which drops
REM System32 off the path and breaks every command that follows. call runs a
REM second expansion pass and resolves them.
set "UPATH="
set "SPATH="
for /f "skip=2 tokens=2,*" %%A in ('reg query HKCU\Environment /v PATH 2^>nul') do set "UPATH=%%B"
for /f "skip=2 tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul') do set "SPATH=%%B"
call set "PATH=%SPATH%;%UPATH%"

where py >nul 2>&1 && set "PY=py -3"
if defined PY goto :run
where python >nul 2>&1 && set "PY=python"
if defined PY goto :run

REM Still not on PATH — look where winget actually put it. More reliable than
REM the environment, which some shells cache regardless of what we do here.
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if defined PY goto :run

:nopython
echo.
echo   Python is not installed, and it could not be installed automatically.
echo.
echo   FinMate needs Python 3.10 or newer. To install it by hand:
echo.
echo     1. Go to  https://www.python.org/downloads/
echo     2. Download the latest Python for Windows
echo     3. IMPORTANT: tick "Add python.exe to PATH" on the first screen
echo     4. Finish the installer, then double-click this file again
echo.
pause
exit /b 1

:run
%PY% "%~dp0setup.py" %*

REM Keep the window open if setup exited with an error, so the reason is readable.
if errorlevel 1 (
    echo.
    pause
)
