# start-finmate-https.ps1
# Builds the frontend and serves App (app + API) over HTTPS on port 8443,
# for use from an iPhone / iPad / other device on the same Wi-Fi as this PC.
# Prerequisite: MySQL must be running (the normal "Start App" launcher starts it).

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Building the app (frontend/dist)..." -ForegroundColor Cyan
Push-Location "$root\frontend"
npm run build
Pop-Location

# Best-guess LAN IP (private ranges) + the mDNS hostname (IP-independent).
$ip = (Get-NetIPAddress -AddressFamily IPv4 |
       Where-Object { $_.IPAddress -match '^(192\.168|10\.|172\.(1[6-9]|2\d|3[01]))' } |
       Select-Object -First 1).IPAddress
$mdns = "$($env:COMPUTERNAME).local"

Write-Host ""
Write-Host "=======================================================" -ForegroundColor Green
Write-Host " App is serving over HTTPS on port 8443." -ForegroundColor Green
Write-Host ""
Write-Host " On your iPhone (same Wi-Fi), in Safari:" -ForegroundColor Green
Write-Host "   1) FIRST TIME ONLY - install the certificate:"
Write-Host "        https://$ip`:8443/finmate-ca.crt"
Write-Host "      (accept the one-time warning, then Settings > General >"
Write-Host "       VPN & Device Management > install; then Settings > General >"
Write-Host "       About > Certificate Trust Settings > turn ON 'App Local CA')"
Write-Host "   2) Open the app:"
Write-Host "        https://$ip`:8443"
Write-Host "        https://$mdns`:8443   (works even if the IP changes)"
Write-Host "   3) Share > Add to Home Screen."
Write-Host "=======================================================" -ForegroundColor Green
Write-Host ""

Set-Location "$root\backend"
& ".\venv\Scripts\python.exe" -m uvicorn app.main:app --host 0.0.0.0 --port 8443 `
    --ssl-keyfile "$root\certs\server.key" --ssl-certfile "$root\certs\server.crt"
