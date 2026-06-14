Param(
  [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

Write-Host "[1/6] Checking Python..."
$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
  Write-Error "python not found. Install Python 3.11+ and re-run."
}

$pyVer = python -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
Write-Host "Found Python $pyVer"

Write-Host "[2/6] Creating virtual environment (.venv)..."
python -m venv .venv

Write-Host "[3/6] Upgrading pip..."
.\.venv\Scripts\python.exe -m pip install --upgrade pip

Write-Host "[4/6] Installing project dependencies..."
.\.venv\Scripts\pip.exe install -r requirements-dev.txt

Write-Host "[5/6] Installing CLI entrypoint (editable)..."
.\.venv\Scripts\pip.exe install -e .

Write-Host "[6/6] Creating working folders..."
New-Item -ItemType Directory -Force -Path reports, mappings\csv, rules\csv, data\files | Out-Null

if (-not $SkipTests) {
  Write-Host "Running quick unit tests..."
  .\.venv\Scripts\pytest.exe -q -o addopts='' tests\unit
}

Write-Host ""
Write-Host "Setup complete"
Write-Host "Try these commands:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  valdo --help"
Write-Host "  valdo info"
