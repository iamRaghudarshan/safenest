# start-finmate-tunnel.ps1
# Runs the permanent "finmate" Cloudflare tunnel, pointing the public hostname at
# the local app on port 8080. No config file or tunnel ID needed — cloudflared
# finds the tunnel by name and the credentials it saved during `tunnel create`.
#
# Prereqs (one-time): cloudflared installed + `cloudflared tunnel login` done +
# `cloudflared tunnel create finmate` + `cloudflared tunnel route dns finmate finmate.raghudarshan.online`.
# Also run start-finmate-internet.ps1 first (serves the app on 127.0.0.1:8080) and have MySQL up.

$ErrorActionPreference = "Stop"

$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cf)) { $cf = "cloudflared" }  # fall back to PATH

Write-Host "Starting the FinMate tunnel -> https://finmate.raghudarshan.online" -ForegroundColor Green
& $cf tunnel --url http://127.0.0.1:8080 run finmate
