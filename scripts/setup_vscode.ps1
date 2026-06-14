$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VscodeDir = Join-Path $ProjectRoot ".vscode"

New-Item -ItemType Directory -Force -Path $VscodeDir | Out-Null

@'
{
  "python.defaultInterpreterPath": "${workspaceFolder}\\.venv\\Scripts\\python.exe",
  "python.terminal.activateEnvironment": true,
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": [
    "tests/unit",
    "-q",
    "-o",
    "addopts="
  ],
  "files.associations": {
    "*.yml": "yaml",
    "*.yaml": "yaml"
  }
}
'@ | Set-Content -Path (Join-Path $VscodeDir "settings.json") -Encoding UTF8

@'
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "Valdo: Setup (Windows)",
      "type": "shell",
      "command": "powershell -ExecutionPolicy Bypass -File scripts/setup_windows.ps1",
      "group": "build"
    },
    {
      "label": "Valdo: Run Unit Tests",
      "type": "shell",
      "command": ".\\.venv\\Scripts\\pytest.exe -q -o addopts='' tests\\unit",
      "group": "test"
    },
    {
      "label": "Valdo: Validate Sample",
      "type": "shell",
      "command": "valdo validate -f data/samples/customers.txt -m config/mappings/customer_mapping.json -o reports/validation.html --detailed"
    }
  ]
}
'@ | Set-Content -Path (Join-Path $VscodeDir "tasks.json") -Encoding UTF8

Write-Host "VS Code workspace files created under .vscode/"
