param(
    [switch]$NoBrowser,
    # By default start.bat does a fresh restart: any previous instance is stopped first.
    # Pass -NoRestart to keep an already-running healthy instance instead.
    [switch]$NoRestart,
    # By default start.bat stays open and Ctrl+C (or Q) stops everything. Pass -Detach to start the
    # servers in the background and return to the prompt right away (stop later with stop.bat).
    [switch]$Detach
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Logs = Join-Path $Root "logs"
$Pids = Join-Path $Logs "pids"
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
$BackendDir = Join-Path $Root "backend"
$FrontendDir = Join-Path $Root "frontend"
$ApiUrl = "http://127.0.0.1:8000"
$UiUrl = "http://127.0.0.1:5173"

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
New-Item -ItemType Directory -Force -Path $Pids | Out-Null

# The servers get an empty file as stdin. Otherwise Vite/node switches the shared console to raw
# input mode and swallows the keys typed in this terminal (Ctrl+C and typing then stop working).
$NullInput = Join-Path $Logs "empty.in"
Set-Content -Path $NullInput -Value "" -NoNewline -Encoding ASCII

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $pending = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) {
            return $false
        }
        $client.EndConnect($pending)
        return $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Close()
    }
}

function Wait-Http([string]$Url, [int]$TimeoutSeconds, [int]$WatchProcess) {
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if ($WatchProcess -gt 0) {
            $alive = Get-Process -Id $WatchProcess -ErrorAction SilentlyContinue
            if (-not $alive) {
                return $false
            }
        }
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return $true
            }
        }
        catch {
            # not ready yet
        }
        Start-Sleep -Milliseconds 750
    }
    return $false
}

function Show-Tail([string]$Path, [int]$LineCount = 25) {
    if (Test-Path $Path) {
        Write-Host "--- tail of $Path ---" -ForegroundColor DarkGray
        Get-Content -Path $Path -Tail $LineCount | ForEach-Object {
            Write-Host "    $_" -ForegroundColor DarkGray
        }
    }
}

function Save-Pid([string]$Name, [int]$ProcessId) {
    Set-Content -Path (Join-Path $Pids "$Name.pid") -Value $ProcessId -Encoding ASCII
}

function Stop-Tracked([int]$ProcessId) {
    if ($ProcessId -le 0) {
        return
    }
    $alive = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($alive) {
        & taskkill.exe /PID $ProcessId /T /F 2>$null | Out-Null
    }
}

function Remove-PidFile([string]$Name) {
    $path = Join-Path $Pids "$Name.pid"
    if (Test-Path $path) {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Write-Host "FINAL60 - Polymarket BTC 5m bot" -ForegroundColor Green
Write-Host "Starting backend and dashboard" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoRestart) {
    # Stop the old server first (also frees the venv files before setup/pip touch them).
    # stop.ps1 only kills processes it can prove belong to this project folder.
    Write-Step "Stopping any previous instance (fresh restart)"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop.ps1")
    Write-Host ""
}

$needsSetup = $false
if (-not (Test-Path $VenvPython)) { $needsSetup = $true }
if (-not (Test-Path (Join-Path $FrontendDir "node_modules"))) { $needsSetup = $true }
if (-not (Test-Path (Join-Path $Root ".env"))) { $needsSetup = $true }

if ($needsSetup) {
    Write-Step "First run detected - running setup"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "setup.ps1")
    if ($LASTEXITCODE -ne 0) {
        Fail "Setup did not complete. Run setup.bat and fix the reported problem."
    }
}

$apiOpen = Test-PortOpen 8000
$uiOpen = Test-PortOpen 5173

$apiHealthy = $false
if ($apiOpen) {
    $apiHealthy = Wait-Http "$ApiUrl/api/health" 3 0
    if (-not $apiHealthy) {
        Fail "Port 8000 is already in use by another process that does not answer /api/health. Close it and retry."
    }
}

$uiHealthy = $false
if ($uiOpen) {
    $uiHealthy = Wait-Http "$UiUrl/" 3 0
    if (-not $uiHealthy) {
        Fail "Port 5173 is already in use by another process that does not serve the dashboard. Close it and retry."
    }
}

$backendPid = 0
$frontendPid = 0
$startedBackend = $false
$startedFrontend = $false

if ($apiHealthy) {
    Write-Host "    API already running on 127.0.0.1:8000" -ForegroundColor DarkGray
}
else {
    Write-Step "Starting API (FastAPI) on 127.0.0.1:8000"
    $backendOut = Join-Path $Logs "backend.out.log"
    $backendErr = Join-Path $Logs "backend.err.log"
    $proc = Start-Process -FilePath $VenvPython `
        -ArgumentList @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--no-access-log") `
        -WorkingDirectory $BackendDir `
        -RedirectStandardInput $NullInput `
        -RedirectStandardOutput $backendOut `
        -RedirectStandardError $backendErr `
        -WindowStyle Hidden -PassThru
    $backendPid = $proc.Id
    $startedBackend = $true
    Save-Pid "backend" $backendPid
    Write-Host "    PID $backendPid" -ForegroundColor DarkGray
}

