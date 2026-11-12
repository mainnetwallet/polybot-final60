$ErrorActionPreference = "Continue"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Logs = Join-Path $Root "logs"
$Pids = Join-Path $Logs "pids"
$ApiUrl = "http://127.0.0.1:8000"

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
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

function Get-PortOwner([int]$Port) {
    foreach ($line in (& netstat.exe -ano)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") {
            return [int]$Matches[1]
        }
    }
    return 0
}

function Find-ProjectServer([int]$Port) {
    $candidates = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.ToLower().Contains($Root.ToLower()) -and
        ($_.Name -ieq "node.exe" -or $_.Name -ieq "python.exe")
    }
    foreach ($item in $candidates) {
        $cmd = [string]$item.CommandLine
        if ($Port -eq 8000 -and $cmd.Contains("uvicorn") -and $cmd.Contains("--port 8000")) {
            return [int]$item.ProcessId
        }
        if ($Port -eq 5173 -and $cmd.Contains("vite")) {
            return [int]$item.ProcessId
        }
    }
    return 0
}

function Read-Pid([string]$Name) {
    $path = Join-Path $Pids "$Name.pid"
    if (-not (Test-Path $path)) {
        return 0
    }
    $raw = (Get-Content -Path $path -TotalCount 1 -ErrorAction SilentlyContinue)
    $value = 0
    if ([int]::TryParse(([string]$raw).Trim(), [ref]$value)) {
        return $value
    }
    return 0
}

function Remove-PidFile([string]$Name) {
    $path = Join-Path $Pids "$Name.pid"
    if (Test-Path $path) {
        Remove-Item $path -Force -ErrorAction SilentlyContinue
    }
}

function Test-TrackedProcess([int]$ProcessId, [string]$Name) {
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $info) {
        return $false
    }
    $cmd = [string]$info.CommandLine
    if (-not $cmd) {
        return $false
    }
    if ($Name -eq "backend") {
        return $cmd.Contains("uvicorn") -and $cmd.Contains("app.main:app")
    }
    if ($Name -eq "frontend") {
        return ($cmd.Contains("npm") -and $cmd.Contains("run") -and $cmd.Contains("dev")) -or $cmd.Contains("vite")
    }
    return $false
}

function Test-ProjectOwner([int]$ProcessId) {
    $info = Get-CimInstance Win32_Process -Filter "ProcessId=$ProcessId" -ErrorAction SilentlyContinue
    if (-not $info) {
        return $false
    }
    $cmd = [string]$info.CommandLine
    if (-not $cmd) {
        return $false
    }
    if (-not $cmd.ToLower().Contains($Root.ToLower())) {
        return $false
    }
    return ($info.Name -ieq "node.exe") -or ($info.Name -ieq "python.exe")
}

function Wait-PortClosed([int]$Port) {
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-PortOpen $Port)) {
            return $true
        }
        Start-Sleep -Milliseconds 500
    }
    return (-not (Test-PortOpen $Port))
}

Write-Host ""
Write-Host "FINAL60 - stopping local services" -ForegroundColor Green
Write-Host ""

if (Test-PortOpen 8000) {
    try {
        Invoke-RestMethod -Method Post -Uri "$ApiUrl/api/bot/stop" -TimeoutSec 5 | Out-Null
        Write-Host "    Bot stopped through the API; state is persisted." -ForegroundColor DarkGray
    }
    catch {
        Write-Host "    API not reachable for a graceful bot stop; continuing with process shutdown." -ForegroundColor DarkGray
    }
    Start-Sleep -Milliseconds 800
}

foreach ($name in @("frontend", "backend")) {
    $processId = Read-Pid $name
    if ($processId -le 0) {
        continue
    }
    if (Test-TrackedProcess $processId $name) {
        Write-Step "Stopping $name (PID $processId)"
        & taskkill.exe /PID $processId /T /F 2>$null | Out-Null
    }
    else {
        $exists = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($exists) {
            Write-Host "    PID $processId from $name.pid is not the tracked $name process; leaving it alone." -ForegroundColor Yellow
        }
    }
    Remove-PidFile $name
}

foreach ($port in @(8000, 5173)) {
    if (Wait-PortClosed $port) {
        continue
    }

    $processId = Find-ProjectServer $port
    if ($processId -le 0) {
        $processId = Get-PortOwner $port
    }
    if ($processId -gt 0 -and (Test-ProjectOwner $processId)) {
        Write-Step "Stopping leftover project process on port $port (PID $processId)"
        & taskkill.exe /PID $processId /T /F 2>$null | Out-Null
        if (-not (Wait-PortClosed $port)) {
            Write-Host "    WARNING: port $port is still in use; close it manually." -ForegroundColor Yellow
        }
    }
    elseif ($processId -gt 0) {
        Write-Host "    WARNING: port $port is held by PID $processId, which is not part of this project; leaving it alone." -ForegroundColor Yellow
    }
    else {
        Write-Host "    WARNING: port $port is still in use and its owner could not be identified; close it manually." -ForegroundColor Yellow
    }
}

Write-Host ""
Write-Host "Stopped. Data is saved in data\polymarket_bot.db." -ForegroundColor Green
Write-Host ""
exit 0
