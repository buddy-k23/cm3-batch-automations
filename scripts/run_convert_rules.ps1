Param(
  [string]$InputDir = "rules/csv",
  [string]$OutputDir = "config/rules"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

.\.venv\Scripts\python.exe scripts/bulk_convert_rules.py --input-dir $InputDir --output-dir $OutputDir
