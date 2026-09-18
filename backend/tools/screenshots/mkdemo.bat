@echo off
REM Create the demo account inside the THROWAWAY sqlite database only.
set DB_ENGINE=sqlite
set DB_FILE=%TEMP%\safenest-demo\demo.db
set MEDIA_ROOT=%TEMP%\safenest-demo\media
set SAFENEST_NO_TUNNEL=1
set JWT_SECRET=demo-only-not-a-real-secret-0000000000
set VAULT_KEY_HEX=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
set MEDIA_SECRET=demo-only-media-secret-000000000000
cd /d "D:\AI PRO\finmate-react\backend"
venv\Scripts\python.exe create_admin.py "Priya Sharma" priya@example.com "DemoHouse#2026"
