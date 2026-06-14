Param(
  [string]$InputFile = "/Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample.txt",
  [string]$OutputFile = "/Users/buddy/Downloads/valdo-main-mappings-csv/mappings/csv/p327_sample_200k.txt",
  [int]$TargetCount = 200000
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path $InputFile)) { throw "Input file not found: $InputFile" }
if ($TargetCount -le 0) { throw "TargetCount must be > 0" }

$lines = Get-Content -Path $InputFile
if (-not $lines -or $lines.Count -eq 0) { throw "No source rows found" }

function Set-Field([string]$record, [int]$start1, [int]$len, [string]$value) {
  $start = $start1 - 1
  if ($value.Length -gt $len) { $value = $value.Substring(0, $len) }
  if ($value.Length -lt $len) { $value = $value + (" " * ($len - $value.Length)) }

  if ($record.Length -lt ($start + $len)) {
    $record = $record + (" " * (($start + $len) - $record.Length))
  }

  $prefix = if ($start -gt 0) { $record.Substring(0, $start) } else { "" }
  $suffix = if (($start + $len) -lt $record.Length) { $record.Substring($start + $len) } else { "" }
  return $prefix + $value + $suffix
}

$results = New-Object System.Collections.Generic.List[string]
for ($i = 1; $i -le $TargetCount; $i++) {
  $src = $lines[($i - 1) % $lines.Count]

  $location = ($i % 1000000).ToString("D6")
  $acct = $i.ToString("D18")
  $bal = "+" + (($i * 137) % 999999999999999999).ToString("D18")

  $rec = Set-Field $src 1 6 $location
  $rec = Set-Field $rec 7 18 $acct
  $rec = Set-Field $rec 46 19 $bal
  $results.Add($rec)
}

$parent = Split-Path -Parent $OutputFile
if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
$results | Set-Content -Path $OutputFile -Encoding UTF8

Write-Host "Done. Wrote $TargetCount records to $OutputFile"
