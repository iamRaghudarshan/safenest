# start-finmate-tailscale.ps1
# Serves App (app + API) on a LOCAL http port for Tailscale to publish privately.
# Tailscale then exposes it over HTTPS to ONLY your own devices, from anywhere.
# Prerequisite: MySQL running (your normal "Start App" starts it).

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
Write-Host " ONE-TIME, in a SEPARATE terminal, publish it on your tailnet:" -ForegroundColor Yellow
Write-Host "     tailscale serve --bg --https=443 http://127.0.0.1:$port"
Write-Host ""
Write-Host " Then find your address:"
Write-Host "     tailscale status        (shows this PC's name)"
Write-Host " and open it on any of YOUR devices (even on mobile data):"
Write-Host "     https://<this-pc-name>.<your-tailnet>.ts.net"
Write-Host ""
Write-Host " To stop publishing later:  tailscale serve --https=443 off" -ForegroundColor DarkGray
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""

Set-Location "$root\backend"
& ".\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port
