# ============================================================
#  FinMate 2.0 - start the full local stack
#  MySQL (3307) + Face service (8090) + FastAPI (8001) + Vite (5173)
#  Usage:  right-click > Run with PowerShell   (or)   ./start-finmate-react.ps1
# ============================================================
$ErrorActionPreference = "Stop"
$root        = "D:\AI PRO"
$mysqld      = "$root\tools\mysql-8.4.6-winx64\bin\mysqld.exe"
$myini       = "$root\tools\my.ini"
$faceEnv     = "$root\tools\faceenv\Scripts\python.exe"
$faceDir     = "$root\tools\faceservice"
$backendPy   = "$root\finmate-react\backend\venv\Scripts\python.exe"
$backendDir  = "$root\finmate-react\backend"
$frontendDir = "$root\finmate-react\frontend"

function Test-Port($port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host ""
Write-Host "  FinMate 2.0  (React + FastAPI)" -ForegroundColor Magenta
Write-Host "  --------------------------------" -ForegroundColor DarkGray

# 1) MySQL
Write-Host "[1/4] MySQL (3307)... " -ForegroundColor Cyan -NoNewline
if (Test-Port 3307) {
    Write-Host "already running." -ForegroundColor Yellow
} else {
    Start-Process -FilePath $mysqld -ArgumentList "--defaults-file=`"$myini`"" -WindowStyle Hidden
    Start-Sleep -Seconds 4
    Write-Host "started." -ForegroundColor Green
}

# 2) Face service
Write-Host "[2/4] Face service (8090)... " -ForegroundColor Cyan -NoNewline
if (Test-Port 8090) {
    Write-Host "already running." -ForegroundColor Yellow
} elseif (Test-Path $faceEnv) {
    Start-Process -FilePath $faceEnv -ArgumentList @("-m","uvicorn","app:app","--host","127.0.0.1","--port","8090") -WorkingDirectory $faceDir -WindowStyle Hidden
    Write-Host "started (auto-groups faces into people)." -ForegroundColor Green
} else {
    Write-Host "not installed - gallery works without auto face grouping." -ForegroundColor Yellow
}

# 3) FastAPI backend (own window so you can watch API logs)
Write-Host "[3/4] FastAPI backend (8001)... " -ForegroundColor Cyan -NoNewline
if (Test-Port 8001) {
    Write-Host "already running." -ForegroundColor Yellow
} else {
    $apiCmd = "& '$backendPy' -m uvicorn app.main:app --host 127.0.0.1 --port 8001"
    Start-Process powershell -WorkingDirectory $backendDir -ArgumentList @("-NoExit", "-Command", $apiCmd)
    Write-Host "starting in its own window." -ForegroundColor Green
}

# 4) Open the browser once Vite is listening
Start-Job {
    for ($i = 0; $i -lt 40; $i++) {
        if (Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue) {
            Start-Process "http://localhost:5173"
            break
        }
        Start-Sleep -Seconds 1
    }
} | Out-Null

Write-Host ""
Write-Host "  Open:  http://localhost:5173" -ForegroundColor Green
Write-Host ""
Write-Host "  Vite dev server runs in THIS window. Press Ctrl+C to stop it." -ForegroundColor DarkGray
Write-Host "  (MySQL, face service and the API keep running in the background.)" -ForegroundColor DarkGray
Write-Host ""

# 5) Vite frontend (foreground - this window)
Set-Location $frontendDir
if (Test-Port 5173) {
    Write-Host "  Vite already running on 5173 - opening browser only." -ForegroundColor Yellow
    Start-Process "http://localhost:5173"
} else {
    npm run dev
}
