$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Write-Step([string]$Message) {
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "FINAL60 - Polymarket BTC 5m bot" -ForegroundColor Green
Write-Host "Local setup (backend + dashboard)" -ForegroundColor DarkGray
Write-Host ""

Write-Step "Checking Python"
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Fail "Python was not found on PATH. Install Python 3.12+ from python.org and reopen this window."
}
$pyVersion = (& python -c "import sys; print('%d.%d.%d' % sys.version_info[:3])").Trim()
$pyParts = $pyVersion.Split(".")
if (([int]$pyParts[0] -lt 3) -or (([int]$pyParts[0] -eq 3) -and ([int]$pyParts[1] -lt 11))) {
    Fail "Python 3.11 or newer is required; found $pyVersion."
}
if (([int]$pyParts[0] -eq 3) -and ([int]$pyParts[1] -lt 12)) {
    Write-Host "    WARNING: found Python $pyVersion. The project targets 3.12+; this code is 3.11-compatible." -ForegroundColor Yellow
}
Write-Host "    Python $pyVersion" -ForegroundColor DarkGray

Write-Step "Checking Node.js"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Fail "Node.js was not found on PATH. Install Node.js 20.19+ (or 22.12+) and reopen this window."
}
$nodeVersion = (& node --version).Trim().TrimStart("v")
$nodeMajor = [int]$nodeVersion.Split(".")[0]
if ($nodeMajor -lt 20) {
    Fail "Node.js 20.19+ is required by Vite 7; found $nodeVersion."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
    Fail "npm.cmd was not found on PATH. Reinstall Node.js with npm."
}
Write-Host "    Node $nodeVersion" -ForegroundColor DarkGray

$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating Python virtual environment (.venv)"
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { Fail "Could not create the virtual environment." }
}

Write-Step "Installing backend dependencies"
& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install -r (Join-Path $Root "backend\requirements.txt")
if ($LASTEXITCODE -ne 0) { Fail "pip install failed. Check your network connection and retry." }

Write-Step "Installing dashboard dependencies"
Push-Location (Join-Path $Root "frontend")
try {
    & cmd.exe /c "npm install --no-fund --no-audit"
    if ($LASTEXITCODE -ne 0) { Fail "npm install failed. Check your network connection and retry." }
}
finally {
    Pop-Location
}

if (-not (Test-Path (Join-Path $Root ".env"))) {
    Write-Step "Creating .env from .env.example"
    Copy-Item (Join-Path $Root ".env.example") (Join-Path $Root ".env")
    Write-Host "    Live trading stays disabled until you explicitly configure it." -ForegroundColor DarkGray
}

New-Item -ItemType Directory -Force -Path (Join-Path $Root "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $Root "logs") | Out-Null

Write-Step "Initializing the SQLite schema"
Push-Location (Join-Path $Root "backend")
try {
    & $VenvPython -c "from app.database.migrate import ensure_schema; ensure_schema(); print('    database ready')"
    if ($LASTEXITCODE -ne 0) { Fail "Database initialization failed." }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next: double-click start.bat (or run start.bat from this folder)." -ForegroundColor Gray
Write-Host "Dashboard: http://127.0.0.1:5173   API docs: http://127.0.0.1:8000/docs" -ForegroundColor DarkGray
Write-Host ""
exit 0
