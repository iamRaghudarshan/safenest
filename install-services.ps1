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
# --no-access-log matters: a per-request log line written to a console nobody
# drains will eventually fill its buffer and block every request, which looks
# exactly like the app hanging while using no CPU.
$action = New-ScheduledTaskAction -Execute $python `
    -Argument "-m uvicorn app.main:app --host 127.0.0.1 --port 8080 --no-access-log" `
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
    -Description "App FastAPI backend + built SPA on 127.0.0.1:8080" | Out-Null
Start-ScheduledTask -TaskName $API_TASK
Write-Host "installed." -ForegroundColor Green
Start-Sleep -Seconds 6

# ---- 3) cloudflared tunnel -------------------------------------------------
Write-Host "[3/3] cloudflared service... " -ForegroundColor Cyan -NoNewline
# The service reads %USERPROFILE%\.cloudflared\config.yml, so put the project's
# config (tunnel id + ingress -> 127.0.0.1:8080) where it will be found.
if (-not (Test-Path $cfHome)) { New-Item -ItemType Directory -Path $cfHome | Out-Null }
Copy-Item (Join-Path $root "cloudflared\config.yml") (Join-Path $cfHome "config.yml") -Force

if (Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue) {
    Write-Host "already installed - reinstalling to pick up the config." -ForegroundColor Yellow
    & $cfExe service uninstall | Out-Null
    Start-Sleep -Seconds 2
}
& $cfExe service install | Out-Null
Set-Service -Name "cloudflared" -StartupType Automatic -ErrorAction SilentlyContinue
Start-Service -Name "cloudflared" -ErrorAction SilentlyContinue
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

$public = $null
try { $public = Invoke-RestMethod "https://finmate.raghudarshan.online/api/health" -TimeoutSec 20 } catch {}
Show "Public URL        " ($null -ne $public -and $public.ok)

Write-Host ""
if ($okMysql -and $okApi -and $okTun) {
    Write-Host "  All three start automatically at boot now." -ForegroundColor Green
    Write-Host "  https://finmate.raghudarshan.online" -ForegroundColor Green
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
