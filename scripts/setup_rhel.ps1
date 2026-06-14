# PowerShell wrapper for setup_rhel.sh (intended for pwsh on Linux)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Get-Command bash -ErrorAction SilentlyContinue) {
  bash scripts/setup_rhel.sh
} else {
  throw "bash not found. Install bash to run setup_rhel.sh"
}
