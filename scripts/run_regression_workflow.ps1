Param(
  [string]$Config = "config/pipeline/regression_workflow.sample.json",
  [string]$SummaryOut = "reports/regression_workflow/summary.json"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$argsList = @(
  "scripts/run_regression_workflow.py",
  "--config", $Config,
  "--summary-out", $SummaryOut
)

.\.venv\Scripts\python.exe @argsList
