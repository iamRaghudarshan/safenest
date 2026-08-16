# ============================================================================
#  App — install as Windows services so the app survives reboots.
#
#  HOW TO RUN — this needs administrator rights.
#    Easiest:  double-click "Install App Services.bat" (next to this file).
#              It re-launches itself elevated; just accept the UAC prompt.
#
#    Manually: Start -> type "powershell" -> right-click "Windows PowerShell"
#              -> "Run as administrator", then paste:
#       powershell -NoProfile -ExecutionPolicy Bypass -File "D:\AI PRO\finmate-react\install-services.ps1"
#
#  NOTE: right-clicking a .ps1 in Explorer offers "Run with PowerShell", which is
#  NOT elevated — this script will stop and tell you so.
#
#  Installs three pieces:
#    1. AppMySQL  (service)        - the portable MySQL on port 3307 that holds
#                                        App's data. NOTE: the pre-existing
#                                        "MySQL84" service is a DIFFERENT instance
#                                        on 3306 and is left completely alone.
#    2. AppAPI    (scheduled task) - the FastAPI app + built SPA on port 8080.
#                                        A boot-triggered task rather than a service
#                                        because a plain Python process can't answer
#                                        the Windows service control protocol without
#                                        a third-party wrapper such as NSSM.
#    3. cloudflared   (service)        - publishes 8080 at the public hostname.
#
#  Reverse it all with uninstall-services.ps1.
# ============================================================================

$ErrorActionPreference = "Stop"

# Everything printed here is also written to install-services.log next to this
# script, so a window that closes too fast still leaves evidence behind.
$logPath = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "install-services.log"
try { Start-Transcript -Path $logPath -Force | Out-Null } catch {}
trap {
    Write-Host ""
    Write-Host "  ERROR: $_" -ForegroundColor Red
    Write-Host "  Full log: $logPath" -ForegroundColor Yellow
    try { Stop-Transcript | Out-Null } catch {}
    pause
    exit 1
}

# ---- must be elevated ------------------------------------------------------
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "  This script must run as Administrator." -ForegroundColor Red
    Write-Host "  Close this and double-click 'Install App Services.bat' instead -" -ForegroundColor Yellow
    Write-Host "  it elevates automatically." -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path

# If this copy was moved to another computer or drive, the full paths below (and
# in my.ini and the tunnel config) point at the old machine. Repair them first —
# it is a no-op when they are already right, and installing services that point
# at a non-existent mysqld would otherwise fail in a confusing way.
$fixer = Join-Path $root "fix-paths.ps1"
if (Test-Path $fixer) { & $fixer }

$backendDir = Join-Path $root "backend"
$python     = Join-Path $backendDir "venv\Scripts\python.exe"
$mysqld     = "D:\AI PRO\tools\mysql-8.4.6-winx64\bin\mysqld.exe"
$myini      = "D:\AI PRO\tools\my.ini"
$cfExe      = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cfHome     = Join-Path $env:USERPROFILE ".cloudflared"

$MYSQL_SVC  = "AppMySQL"
$API_TASK   = "AppAPI"
$TUNNEL_TASK = "AppTunnel"

