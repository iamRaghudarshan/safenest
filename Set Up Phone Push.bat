@echo off
setlocal enabledelayedexpansion
REM Finish the phone-push setup once the Firebase project exists.
REM
REM Everything on the server and in the app is already written. This is only
REM the part that could not be done for you: telling this installation which
REM Firebase project is yours, and where its service-account key lives.
REM
REM Nothing here is sent anywhere. The service-account key stays on this
REM machine and is what lets YOUR server talk to Firebase; the four public
REM values identify the project and are compiled into the phone app.

echo.
echo   ===============================================================
echo     Phone push setup
echo   ===============================================================
echo.
echo   You need, from console.firebase.google.com:
echo.
echo     1. The service account JSON file, saved somewhere on this PC
echo        (Project settings -^> Service accounts -^> Generate new private key)
echo.
echo     2. Four values from Project settings -^> General:
echo          Project ID, App ID, Web API Key, Sender ID
echo.
echo   Press Ctrl+C to stop if you do not have them yet.
echo.
pause

set /p SA="  Full path to the service account .json file: "
if not exist "!SA!" (
    echo.
    echo   [!] No file at that path. Nothing has been changed.
    pause
    exit /b 1
)

echo.
set /p PROJ="  Project ID  : "
set /p APPID="  App ID      : "
set /p APIKEY="  Web API Key : "
set /p SENDER="  Sender ID   : "

echo.
echo   Writing the server setting...

REM Appended rather than rewritten: .env holds the vault key, the licence
REM signing key and the JWT secret, and a script that rewrites it is a script
REM that can lose them.
set ENVFILE=%~dp0backend\.env
findstr /b /c:"FCM_SERVICE_ACCOUNT=" "%ENVFILE%" >nul 2>&1
if %errorlevel% equ 0 (
    echo   [!] FCM_SERVICE_ACCOUNT is already in backend\.env — edit it by hand
    echo       rather than have this add a second one.
) else (
    echo.>> "%ENVFILE%"
    echo # Firebase service account, for push to the phone app. See app/fcm.py.>> "%ENVFILE%"
    echo FCM_SERVICE_ACCOUNT=!SA!>> "%ENVFILE%"
    echo   Added FCM_SERVICE_ACCOUNT to backend\.env
)

REM The four public values go into a file the phone build reads, NOT into
REM .env — they are compiled into the app, and keeping them beside the secrets
REM invites one being sent where the other belongs.
set OUT=%~dp0..\safenest-mobile\firebase.env
(
  echo FCM_PROJECT_ID=!PROJ!
  echo FCM_APP_ID=!APPID!
  echo FCM_API_KEY=!APIKEY!
  echo FCM_SENDER_ID=!SENDER!
) > "%OUT%"
echo   Wrote the app values to safenest-mobile\firebase.env

echo.
echo   Now restart the API so it reads the new setting:
echo       "%~dp0Restart App API.bat"
echo.
echo   Then tell Claude "firebase is set up" and the phone build will be
echo   made with these values compiled in.
echo.
pause
