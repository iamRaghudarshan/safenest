# ============================================================================
#  FinMate — remove the auto-start services created by install-services.ps1.
#  RUN AS ADMINISTRATOR. Leaves the pre-existing "MySQL84" service untouched,
#  and never touches your data (backend\private, the database files, or .env).
# ============================================================================

$ErrorActionPreference = "Continue"

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "  Run this as Administrator." -ForegroundColor Red; pause; exit 1
}

$mysqld    = "D:\AI PRO\tools\mysql-8.4.6-winx64\bin\mysqld.exe"
$cfExe     = "C:\Program Files (x86)\cloudflared\cloudflared.exe"
$MYSQL_SVC = "FinMateMySQL"
$API_TASK  = "FinMateAPI"

Write-Host ""
Write-Host "  Removing FinMate auto-start entries..." -ForegroundColor Magenta

# cloudflared
if (Get-Service -Name "cloudflared" -ErrorAction SilentlyContinue) {
    Stop-Service cloudflared -Force -ErrorAction SilentlyContinue
    & $cfExe service uninstall | Out-Null
    Write-Host "    cloudflared service removed" -ForegroundColor Green
} else { Write-Host "    cloudflared service not present" -ForegroundColor DarkGray }

# API task
if (Get-ScheduledTask -TaskName $API_TASK -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $API_TASK -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $API_TASK -Confirm:$false
    Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Write-Host "    FinMateAPI task removed" -ForegroundColor Green
} else { Write-Host "    FinMateAPI task not present" -ForegroundColor DarkGray }

# MySQL 3307 (data on disk is left completely intact)
if (Get-Service -Name $MYSQL_SVC -ErrorAction SilentlyContinue) {
    Stop-Service $MYSQL_SVC -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 3
    & $mysqld --remove $MYSQL_SVC | Out-Null
    Write-Host "    $MYSQL_SVC service removed (database files untouched)" -ForegroundColor Green
} else { Write-Host "    $MYSQL_SVC not present" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  Done. Start FinMate manually again with 'Start FinMate.bat'." -ForegroundColor Green
Write-Host ""
pause
