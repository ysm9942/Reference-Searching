# setup.ps1 — one-shot installer for Reference-Searching PoC
#
# What it does:
#   1) verifies Python and Chrome are installed
#   2) creates .venv (skipped if it already exists)
#   3) upgrades pip + installs everything in requirements.txt
#
# How to run:
#   PowerShell -ExecutionPolicy Bypass -File setup.ps1
#
# After setup, run the PoC without activating venv:
#   .\.venv\Scripts\python.exe poc_shopping_sticker.py "https://www.youtube.com/shorts/XXXX"

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "`n[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red }

Write-Host "=== Reference-Searching setup ===" -ForegroundColor Magenta

# --- 1. Python ---
Write-Step "Checking Python"
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Err "Python not found on PATH. Install Python 3.10+ from https://www.python.org/downloads/"
    exit 1
}
$pyVer = & python --version 2>&1
Write-Ok "$pyVer ($($pythonCmd.Source))"

# --- 2. Chrome (required by undetected_chromedriver) ---
Write-Step "Checking Chrome"
$chromePaths = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LocalAppData}\Google\Chrome\Application\chrome.exe"
)
$chromeFound = $chromePaths | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($chromeFound) {
    Write-Ok "Chrome at $chromeFound"
} else {
    Write-Warn "Chrome not found in standard paths."
    Write-Warn "undetected_chromedriver needs real Chrome. Install: https://www.google.com/chrome/"
    Write-Warn "(continuing — install Chrome before running the PoC)"
}

# --- 3. venv ---
Write-Step "Setting up virtual environment (.venv)"
if (Test-Path ".venv") {
    Write-Ok ".venv already exists, reusing"
} else {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Err "venv creation failed"
        exit 1
    }
    Write-Ok "created .venv"
}

$venvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Write-Err "venv Python not found at $venvPy"
    exit 1
}

# --- 4. install ---
Write-Step "Installing dependencies"
& $venvPy -m pip install --upgrade pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Write-Err "pip upgrade failed"; exit 1 }

& $venvPy -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Err "requirements install failed"; exit 1 }

Write-Ok "all dependencies installed"

# --- 5. usage hint ---
Write-Host "`n=== Setup done ===" -ForegroundColor Magenta
Write-Host "Run the PoC (no activation needed):" -ForegroundColor White
Write-Host '  .\.venv\Scripts\python.exe poc_shopping_sticker.py "https://www.youtube.com/shorts/ovAFK2ASguw"' -ForegroundColor Gray
Write-Host ""
Write-Host "First run downloads a patched chromedriver (~10-20s)." -ForegroundColor DarkGray
