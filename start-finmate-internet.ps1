# start-finmate-internet.ps1
# Serves App (app + API) on http://127.0.0.1:8080 for a Cloudflare Tunnel to
# publish as a public HTTPS URL — usable from any device, anywhere, while this PC is on.
# Prerequisite: MySQL running (your normal "Start App" starts it) and cloudflared installed.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$port = 8080

Write-Host "Building the app (frontend/dist)..." -ForegroundColor Cyan
Push-Location "$root\frontend"
npm run build
Pop-Location

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host " App is serving locally on http://127.0.0.1:$port" -ForegroundColor Green
Write-Host ""
Write-Host " In a SEPARATE terminal, start the public tunnel:" -ForegroundColor Yellow
Write-Host "   Quick (temporary URL, no account):"
Write-Host "       cloudflared tunnel --url http://127.0.0.1:$port"
Write-Host "   -> it prints a https://<random>.trycloudflare.com address."
Write-Host ""
Write-Host " Open that URL on ANY device, anywhere. Keep this PC on." -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""

Set-Location "$root\backend"
& ".\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
