# ============================================================================
#  SafeNest watchdog - keep the stack serving, and say so when it cannot.
#
#  WHY THIS EXISTS.
#
#  On 20 November 2026 SafeNest was down and had been for some time: nothing on
#  port 8080, all three public hostnames answering 502. The code was fine. What
#  had gone was the machine setup around it:
#
#    - the AppAPI scheduled task      DID NOT EXIST
#    - the AppTunnel scheduled task   DID NOT EXIST
#    - the AppMySQL service was STOPPED, and not set to start itself
#
#  install-services.log shows all three registered and verified OK on 16
#  September, so they were installed and later removed. By what is unknown and
#  now unknowable: Microsoft-Windows-TaskScheduler/Operational is DISABLED on
#  this machine, so the deletion left no record. (Restore Boot Tasks.bat turns
#  that log on, so a second occurrence will be traceable.)
#
#  Since the cause cannot be found, the answer is to stop the cause mattering.
#  This script is checked every few minutes and repairs what is down. It is
#  deliberately NOT the same mechanism as the boot tasks: those are what SHOULD
#  run the stack, and this is what notices when they are not there at all --
#  which is precisely the failure that happened and which a boot task, by
#  definition, cannot notice about itself.
#
#  THE RULES IT FOLLOWS, each one paid for:
#
#  1. HEALTH, NOT A PORT. A listening socket is not a working API. It asks
#     /api/health and reads the `ok` field. A uvicorn that bound the port and
#     then failed to reach MySQL is down as far as a phone is concerned.
#
#  2. NEVER KILL WHAT IS NOT OURS. It starts things; the only process it ever
#     stops is a connector it started itself and then proved did not help.
#     Restart App API.bat carries a long comment about an unfiltered
#     "kill whatever holds 8080" loop that took an unrelated PHP server down
#     with it, and the only symptom was that other project being off.
#
#  3. LOG ON CHANGE, NOT ON EVERY TICK. A line every two minutes forever is a
#     file nobody reads. It writes when it repairs something, when a repair
#     fails, and once when a sustained outage clears -- so the log is a history
#     of events, not a heartbeat.
#
#  4. THE TUNNEL IS THE ONE PIECE IT WILL NOT GUESS AT. See the comment above
#     Repair-Tunnel: config.yml names a different tunnel from the connector
#     that was actually serving, and starting the wrong one looks like success.
#
#  Run it by hand to see what it would do:
#     powershell -NoProfile -ExecutionPolicy Bypass -File safenest-watchdog.ps1
#  Nothing here needs administrator.
# ============================================================================

#  VERIFYING IT WITHOUT AN OUTAGE. The repair paths must not be taken on trust,
#  and the obvious test -- kill the API and watch it come back -- is a real
#  outage on a live server. So the decision logic can be exercised against a
#  port nothing is using:
#
#     .\safenest-watchdog.ps1 -ApiPort 8099 -DryRun
#
#  which makes it find no API, and report what it WOULD start instead of
#  starting it. -DryRun never starts or stops anything.
param(
    [switch]$DryRun,
    [int]$ApiPort = 8080
)

$ErrorActionPreference = "Continue"

$root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$backendDir  = Join-Path $root "backend"
$python      = Join-Path $backendDir "venv\Scripts\python.exe"
$mysqld      = "D:\AI PRO\tools\mysql-8.4.6-winx64\bin\mysqld.exe"
$myini       = "D:\AI PRO\tools\my.ini"
$faceEnv     = "D:\AI PRO\tools\faceenv\Scripts\python.exe"
$faceDir     = "D:\AI PRO\tools\faceservice"
$cfExe       = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$cfConfig    = Join-Path $root "cloudflared\config.yml"

$logFile     = Join-Path $root "watchdog.log"
$stateFile   = Join-Path $env:LOCALAPPDATA "safenest-watchdog-state.txt"

# The public health URL comes from .env so a domain change never leaves this
# checking the old one. PUBLIC_BASE_URL and not the tunnel config, because the
# config lists three hostnames and only the app one serves /api/health.
$publicUrl = ""
$envFile = Join-Path $backendDir ".env"
if (Test-Path $envFile) {
    $m = Select-String -Path $envFile -Pattern '^PUBLIC_BASE_URL=(\S+)' | Select-Object -First 1
    if ($m) { $publicUrl = $m.Matches[0].Groups[1].Value.TrimEnd('/') }
}
if (-not $publicUrl) { $publicUrl = "https://app.safenesthub.in" }

function Write-Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    try { Add-Content -Path $logFile -Value $line -Encoding utf8 } catch {}
    Write-Host $line
}

# Remember what was broken last tick, so a sustained outage is reported once
# rather than every two minutes, and its recovery is reported too.
function Get-LastState {
    try { if (Test-Path $stateFile) { return (Get-Content $stateFile -Raw).Trim() } } catch {}
    return ""
}
function Set-LastState($s) {
    try { Set-Content -Path $stateFile -Value $s -Encoding utf8 } catch {}
}

