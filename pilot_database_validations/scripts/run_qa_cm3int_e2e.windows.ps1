Param(
    [string]$Root = "C:\workspace\database-validations",
    [string]$PythonExe = "C:\workspace\cm3-batch-automations\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $PythonExe)) {
    Write-Error "Python executable not found at: $PythonExe"
    Write-Host "Tip: point -PythonExe to your app venv python, e.g. .venv\Scripts\python.exe"
    exit 1
}

$oracleScript = Join-Path $Root "tools\e2e_cm3int_oracle.py"
$artifactScript = Join-Path $Root "tools\e2e_cm3int_to_wave_artifacts.py"

if (-not (Test-Path $oracleScript)) {
    Write-Error "Missing script: $oracleScript"
    exit 1
}
if (-not (Test-Path $artifactScript)) {
    Write-Error "Missing script: $artifactScript"
    exit 1
}

Write-Host "[1/2] Running CM3INT Oracle E2E demo..."
& $PythonExe $oracleScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/2] Generating Wave artifacts from E2E output..."
& $PythonExe $artifactScript
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done. Key outputs:"
Write-Host "- $Root\generated\e2e_cm3int\summary.txt"
Write-Host "- $Root\generated\e2e_cm3int\wave_artifacts\summary-report.json"
Write-Host "- $Root\generated\e2e_cm3int\wave_artifacts\telemetry.json"
Write-Host "- $Root\generated\e2e_cm3int\wave_artifacts\promotion-evidence.json"
