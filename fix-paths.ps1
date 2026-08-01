# ============================================================================
#  Repair the absolute paths after moving this app to another computer or drive.
#
#  WHY THIS EXISTS
#  Five files record where things live as full paths - "D:\AI PRO\tools\..." -
#  because MySQL and cloudflared both read plain config files that have no idea
#  where they were loaded from and cannot use a relative path. Move the folder to
#  a machine whose drive is C: or E: and every one of them points at nothing.
#
#  This script works out where everything actually is now, from its own location,
#  and rewrites those files. It is safe to run repeatedly: if a path is already
#  correct it says so and changes nothing.
#
#  RUN IT: double-click "Fix Paths After Moving.bat", or
#          powershell -ExecutionPolicy Bypass -File fix-paths.ps1
#
#  install-services.ps1 calls this automatically, so installing the services on a
#  new machine already does the right thing.
# ============================================================================

$ErrorActionPreference = "Stop"

$app   = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\finmate-react
$root  = Split-Path -Parent $app                           # the folder holding it
$tools = Join-Path $root "tools"

Write-Host ""
Write-Host "  Repairing paths" -ForegroundColor Magenta
Write-Host "  ===============" -ForegroundColor DarkGray
Write-Host "  App folder : $app" -ForegroundColor DarkGray
Write-Host "  Looking for tools in: $tools" -ForegroundColor DarkGray
Write-Host ""

$changed = 0
$problems = @()

function Say($ok, $text) {
    $mark = if ($ok) { "  [ok]   " } else { "  [FIX]  " }
    $col  = if ($ok) { "DarkGray" } else { "Green" }
    Write-Host "$mark$text" -ForegroundColor $col
}

# ---- find MySQL --------------------------------------------------------------
# The version is part of the folder name, so match on the pattern rather than a
# fixed version - an upgraded MySQL should still be found.
$mysqlDir = $null
if (Test-Path $tools) {
    $mysqlDir = Get-ChildItem $tools -Directory -Filter "mysql-*" -ErrorAction SilentlyContinue |
        Where-Object { Test-Path (Join-Path $_.FullName "bin\mysqld.exe") } |
        Select-Object -First 1 -ExpandProperty FullName
}
$dataDir = Join-Path $tools "mysql-data"
$myIni   = Join-Path $tools "my.ini"

if (-not $mysqlDir) {
    $problems += "No MySQL server found under $tools (expected a 'mysql-*' folder with bin\mysqld.exe)."
} else {
    Write-Host "  Found MySQL: $mysqlDir" -ForegroundColor DarkGray
}
if (-not (Test-Path $dataDir)) {
    $problems += "No database found at $dataDir - this is where all your records live. Did you copy 'tools\mysql-data'?"
}

# ---- 1. tools\my.ini ---------------------------------------------------------
if ((Test-Path $myIni) -and $mysqlDir) {
    $ini = Get-Content $myIni -Raw
    $want = @{
        'basedir'   = ($mysqlDir -replace '\\', '/')
        'datadir'   = ($dataDir  -replace '\\', '/')
        'log-error' = ((Join-Path $dataDir "error.log") -replace '\\', '/')
    }
    $before = $ini
    foreach ($k in $want.Keys) {
        # Quoted value on its own line, e.g.  datadir="D:/AI PRO/tools/mysql-data"
        $ini = [regex]::Replace($ini, "(?m)^\s*$k\s*=.*$", "$k=`"$($want[$k])`"")
    }
    if ($ini -ne $before) {
        Set-Content $myIni $ini -Encoding UTF8 -NoNewline
        Say $false "tools\my.ini  -> basedir / datadir / log-error"
        $changed++
    } else {
        Say $true "tools\my.ini already correct"
    }
} elseif (-not (Test-Path $myIni)) {
    $problems += "tools\my.ini is missing - MySQL will not know which port or datadir to use."
}

# ---- 2..4. the PowerShell helpers -------------------------------------------
# Each records the old root as a literal string. Swap it for wherever we are now.
$scripts = @(
    @{ file = "install-services.ps1"   },
    @{ file = "uninstall-services.ps1" },
    @{ file = "start-finmate-react.ps1"}
)
foreach ($s in $scripts) {
    $path = Join-Path $app $s.file
    if (-not (Test-Path $path)) { continue }
    $text = Get-Content $path -Raw
    $before = $text

    # Any "<drive>:\...\tools\mysql-*\bin\mysqld.exe" -> the one we found
    if ($mysqlDir) {
        $text = [regex]::Replace($text,
            '"[A-Za-z]:\\[^"]*?\\tools\\mysql-[^"\\]*\\bin\\mysqld\.exe"',
            '"' + (Join-Path $mysqlDir "bin\mysqld.exe") + '"')
    }
    $text = [regex]::Replace($text, '"[A-Za-z]:\\[^"]*?\\tools\\my\.ini"', '"' + $myIni + '"')
    # start-finmate-react.ps1 keeps a $root that is the PARENT of this folder.
    $text = [regex]::Replace($text, '(?m)^(\$root\s*=\s*)"[A-Za-z]:\\[^"]*"$', "`$1`"$root`"")

    if ($text -ne $before) {
        Set-Content $path $text -Encoding UTF8 -NoNewline
        Say $false "$($s.file)"
        $changed++
    } else {
        Say $true "$($s.file) already correct"
    }
}

# ---- 5. cloudflared credentials ---------------------------------------------
# This one moves with the WINDOWS USER, not the drive, so it needs fixing even
# when the folder lands in the same place under a different account.
$cfConfig = Join-Path $app "cloudflared\config.yml"
if (Test-Path $cfConfig) {
    $cf = Get-Content $cfConfig -Raw
    $m = [regex]::Match($cf, '(?m)^credentials-file:\s*(.+?)\s*$')
    if ($m.Success) {
        $old = $m.Groups[1].Value
        $leaf = Split-Path $old -Leaf
        $new = Join-Path $env:USERPROFILE ".cloudflared\$leaf"
        if ($old -ne $new) {
            $cf = [regex]::Replace($cf, '(?m)^credentials-file:\s*.+?\s*$', "credentials-file: $new")
            Set-Content $cfConfig $cf -Encoding UTF8 -NoNewline
            Say $false "cloudflared\config.yml -> $new"
            $changed++
        } else {
            Say $true "cloudflared\config.yml already correct"
        }
        if (-not (Test-Path $new)) {
            $problems += "The tunnel credentials file is not there: $new`n" +
                         "         Copy it from the old machine's .cloudflared folder, or the public web address will not come up. Everything else still works."
        }
    }
}

# ---- report ------------------------------------------------------------------
Write-Host ""
if ($changed -eq 0) {
    Write-Host "  Nothing needed changing." -ForegroundColor Green
} else {
    Write-Host "  Repaired $changed file(s)." -ForegroundColor Green
}
if ($problems.Count) {
    Write-Host ""
    Write-Host "  Still to sort out:" -ForegroundColor Yellow
    foreach ($p in $problems) { Write-Host "    - $p" -ForegroundColor Yellow }
}
Write-Host ""
Write-Host "  Next: run 'Install FinMate Services.bat' as administrator," -ForegroundColor DarkGray
Write-Host "  or start things by hand in this order: MySQL, backend, tunnel." -ForegroundColor DarkGray
Write-Host ""