function Test-Port($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "  App - install services" -ForegroundColor Magenta
Write-Host "  ==========================" -ForegroundColor DarkGray

# ---- sanity checks ---------------------------------------------------------
foreach ($p in @($python, $mysqld, $myini, $cfExe)) {
    if (-not (Test-Path $p)) { Write-Host "  MISSING: $p" -ForegroundColor Red; pause; exit 1 }
}
if (-not (Test-Path (Join-Path $root "frontend\dist\index.html"))) {
    Write-Host "  frontend\dist is missing - build it first:" -ForegroundColor Red
    Write-Host "     cd `"$root\frontend`"; npm run build" -ForegroundColor Yellow
    pause; exit 1
}

# ---- stop anything already holding the ports -------------------------------
Write-Host ""
Write-Host "[0/3] Stopping any manually-started instances..." -ForegroundColor Cyan
foreach ($port in @(3307, 8080)) {
    $owner = (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).OwningProcess |
             Select-Object -First 1
    if ($owner) {
        try { Stop-Process -Id $owner -Force -ErrorAction Stop; Write-Host "      freed port $port" -ForegroundColor DarkGray }
        catch { Write-Host "      could not free port $port - close it manually" -ForegroundColor Yellow }
    }
}
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 2

# ---- 1) MySQL (port 3307) --------------------------------------------------
Write-Host ""
Write-Host "[1/3] MySQL service ($MYSQL_SVC, port 3307)... " -ForegroundColor Cyan -NoNewline
if (Get-Service -Name $MYSQL_SVC -ErrorAction SilentlyContinue) {
    Write-Host "already installed." -ForegroundColor Yellow
} else {
    # mysqld registers itself; the service name comes last on the command line.
    & $mysqld --install $MYSQL_SVC --defaults-file="$myini" | Out-Null
    Write-Host "installed." -ForegroundColor Green
}
Set-Service -Name $MYSQL_SVC -StartupType Automatic
Start-Service -Name $MYSQL_SVC -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

# ---- 2) App API (port 8080) -------------------------------------------
Write-Host "[2/3] App API task ($API_TASK, port 8080)... " -ForegroundColor Cyan -NoNewline
if (Get-ScheduledTask -TaskName $API_TASK -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $API_TASK -Confirm:$false
}
# --no-server-header matters too, and was missing here while every hand-started
# copy had it: without it uvicorn adds its OWN `Server: uvicorn` beside the
# app's, so a service-installed machine quietly advertises the framework — and
# on a version bump, the version — to anyone who asks. It is the one difference
# between the documented start command and what this registered, and it was
# found by an audit noticing two Server headers rather than one.
#
# --no-access-log matters: a per-request log line written to a console nobody
# drains will eventually fill its buffer and block every request, which looks
# exactly like the app hanging while using no CPU.
# --host 0.0.0.0, so the app answers on the HOME NETWORK as well as through the
# tunnel.
#
# It was 127.0.0.1, which meant only this machine could reach it — the tunnel
# runs here, so the public address worked and the phone could reach the app from
# anywhere in the world and NOT from the sofa. Typing the LAN address into the
# app gave a connection refused, which reads as the app being broken rather than
# the server declining to listen.
#
# That matters beyond convenience: at home the phone talks to the computer
# directly over the wifi, so photos and documents never leave the house at all —
# no Cloudflare, no internet. It is the more private of the two routes and it
# was the one that did not work.
#
# The firewall rule below scopes this to the local subnet, so opening it up does
# not mean opening it to anything that can route here. Everything in front of it
# is unchanged: bcrypt, the per-account lockout, the per-IP throttle and the
# signed media URLs were always the real defence, since the tunnel already
# exposed this app to the whole internet.
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log --no-server-header" `
    -WorkingDirectory $backendDir
$trigger = New-ScheduledTaskTrigger -AtStartup
# SYSTEM avoids storing a password and starts without anyone logging in.
$sysPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
# Give MySQL a head start, run forever, and come back if it ever exits.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.DisallowStartOnRemoteAppSession = $false
$trigger.Delay = "PT30S"

Register-ScheduledTask -TaskName $API_TASK -Action $action -Trigger $trigger `
    -Principal $sysPrincipal -Settings $settings `
    -Description "App FastAPI backend + built SPA on 0.0.0.0:8080" | Out-Null
Start-ScheduledTask -TaskName $API_TASK
Write-Host "installed." -ForegroundColor Green
Start-Sleep -Seconds 6

# Let the home network in, and nothing else.
#
# Binding 0.0.0.0 only decides what the app is willing to answer; Windows
# Firewall decides who may knock. RemoteAddress=LocalSubnet keeps this to the
# devices on the same wifi — a phone in the house, not a hotel network the
# laptop later joins. Without the rule, Windows silently drops the connection
# and the LAN address looks broken in exactly the same way an unbound port did.
$fwName = "App API (home network)"
Get-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $fwName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 8080 -RemoteAddress LocalSubnet `
    -Profile Private -ErrorAction SilentlyContinue | Out-Null
Write-Host "      home-network access allowed on port 8080 (this subnet only)." -ForegroundColor DarkGray

# ---- 3) cloudflared tunnel -------------------------------------------------
Write-Host "[3/3] cloudflared service... " -ForegroundColor Cyan -NoNewline
# The service reads %USERPROFILE%\.cloudflared\config.yml, so put the project's
# config (tunnel id + ingress -> 127.0.0.1:8080) where it will be found.
if (-not (Test-Path $cfHome)) { New-Item -ItemType Directory -Path $cfHome | Out-Null }
Copy-Item (Join-Path $root "cloudflared\config.yml") (Join-Path $cfHome "config.yml") -Force

# AND into the SYSTEM profile, which is the one that actually matters.
#
# `cloudflared service install` registers the service as LocalSystem. LocalSystem's
# %USERPROFILE% is C:\Windows\System32\config\systemprofile — NOT the person's
# folder — so a config written only to $cfHome above is somewhere the running
# service never looks. It starts, finds no ingress, and the public address
# answers Cloudflare error 1033 while everything local is perfectly healthy.
#
# That is exactly what happened on the first real install here: MySQL and the
# API came up as services, the tunnel service showed Running, and the site was
# down. "Service is running" and "tunnel is connected" are not the same fact.
#
# The credentials JSON is referenced by ABSOLUTE path from inside config.yml so
# it does not need moving, but cert.pem is looked up beside the config, so it is
# copied too.
$sysHome = Join-Path $env:SystemRoot "System32\config\systemprofile\.cloudflared"
if (-not (Test-Path $sysHome)) { New-Item -ItemType Directory -Path $sysHome -Force | Out-Null }
Copy-Item (Join-Path $root "cloudflared\config.yml") (Join-Path $sysHome "config.yml") -Force
foreach ($extra in @("cert.pem")) {
    $src = Join-Path $cfHome $extra
    if (Test-Path $src) { Copy-Item $src (Join-Path $sysHome $extra) -Force }
}

# A TASK, NOT `cloudflared service install`.
#
# That command registers the service to run the bare executable with NO
# arguments — no `tunnel run`, no `--config` — so it depends on discovering a
# config relative to the service account's profile. It does not find one, and
# the Windows event log fills with "terminated unexpectedly", 31 times in the
# first eleven minutes after a reboot, SCM restarting it every 20 seconds.
#
# The tunnel LOOKS fine while that happens: each brief run does connect, so the
# public address answers most requests. It is flapping, not working, and
# `Get-Service` reports Stopped while the site is up — which is what makes it
# so easy to miss.
#
# A SYSTEM task naming the config path and the tunnel id outright has none of
# that ambiguity, and it is the same shape as the API task above, which comes
# back from a reboot cleanly.
if (Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue) {
    & $cfExe service uninstall | Out-Null
    Start-Sleep -Seconds 2
}
Get-Process cloudflared -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue

if (Get-ScheduledTask -TaskName $TUNNEL_TASK -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TUNNEL_TASK -Confirm:$false
}
$tunnelId = (Select-String -Path (Join-Path $root "cloudflared\config.yml") -Pattern '^tunnel:\s*(\S+)').Matches.Groups[1].Value
$cfAction = New-ScheduledTaskAction -Execute $cfExe `
    -Argument "tunnel --config `"$(Join-Path $root 'cloudflared\config.yml')`" run $tunnelId"
$cfTrigger = New-ScheduledTaskTrigger -AtStartup
# After the API. A connector that comes up first just retries, but in order
# means the first request through the tunnel finds something serving.
$cfTrigger.Delay = "PT45S"
Register-ScheduledTask -TaskName $TUNNEL_TASK -Action $cfAction -Trigger $cfTrigger `
    -Principal $sysPrincipal -Settings $settings `
    -Description "App Cloudflare tunnel -> 127.0.0.1:8080" | Out-Null
Start-ScheduledTask -TaskName $TUNNEL_TASK
Write-Host "installed." -ForegroundColor Green
Start-Sleep -Seconds 6

# ---- verify ----------------------------------------------------------------
Write-Host ""
Write-Host "  Verifying..." -ForegroundColor Cyan
$okMysql = Test-Port 3307
$okApi   = Test-Port 8080
$okTun   = (Get-Service cloudflared -ErrorAction SilentlyContinue).Status -eq "Running"

function Show($label, $ok) {
    $mark = if ($ok) { "OK  " } else { "FAIL" }
    $col  = if ($ok) { "Green" } else { "Red" }
    Write-Host ("    [{0}] {1}" -f $mark, $label) -ForegroundColor $col
}
Show "MySQL       (3307)" $okMysql
Show "App API (8080)" $okApi
Show "cloudflared tunnel" $okTun

$health = $null
try { $health = Invoke-RestMethod "http://127.0.0.1:8080/api/health" -TimeoutSec 10 } catch {}
Show "API health check  " ($null -ne $health -and $health.ok)

# Read the public hostname from the tunnel config rather than hard-coding it, so a
# domain change (edit cloudflared\config.yml) never leaves this verification pointing
# at the old address.
$pubHost = (Select-String -Path (Join-Path $root "cloudflared\config.yml") -Pattern '^\s*-?\s*hostname:\s*(\S+)').Matches.Groups[1].Value
$publicUrl = if ($pubHost) { "https://$pubHost" } else { "" }
$public = $null
if ($publicUrl) { try { $public = Invoke-RestMethod "$publicUrl/api/health" -TimeoutSec 20 } catch {} }
Show "Public URL        " ($null -ne $public -and $public.ok)

Write-Host ""
if ($okMysql -and $okApi -and $okTun) {
    Write-Host "  All three start automatically at boot now." -ForegroundColor Green
    if ($publicUrl) { Write-Host "  $publicUrl" -ForegroundColor Green }
} else {
    Write-Host "  Something did not come up - check the details above." -ForegroundColor Yellow
    Write-Host "  Logs:  Get-ScheduledTaskInfo $API_TASK   |   Get-Service cloudflared" -ForegroundColor DarkGray
}
Write-Host ""
Write-Host "  To undo:  double-click 'Uninstall App Services.bat'" -ForegroundColor DarkGray
Write-Host "  Log:      $logPath" -ForegroundColor DarkGray
Write-Host ""
try { Stop-Transcript | Out-Null } catch {}
pause