if ($uiHealthy) {
    Write-Host "    Dashboard already running on 127.0.0.1:5173" -ForegroundColor DarkGray
}
else {
    $viteEntry = Join-Path $FrontendDir "node_modules\vite\bin\vite.js"
    if (-not (Test-Path $viteEntry)) {
        Fail "Vite is not installed in frontend\node_modules. Run setup.bat and retry."
    }
    Write-Step "Starting dashboard (Vite) on 127.0.0.1:5173"
    $frontendOut = Join-Path $Logs "frontend.out.log"
    $frontendErr = Join-Path $Logs "frontend.err.log"
    $proc = Start-Process -FilePath "node.exe" `
        -ArgumentList @($viteEntry) `
        -WorkingDirectory $FrontendDir `
        -RedirectStandardInput $NullInput `
        -RedirectStandardOutput $frontendOut `
        -RedirectStandardError $frontendErr `
        -WindowStyle Hidden -PassThru
    $frontendPid = $proc.Id
    $startedFrontend = $true
    Save-Pid "frontend" $frontendPid
    Write-Host "    PID $frontendPid" -ForegroundColor DarkGray
}

if ($startedBackend) {
    Write-Step "Waiting for the API to answer /api/health"
    if (-not (Wait-Http "$ApiUrl/api/health" 45 $backendPid)) {
        Show-Tail $backendErr
        Stop-Tracked $backendPid
        Remove-PidFile "backend"
        if ($startedFrontend) {
            Stop-Tracked $frontendPid
            Remove-PidFile "frontend"
        }
        Fail "The backend did not start within 45 seconds. See logs\backend.err.log and logs\backend.log."
    }
    Write-Host "    API ready" -ForegroundColor DarkGray
}

if ($startedFrontend) {
    Write-Step "Waiting for the dashboard"
    if (-not (Wait-Http "$UiUrl/" 45 $frontendPid)) {
        Show-Tail $frontendErr
        Stop-Tracked $frontendPid
        Remove-PidFile "frontend"
        if ($startedBackend) {
            Stop-Tracked $backendPid
            Remove-PidFile "backend"
        }
        Fail "The dashboard did not start within 45 seconds. See logs\frontend.err.log."
    }
    Write-Host "    Dashboard ready" -ForegroundColor DarkGray
}

$mode = "paper"
try {
    $health = Invoke-RestMethod -Uri "$ApiUrl/api/health" -TimeoutSec 5
    $mode = [string]$health.mode
}
catch {
    # mode display is cosmetic
}

Write-Host ""
Write-Host "FINAL60 is running." -ForegroundColor Green
Write-Host "    Dashboard: $UiUrl" -ForegroundColor Gray
Write-Host "    API docs:  $ApiUrl/docs" -ForegroundColor Gray
Write-Host "    Mode:      $($mode.ToUpper())" -ForegroundColor Gray
Write-Host "    Logs:      logs\backend.log, logs\frontend.out.log" -ForegroundColor DarkGray
if ($Detach) {
    Write-Host "    Stop:      stop.bat" -ForegroundColor DarkGray
}
else {
    Write-Host "    Stop:      press Ctrl+C (or Q) here, or run stop.bat" -ForegroundColor DarkGray
}
Write-Host ""

if (-not $NoBrowser) {
    Start-Process $UiUrl
}

if ($Detach) {
    exit 0
}

# Stay attached: Ctrl+C / Q stops the API and the dashboard. Ctrl+C is read as a normal key
# (no console signal), so the batch file never asks "Terminate batch job (Y/N)?".
$interactive = $true
try {
    [Console]::TreatControlCAsInput = $true
}
catch {
    $interactive = $false
}

$stopRequested = $false
try {
    while (-not $stopRequested) {
        if ($interactive) {
            try {
                while ([Console]::KeyAvailable) {
                    $key = [Console]::ReadKey($true)
                    $ctrlC = ($key.Key -eq [ConsoleKey]::C) -and (($key.Modifiers -band [ConsoleModifiers]::Control) -ne 0)
                    if ($ctrlC -or $key.Key -eq [ConsoleKey]::Q) {
                        $stopRequested = $true
                    }
                }
            }
            catch {
                $interactive = $false
            }
        }
        if ($stopRequested) { break }
        # stop.bat from another window (or a crash) ends the servers: nothing left to watch.
        if (-not (Test-PortOpen 8000) -and -not (Test-PortOpen 5173)) {
            Write-Host "Servers are no longer running." -ForegroundColor Yellow
            break
        }
        Start-Sleep -Milliseconds 400
    }
}
finally {
    try { [Console]::TreatControlCAsInput = $false } catch { }
}

if ($stopRequested) {
    Write-Host ""
    Write-Step "Stopping FINAL60"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "stop.ps1")
}

exit 0
