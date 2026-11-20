# ============================================================================
#  Restore the pieces that make SafeNest come back after a reboot.
#
#  WHY THIS EXISTS, AND WHY IT IS NOT "Install App Services.bat".
#
#  Found on 20 November 2026: the site was down with nothing on port 8080 and
#  both public hostnames answering 502. The cause was not the code. Three
#  separate things had gone:
#
#    1. The AppAPI scheduled task  DID NOT EXIST     -> nothing started uvicorn
#    2. The AppTunnel scheduled task DID NOT EXIST   -> nothing started a connector
#    3. The AppMySQL service was STOPPED and not set to start itself
#
#  So nothing was serving, and nothing would have come back on its own.
#
#  install-services.ps1 is the right script for 1 and 3 and the WRONG script
#  for 2 on this machine, which is the whole reason for a separate file.
#  cloudflared\config.yml names tunnel 1922fb4d-5f3c-4421-8187-9fd12a68514b,
#  but the connector that was actually carrying safenesthub.in,
#  www.safenesthub.in and app.safenesthub.in was a DIFFERENT tunnel, started
#  from the logged-in session with `tunnel run --token ...`. Registering
#  AppTunnel from that config would start a connector with no routes on it:
#  the task would report Running, Get-Process would find cloudflared, and the
#  site would be down. That is the same "a service is running is not the same
#  fact as a tunnel is connected" trap install-services.ps1 warns about, in a
#  second form it does not cover.
#
#  THIS SCRIPT THEREFORE DOES NOT TOUCH THE TUNNEL AT ALL. Decide that
#  deliberately: either re-point config.yml at the tunnel that owns the DNS
#  records, or register the token connector as its own boot task. Until then
#  the tunnel is hand-started and dies with the session.
#
#  HOW TO RUN: double-click "Restore Boot Tasks.bat" next to this file and
#  accept the UAC prompt. Registering a SYSTEM task and changing a service's
#  start type both need administrator; nothing here can be done without it.
#
#  Safe to run twice.
# ============================================================================

$ErrorActionPreference = "Stop"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host ""
    Write-Host "  This must run as Administrator." -ForegroundColor Red
    Write-Host "  Close this and double-click 'Restore Boot Tasks.bat' instead." -ForegroundColor Yellow
    Write-Host ""
    pause
    exit 1
}

$root       = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir = Join-Path $root "backend"
$python     = Join-Path $backendDir "venv\Scripts\python.exe"
$watchdog   = Join-Path $root "safenest-watchdog.ps1"
$API_TASK   = "AppAPI"
$WATCH_TASK = "SafeNestWatchdog"
$MYSQL_SVC  = "AppMySQL"

foreach ($p in @($python, (Join-Path $root "frontend\dist\index.html"))) {
    if (-not (Test-Path $p)) { Write-Host "  MISSING: $p" -ForegroundColor Red; pause; exit 1 }
}

Write-Host ""
Write-Host "  SafeNest - restore boot tasks" -ForegroundColor Magenta
Write-Host "  =============================" -ForegroundColor DarkGray

# ---- 0) Turn on the log that would have answered "what deleted them?" ------
#
# Microsoft-Windows-TaskScheduler/Operational is DISABLED by default, and it was
# disabled here -- so when AppAPI and AppTunnel vanished there was no record of
# it, and the cause is now permanently unknowable. Event 141 is a task being
# deregistered. Turning this on costs nothing and means a second occurrence
# names its culprit instead of starting another investigation from nothing.
Write-Host ""
Write-Host "[0/3] Task Scheduler operational log... " -ForegroundColor Cyan -NoNewline
try {
    $log = Get-WinEvent -ListLog "Microsoft-Windows-TaskScheduler/Operational"
    if ($log.IsEnabled) {
        Write-Host "already on." -ForegroundColor Yellow
    } else {
        $log.IsEnabled = $true
        $log.SaveChanges()
        Write-Host "enabled - a future task deletion will be recorded (event 141)." -ForegroundColor Green
    }
} catch {
    Write-Host "could not enable it: $_" -ForegroundColor Yellow
}

