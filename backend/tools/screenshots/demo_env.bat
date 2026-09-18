@echo off
REM Throwaway SafeNest instance for taking product screenshots.
REM
REM Its own SQLite file, its own port, its own media root, and NO licence signing
REM key. Nothing here touches the live MySQL database on 3307 or the running
REM service on 8080 -- the whole point is that seeding realistic demo data for
REM marketing images must not put a single made-up row in the owner's records.
set DB_ENGINE=sqlite
set DB_FILE=%TEMP%\safenest-demo\demo.db
set MEDIA_ROOT=%TEMP%\safenest-demo\media
set SAFENEST_NO_TUNNEL=1
set LICENSE_SIGNING_KEY_HEX=
set PUBLIC_BASE_URL=
set JWT_SECRET=demo-only-not-a-real-secret-0000000000
set VAULT_KEY_HEX=00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff
set MEDIA_SECRET=demo-only-media-secret-000000000000
cd /d "D:\AI PRO\finmate-react\backend"
venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8099 --no-access-log
