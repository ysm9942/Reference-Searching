# build.ps1 — build Setup.exe + Pipeline.exe with PyInstaller
#
# Output:
#   dist\Reference-Searching\
#       Setup.exe       ← GUI wizard: API key input + .env creation
#       Pipeline.exe    ← silent pipeline runner (logs to pipeline.log)
#       web\
#           index.html
#           results.json
#
# Usage:
#   PowerShell -ExecutionPolicy Bypass -File build.ps1
#
# Prerequisites: .venv already created by setup.ps1 (with pyinstaller installed).
# If pyinstaller isn't there yet, this script will install it.

$ErrorActionPreference = "Stop"

function Step($msg) { Write-Host "`n[$([DateTime]::Now.ToString('HH:mm:ss'))] $msg" -ForegroundColor Cyan }
function Ok($msg)   { Write-Host "  OK  $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "  !!  $msg" -ForegroundColor Yellow }
function Die($msg)  { Write-Host "  XX  $msg" -ForegroundColor Red; exit 1 }

Write-Host "=== Reference-Searching: PyInstaller build ===" -ForegroundColor Magenta

# ── 1. venv check ──
$venvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    Die ".venv not found. Run setup.ps1 first."
}
Ok "venv at $venvPy"

# ── 2. ensure pyinstaller ──
Step "Ensuring PyInstaller is installed"
& $venvPy -m pip install --upgrade pyinstaller pyinstaller-hooks-contrib | Out-Null
if ($LASTEXITCODE -ne 0) { Die "pyinstaller install failed" }
Ok "PyInstaller ready"

# ── 3. clean previous build ──
Step "Cleaning previous dist/ and build/"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build, Setup.spec, Pipeline.spec
Ok "cleaned"

# Common PyInstaller flags shared by both entries
$commonFlags = @(
    "--noconfirm",
    "--clean",
    "--windowed",
    "--onefile",
    "--collect-all", "undetected_chromedriver",
    "--collect-all", "selenium",
    "--collect-all", "googleapiclient",
    "--collect-all", "google.api_core",
    "--collect-all", "pytrends",
    "--hidden-import", "phase1_trends",
    "--hidden-import", "phase2_youtube_search",
    "--hidden-import", "phase3_extract",
    "--hidden-import", "phase4_output",
    "--hidden-import", "poc_shopping_sticker"
)

# ── 4. Setup.exe ──
Step "Building Setup.exe"
& $venvPy -m PyInstaller @commonFlags --name Setup entry_setup.py
if ($LASTEXITCODE -ne 0) { Die "Setup build failed" }
Ok "Setup.exe built"

# ── 5. Pipeline.exe ──
Step "Building Pipeline.exe"
& $venvPy -m PyInstaller @commonFlags --name Pipeline entry_pipeline.py
if ($LASTEXITCODE -ne 0) { Die "Pipeline build failed" }
Ok "Pipeline.exe built"

# ── 6. assemble distribution folder ──
Step "Assembling dist\Reference-Searching\"
$out = "dist\Reference-Searching"
New-Item -ItemType Directory -Force -Path $out | Out-Null

Move-Item -Force "dist\Setup.exe"    "$out\Setup.exe"
Move-Item -Force "dist\Pipeline.exe" "$out\Pipeline.exe"

# Include web/ so Pipeline.exe has somewhere to write results.json
New-Item -ItemType Directory -Force -Path "$out\web" | Out-Null
Copy-Item "web\index.html"     "$out\web\index.html"
Copy-Item "web\results.json"   "$out\web\results.json"

# Minimal quick-start
@'
Reference-Searching
====================

1. Run Setup.exe — enter your YouTube Data API v3 key, click Save.
2. Run Pipeline.exe — runs in background, logs to pipeline.log.
   (Chrome will appear during Phase 3 — this is required for stealth.)
3. To publish results to your Vercel site:
     copy web\results.json into your git repo
     git add, commit, push
   Vercel auto-deploys within ~60s.

If something fails, open pipeline.log to see what happened.
'@ | Out-File -Encoding utf8 "$out\README.txt"

# Compute sizes
$setupSize = "{0:N1} MB" -f ((Get-Item "$out\Setup.exe").Length / 1MB)
$pipeSize  = "{0:N1} MB" -f ((Get-Item "$out\Pipeline.exe").Length / 1MB)

Write-Host "`n=== Build complete ===" -ForegroundColor Magenta
Write-Host "Setup.exe    $setupSize"
Write-Host "Pipeline.exe $pipeSize"
Write-Host "`nOutput folder: $((Get-Location).Path)\$out"
Write-Host "Zip the whole folder to distribute." -ForegroundColor DarkGray