# ---- 1) MySQL on 3307 ------------------------------------------------------
#
# A hand-started mysqld.exe may already hold 3307 (that is how the database was
# brought back up without administrator). The SERVICE cannot bind a port another
# process owns, so free it first -- and only a mysqld, by name, never "whatever
# holds the port". This machine has had an unrelated project on 8080 killed by
# exactly that kind of unfiltered loop.
Write-Host ""
Write-Host "[1/3] MySQL service ($MYSQL_SVC, port 3307)... " -ForegroundColor Cyan -NoNewline
if (-not (Get-Service -Name $MYSQL_SVC -ErrorAction SilentlyContinue)) {
    Write-Host "NOT INSTALLED." -ForegroundColor Red
    Write-Host "      Run 'Install App Services.bat' for this one - it registers mysqld." -ForegroundColor Yellow
} else {
    $owner = (Get-NetTCPConnection -LocalPort 3307 -State Listen -ErrorAction SilentlyContinue).OwningProcess |
             Select-Object -First 1
    if ($owner) {
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        if ($proc -and $proc.ProcessName -eq "mysqld") {
            Stop-Process -Id $owner -Force -ErrorAction SilentlyContinue
            Write-Host "" ; Write-Host "      freed 3307 from a hand-started mysqld." -ForegroundColor DarkGray
            Start-Sleep -Seconds 3
        } else {
            Write-Host "" ; Write-Host "      3307 is held by $($proc.ProcessName) - left alone." -ForegroundColor Yellow
        }
    }
    Set-Service -Name $MYSQL_SVC -StartupType Automatic
    Start-Service -Name $MYSQL_SVC -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 5
    Write-Host "      set to Automatic and started." -ForegroundColor Green
}

# ---- 2) The API boot task --------------------------------------------------
#
# Identical to what install-services.ps1 registers, and the flags are not
# cosmetic: --no-access-log stops a per-request line filling a console buffer
# nobody drains (which looks exactly like the app hanging at 0% CPU),
# --no-server-header stops uvicorn advertising itself and its version beside
# the app's own header, and --host 0.0.0.0 is what lets a phone on the house
# wifi reach the machine directly instead of going out through Cloudflare and
# back. 127.0.0.1 here once meant the app worked from anywhere in the world
# and not from the sofa.
Write-Host "[2/3] API task ($API_TASK, port 8080)... " -ForegroundColor Cyan -NoNewline

$owner8080 = (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue).OwningProcess |
             Select-Object -First 1
if ($owner8080) {
    $proc = Get-Process -Id $owner8080 -ErrorAction SilentlyContinue
    if ($proc -and $proc.ProcessName -like "python*") {
        Stop-Process -Id $owner8080 -Force -ErrorAction SilentlyContinue
        Write-Host "" ; Write-Host "      freed 8080 from a hand-started uvicorn." -ForegroundColor DarkGray
        Start-Sleep -Seconds 2
    } else {
        Write-Host "" ; Write-Host "      8080 is held by $($proc.ProcessName) - left alone." -ForegroundColor Yellow
    }
}

if (Get-ScheduledTask -TaskName $API_TASK -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $API_TASK -Confirm:$false
}

$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m uvicorn app.main:app --host 0.0.0.0 --port 8080 --no-access-log --no-server-header" `
    -WorkingDirectory $backendDir
$trigger = New-ScheduledTaskTrigger -AtStartup
$trigger.Delay = "PT30S"          # let MySQL get there first
$sysPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$settings.DisallowStartOnRemoteAppSession = $false

# The API runs its own tunnel supervisor at startup, and with no token in the
# database it falls back to cloudflared\config.yml -- a SECOND connector for a
# tunnel something else is already running. Cloudflare load-balances across
# every connector it can see, so half the requests would go through one nobody
# knows about. A scheduled task has no environment block of its own, hence
# machine scope.
[Environment]::SetEnvironmentVariable("SAFENEST_NO_TUNNEL", "1", "Machine")
$env:SAFENEST_NO_TUNNEL = "1"

Register-ScheduledTask -TaskName $API_TASK -Action $action -Trigger $trigger `
    -Principal $sysPrincipal -Settings $settings `
    -Description "SafeNest FastAPI backend + built SPA on 0.0.0.0:8080" | Out-Null
Start-ScheduledTask -TaskName $API_TASK
Write-Host "      registered and started." -ForegroundColor Green

