@echo off
rem Bring the Mac build back from GitHub after a release.
rem
rem Run this once per release, after pushing the version bump. It waits if the
rem build is still going, checks the archive really is the version this copy is
rem on, and puts it where the Licences screen looks. Every Mac customer's copy is
rem then built from that one file.
rem
rem Needs GITHUB_TOKEN in backend\.env — the script says how if it is missing.

setlocal
cd /d "%~dp0"

set PY=backend\venv\Scripts\python.exe
if not exist "%PY%" set PY=python

echo.
echo   Fetching the Mac build...
echo.
"%PY%" packaging\fetch_mac_build.py %*
set RC=%ERRORLEVEL%

echo.
if not "%RC%"=="0" (
  echo   Nothing was changed.
)
pause
endlocal
exit /b %RC%