function Test-Listening($port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# A listening port is not a working service. Ask, and read the answer.
function Test-ApiHealthy($url, $timeout = 8) {
    try {
        $r = Invoke-RestMethod "$url/api/health" -TimeoutSec $timeout
        return ($null -ne $r -and $r.ok -eq $true)
    } catch { return $false }
}

# ---- MySQL (3307) ----------------------------------------------------------
#
# The SERVICE is the right way to run it and needs administrator to start, so
# try that first and fall back to a plain process. A plain mysqld serves exactly
# the same database from exactly the same datadir; it just does not survive a
# logout. Better up than correct.
function Repair-MySQL {
    if (Test-Listening 3307) { return $true }

    $svc = Get-Service -Name "AppMySQL" -ErrorAction SilentlyContinue
    if ($svc -and $svc.Status -ne "Running") {
        try {
            Start-Service -Name "AppMySQL" -ErrorAction Stop
            Start-Sleep -Seconds 6
            if (Test-Listening 3307) { Write-Log "REPAIRED  MySQL - started the AppMySQL service."; return $true }
        } catch {
            # Access denied without elevation. Expected; fall through.
        }
    }

    if (-not (Test-Path $mysqld)) { Write-Log "FAILED    MySQL - mysqld.exe not found at $mysqld"; return $false }
    if ($DryRun) { Write-Log "DRYRUN    MySQL - would start: $mysqld --defaults-file=`"$myini`""; return $false }
    Start-Process -FilePath $mysqld -ArgumentList "--defaults-file=`"$myini`"" -WindowStyle Hidden
    Start-Sleep -Seconds 8
    if (Test-Listening 3307) {
        Write-Log "REPAIRED  MySQL - started mysqld as a plain process (service needs admin)."
        return $true
    }
    Write-Log "FAILED    MySQL - would not come up on 3307."
    return $false
}

# ---- The API (8080) --------------------------------------------------------
#
# The flags are not cosmetic and all three have cost something here:
#   --no-access-log     a per-request line written to a console nobody drains
#                       eventually fills its buffer and blocks every request,
#                       which looks exactly like the app hanging at 0% CPU.
#   --no-server-header  without it uvicorn adds its own Server header beside
#                       the app's, advertising the framework and its version.
#   --host 0.0.0.0      so a phone on the house wifi reaches the machine
#                       directly, photos never leaving the building. This was
#                       127.0.0.1 once, which meant the app worked from
#                       anywhere in the world and not from the sofa.
# SAFENEST_NO_TUNNEL stops the API starting a SECOND connector for a tunnel
# something else is already running; Cloudflare load-balances across every
# connector it can see, so half the requests would go through one nobody knows
# about.
function Repair-Api {
    if (Test-ApiHealthy "http://127.0.0.1:$ApiPort") { return $true }

    if (Test-Listening $ApiPort) {
        # Something holds the port but will not answer. Leave it alone -- rule 2 --
        # and say so, because this is the one case a restart would fix and the
        # watchdog deliberately will not do it blind.
        $owner = (Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue).OwningProcess |
                 Select-Object -First 1
        $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
        Write-Log ("FAILED    API - $ApiPort is held by {0} (pid {1}) but /api/health does not answer. Not killing it." -f $proc.ProcessName, $owner)
        return $false
    }

    if (-not (Test-Path $python)) { Write-Log "FAILED    API - venv python not found at $python"; return $false }

    $uvicornArgs = @("-m","uvicorn","app.main:app","--host","0.0.0.0","--port","$ApiPort",
                     "--no-access-log","--no-server-header")
    if ($DryRun) {
        Write-Log "DRYRUN    API - would start: $python $($uvicornArgs -join ' ')  (cwd $backendDir)"
        return $false
    }

    $env:SAFENEST_NO_TUNNEL = "1"
    Start-Process -FilePath $python -ArgumentList $uvicornArgs `
        -WorkingDirectory $backendDir -WindowStyle Hidden

    # Poll rather than sleep a fixed amount: a cold start loads the AI models.
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 3
        if (Test-ApiHealthy "http://127.0.0.1:$ApiPort" 5) {
            Write-Log "REPAIRED  API - started uvicorn on 0.0.0.0:8080."
            return $true
        }
    }
    Write-Log "FAILED    API - started uvicorn but /api/health never answered."
    return $false
}

# ---- The face service (8090) -----------------------------------------------
#
# Optional by design: the gallery works without it, only the automatic grouping
# of faces into people stops. So a missing faceenv is a note, not a failure.
function Repair-FaceService {
    if (Test-Listening 8090) { return $true }
    if (-not (Test-Path $faceEnv)) { return $true }
    if ($DryRun) { Write-Log "DRYRUN    face service - would start uvicorn app:app on 127.0.0.1:8090"; return $false }
    Start-Process -FilePath $faceEnv `
        -ArgumentList @("-m","uvicorn","app:app","--host","127.0.0.1","--port","8090") `
        -WorkingDirectory $faceDir -WindowStyle Hidden
    Start-Sleep -Seconds 5
    if (Test-Listening 8090) { Write-Log "REPAIRED  face service - started on 8090."; return $true }
    Write-Log "NOTE      face service would not start; gallery works, face grouping does not."
    return $false
}

# ---- The tunnel ------------------------------------------------------------
#
# THIS IS THE PIECE THAT WILL NOT BE GUESSED AT, and the reason is specific.
#
# cloudflared\config.yml names tunnel 1922fb4d-5f3c-4421-8187-9fd12a68514b. The
# connector that was actually carrying safenesthub.in, www.safenesthub.in and
# app.safenesthub.in on 20 November was a DIFFERENT tunnel, started from the
# logged-in session as `cloudflared tunnel run --token ...`. Hostname routes
# belong to one tunnel, so starting the one in config.yml can produce a
# connector with no routes on it: the task reports Running, Get-Process finds
# cloudflared, and the site stays down. "A service is running" and "a tunnel is
# connected" are not the same fact, and this is the second form of that trap on
# this machine.
#
# There is no cert.pem here either, so `cloudflared tunnel list` cannot be asked
# which tunnel owns the DNS.
#
# So: attempt the config.yml connector, VERIFY against the public hostname, and
# if it did not help, stop the connector this script started and leave a loud
# line. Trying is safe because a routeless connector carries no traffic; what
# would not be safe is leaving one running and calling it fixed.
function Repair-Tunnel {
    if (Test-ApiHealthy $publicUrl 15) { return $true }

    # The API is healthy locally (checked by the caller) and the public address
    # is not, so the tunnel is the gap.
    $existing = Get-Process cloudflared -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Log ("FAILED    tunnel - {0} connector(s) running but {1} does not answer. Not touching them; see Repair-Tunnel in this file." -f $existing.Count, $publicUrl)
        return $false
    }

    if (-not (Test-Path $cfExe) -or -not (Test-Path $cfConfig)) {
        Write-Log "FAILED    tunnel - cloudflared or its config is missing; the public address cannot come back."
        return $false
    }

    $tunnelId = ""
    $m = Select-String -Path $cfConfig -Pattern '^tunnel:\s*(\S+)' | Select-Object -First 1
    if ($m) { $tunnelId = $m.Matches[0].Groups[1].Value }
    if (-not $tunnelId) { Write-Log "FAILED    tunnel - no tunnel id in $cfConfig"; return $false }

    if ($DryRun) { Write-Log "DRYRUN    tunnel - would try config.yml's tunnel $tunnelId and verify against $publicUrl"; return $false }

    Write-Log "ATTEMPT   tunnel - no connector running; trying the one in config.yml ($tunnelId)."
    $p = Start-Process -FilePath $cfExe `
        -ArgumentList @("tunnel","--config","`"$cfConfig`"","run",$tunnelId) `
        -WindowStyle Hidden -PassThru

    for ($i = 0; $i -lt 10; $i++) {
        Start-Sleep -Seconds 4
        if (Test-ApiHealthy $publicUrl 10) {
            Write-Log "REPAIRED  tunnel - config.yml's tunnel carries the hostnames after all."
            return $true
        }
    }

    # It did not help, so it is the wrong tunnel. Stop the one thing this script
    # started rather than leaving a routeless connector behind to confuse the
    # next person into thinking the tunnel is fine.
    try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
    Write-Log "FAILED    tunnel - config.yml's tunnel ($tunnelId) does not carry the hostnames; stopped it again."
    Write-Log "ACTION    tunnel needs a person: either re-point config.yml at the tunnel that owns the DNS, or save the connector token in the app (Profile -> Web address) so the API runs it itself."
    return $false
}

# ---- one pass --------------------------------------------------------------
$broken = @()

if (-not (Repair-MySQL))       { $broken += "mysql" }
if (-not (Repair-Api))         { $broken += "api" }
Repair-FaceService | Out-Null

# Only worth asking about the public address once the API answers locally;
# otherwise a local outage would be reported twice, once as itself and once as
# a tunnel fault it did not cause.
if (Test-ApiHealthy "http://127.0.0.1:$ApiPort") {
    if (-not (Repair-Tunnel)) { $broken += "tunnel" }
}

$now  = ($broken -join ",")
$last = Get-LastState

# A dry run must not rewrite the real state file, or the next genuine pass would
# report a recovery from an outage that never happened.
if ($DryRun) {
    Write-Log "DRYRUN    pass complete; would report broken: $(if ($now) { $now } else { 'nothing' })"
    return
}

if ($now -eq "" -and $last -ne "") {
    Write-Log "RECOVERED everything is serving again (was: $last)."
} elseif ($now -ne "" -and $now -ne $last) {
    Write-Log "DOWN      still broken: $now"
}
Set-LastState $now
