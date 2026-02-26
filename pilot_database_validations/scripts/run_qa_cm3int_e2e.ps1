Param(
    [string]$Root = "/Users/buddy/.openclaw/workspace/database-validations",
    [string]$AppVenvActivate = "/Users/buddy/.openclaw/workspace/cm3-batch-automations/.venv/bin/activate"
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path $AppVenvActivate)) {
    Write-Error "App venv activate script not found at: $AppVenvActivate"
    exit 1
}

Write-Host "[1/2] Running CM3INT Oracle E2E demo..."
$cmd1 = @"
source '$AppVenvActivate'
python '$Root/tools/e2e_cm3int_oracle.py'
"@

bash -lc $cmd1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "[2/2] Generating Wave artifacts from E2E output..."
$cmd2 = @"
source '$AppVenvActivate'
python '$Root/tools/e2e_cm3int_to_wave_artifacts.py'
"@

bash -lc $cmd2
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Done. Key outputs:"
Write-Host "- $Root/generated/e2e_cm3int/summary.txt"
Write-Host "- $Root/generated/e2e_cm3int/wave_artifacts/summary-report.json"
Write-Host "- $Root/generated/e2e_cm3int/wave_artifacts/telemetry.json"
Write-Host "- $Root/generated/e2e_cm3int/wave_artifacts/promotion-evidence.json"
