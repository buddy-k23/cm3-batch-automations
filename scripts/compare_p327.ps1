$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

$file1 = "data/samples/p327_test_data_a_20000.txt"
$file2 = "data/samples/p327_test_data_b_20000.txt"

Write-Host "Comparing P327 test files..."
Write-Host "File 1: $file1"
Write-Host "File 2: $file2"

if (-not (Test-Path $file1) -or -not (Test-Path $file2)) {
  throw "One or both files not found"
}

$diff = Compare-Object (Get-Content $file1) (Get-Content $file2)
if (-not $diff) {
  Write-Host "✓ Files are identical"
} else {
  Write-Host "✗ Files have differences"
  New-Item -ItemType Directory -Force -Path reports | Out-Null
  $diff | Out-File reports/p327_diff.txt -Encoding UTF8
  Write-Host "Saved diff to reports/p327_diff.txt"
}
