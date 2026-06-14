Param(
  [string]$Repo = "https://github.com/pkanduri1/valdo.git",
  [string]$Branch = "main",
  [string]$Target = "/opt/valdo",
  [string]$OracleUser,
  [string]$OraclePassword,
  [string]$OracleDsn,
  [string]$Environment = "dev",
  [string]$LogLevel = "INFO",
  [switch]$Prune
)

$ErrorActionPreference = "Stop"

if (-not $OracleUser -or -not $OraclePassword -or -not $OracleDsn) {
  throw "OracleUser, OraclePassword, and OracleDsn are required."
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git not found" }
if (-not (Get-Command python3 -ErrorAction SilentlyContinue)) { throw "python3 not found" }

if (-not (Test-Path $Target)) { New-Item -ItemType Directory -Force -Path $Target | Out-Null }

if (Test-Path (Join-Path $Target ".git")) {
  git -C $Target fetch --all --prune
} else {
  git clone $Repo $Target
}

git -C $Target checkout $Branch
git -C $Target pull --ff-only origin $Branch

python3 -m venv "$Target/.venv"
& "$Target/.venv/bin/python" -m pip install --upgrade pip
& "$Target/.venv/bin/pip" install -r "$Target/requirements.txt"
& "$Target/.venv/bin/pip" install -e "$Target"

@"
ORACLE_USER=$OracleUser
ORACLE_PASSWORD=$OraclePassword
ORACLE_DSN=$OracleDsn
ENVIRONMENT=$Environment
LOG_LEVEL=$LogLevel
"@ | Set-Content -Path "$Target/.env" -Encoding UTF8

if ($Prune) {
  Write-Host "Prune requested. For full prune behavior use scripts/deploy_rhel_code_only.sh"
}

Write-Host "Deployment complete: $Target"