# ---- 3) The watchdog, as SYSTEM --------------------------------------------
#
# THIS IS THE PART THAT MAKES THE OUTAGE NOT HAPPEN AGAIN, and it is worth
# being clear about why the boot task above is not enough on its own. AppAPI
# was registered and verified working on 16 September and was simply GONE by
# 20 November. A boot task cannot notice that it does not exist. Something has
# to stand outside it and check that the site actually answers.
#
# SafeNestWatchdog may already exist as a USER task -- it can be registered
# without administrator, which is how it was first put in place, but a user
# task runs only while that user is logged on. Re-registering it as SYSTEM is
# the upgrade: it then runs from boot with nobody logged in, which is the whole
# point of the stack being a service rather than something somebody starts.
Write-Host "[3/3] Watchdog task ($WATCH_TASK, every 3 min)... " -ForegroundColor Cyan -NoNewline
if (-not (Test-Path $watchdog)) {
    Write-Host "MISSING safenest-watchdog.ps1 - skipped." -ForegroundColor Red
} else {
    if (Get-ScheduledTask -TaskName $WATCH_TASK -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $WATCH_TASK -Confirm:$false
    }
    $wdAction = New-ScheduledTaskAction -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$watchdog`"" `
        -WorkingDirectory $root
    # At boot AND every three minutes thereafter, forever. The boot trigger
    # matters on its own: if AppAPI has been deleted again, the watchdog is then
    # the only thing that brings the site up at all.
    $wdBoot = New-ScheduledTaskTrigger -AtStartup
    $wdBoot.Delay = "PT90S"      # after MySQL (30s) and the API, so a healthy
                                 # boot is silent instead of racing them
    $wdEvery = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(3) `
        -RepetitionInterval (New-TimeSpan -Minutes 3) `
        -RepetitionDuration ([TimeSpan]::MaxValue)
    $wdSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -StartWhenAvailable `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10) `
        -MultipleInstances IgnoreNew
    $wdSettings.DisallowStartOnRemoteAppSession = $false
    Register-ScheduledTask -TaskName $WATCH_TASK -Action $wdAction `
        -Trigger @($wdBoot, $wdEvery) -Principal $sysPrincipal -Settings $wdSettings `
        -Description "Checks SafeNest actually answers and repairs what is down; logs to watchdog.log" | Out-Null
    Write-Host "registered as SYSTEM." -ForegroundColor Green
}

# Binding 0.0.0.0 only decides what the app will answer; the firewall decides
# who may knock. LocalSubnet keeps this to the devices on this wifi.
$fwName = "App API (home network)"
Get-NetFirewallRule -DisplayName $fwName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $fwName -Direction Inbound -Action Allow `
    -Protocol TCP -LocalPort 8080 -RemoteAddress LocalSubnet `
    -Profile Private -ErrorAction SilentlyContinue | Out-Null

# ---- verify ----------------------------------------------------------------
#
# Poll rather than sleep a fixed amount: a cold start loads the AI models.
Write-Host ""
Write-Host "  Waiting for the API to answer..." -ForegroundColor Cyan
$health = $null
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 3
    try { $health = Invoke-RestMethod "http://127.0.0.1:8080/api/health" -TimeoutSec 5; break } catch {}
}

function Show($label, $ok) {
    $mark = if ($ok) { "OK  " } else { "FAIL" }
    $col  = if ($ok) { "Green" } else { "Red" }
    Write-Host ("    [{0}] {1}" -f $mark, $label) -ForegroundColor $col
}

Write-Host ""
Show "MySQL  (3307)     " ([bool](Get-NetTCPConnection -LocalPort 3307 -State Listen -ErrorAction SilentlyContinue))
Show "API    (8080)     " ([bool](Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue))
Show "API health        " ($null -ne $health -and $health.ok)
Show "AppAPI boot task  " ($null -ne (Get-ScheduledTask -TaskName $API_TASK -ErrorAction SilentlyContinue))
Show "Watchdog task     " ($null -ne (Get-ScheduledTask -TaskName $WATCH_TASK -ErrorAction SilentlyContinue))
$wdPrincipal = (Get-ScheduledTask -TaskName $WATCH_TASK -ErrorAction SilentlyContinue).Principal.UserId
Show "Watchdog as SYSTEM" ("$wdPrincipal" -match "SYSTEM")

# A route that exists ONLY in the newest code, so "it answered" cannot be
# mistaken for "it is running the current build". 401 means the route is there
# and wants a sign-in; 404 means an older build is serving.
$code = ""
try {
    $code = (Invoke-WebRequest "http://127.0.0.1:8080/api/gallery/summary" -TimeoutSec 10 `
             -SkipHttpErrorCheck).StatusCode
} catch { $code = $_.Exception.Response.StatusCode.value__ }
Show "current build     " ("$code" -eq "401")
if ("$code" -eq "404") {
    Write-Host "      404 from /api/gallery/summary - an OLD build is serving." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  THE TUNNEL IS NOT MANAGED BY THIS SCRIPT." -ForegroundColor Yellow
$tun = Get-Process cloudflared -ErrorAction SilentlyContinue
if ($tun) {
    Write-Host "  A connector is running now, but it was started by hand and will not" -ForegroundColor DarkGray
    Write-Host "  come back after a reboot. See the comment at the top of this file." -ForegroundColor DarkGray
} else {
    Write-Host "  No connector is running - the public hostnames will answer 502." -ForegroundColor Red
}
Write-Host ""
pause
