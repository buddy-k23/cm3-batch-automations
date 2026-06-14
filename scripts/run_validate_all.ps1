Param(
  [string]$Manifest = "config/validation_manifest.csv",
  [switch]$AutoDiscover
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$argsList = @("scripts/validate_data_files.py", "--manifest", $Manifest, "--default-chunked")
if ($AutoDiscover) { $argsList += "--auto-discover" }

.\.venv\Scripts\python.exe @argsList
