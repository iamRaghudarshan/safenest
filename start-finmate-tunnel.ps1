# start-finmate-tunnel.ps1
# Runs the permanent "finmate" Cloudflare tunnel, pointing the public hostname at
# the local app on port 8080. No config file or tunnel ID needed — cloudflared
# finds the tunnel by name and the credentials it saved during `tunnel create`.
#
# Prereqs (one-time): cloudflared installed + `cloudflared tunnel login` done +
# `cloudflared tunnel create finmate`, then a DNS route per hostname — they all
# reach the SAME tunnel and the same port, and only the Host header tells the
# website and the app apart (see main.py::_index):
#   cloudflared tunnel route dns finmate safenesthub.in
#   cloudflared tunnel route dns finmate www.safenesthub.in
#   cloudflared tunnel route dns finmate app.safenesthub.in
# ("finmate" here is the Cloudflare TUNNEL NAME, not the public address. It is
# the name the credentials were saved under and is nothing to do with branding.)
# Also run start-finmate-internet.ps1 first (serves the app on 127.0.0.1:8080) and have MySQL up.
# NOTE: the app normally runs the tunnel via the AppTunnel scheduled task; this is a manual fallback.

$ErrorActionPreference = "Stop"

$cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cf)) { $cf = "cloudflared" }  # fall back to PATH

Write-Host "Starting the App tunnel -> https://app.safenesthub.in" -ForegroundColor Green
& $cf tunnel --url http://127.0.0.1:8080 run finmate
