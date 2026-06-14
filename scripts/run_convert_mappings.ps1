Param(
  [string]$InputDir = "mappings/csv",
  [string]$OutputDir = "config/mappings",
  [string]$Format = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$cmd = @("scripts/bulk_convert_mappings.py", "--input-dir", $InputDir, "--output-dir", $OutputDir)
if ($Format -ne "") { $cmd += @("--format", $Format) }

.\.venv\Scripts\python.exe @cmd
