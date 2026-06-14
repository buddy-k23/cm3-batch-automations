# PowerShell Core equivalent of setup_mac.sh (works on macOS/Linux with pwsh)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

python3 --version
python3 -m venv .venv

if (Test-Path ./.venv/bin/python) {
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/pip install -r requirements-dev.txt
  ./.venv/bin/pip install -e .
} else {
  throw "Expected .venv/bin not found."
}

New-Item -ItemType Directory -Force -Path reports, mappings/csv, rules/csv, data/files | Out-Null
Write-Host "Setup complete. Use: source .venv/bin/activate"
