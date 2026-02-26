@echo off
setlocal ENABLEDELAYEDEXPANSION

REM Usage:
REM   run_qa_cm3int_e2e.windows.bat [ROOT] [PYTHON_EXE]
REM Example:
REM   run_qa_cm3int_e2e.windows.bat C:\workspace\database-validations C:\workspace\cm3-batch-automations\.venv\Scripts\python.exe

set "ROOT=%~1"
if "%ROOT%"=="" set "ROOT=C:\workspace\database-validations"

set "PYTHON_EXE=%~2"
if "%PYTHON_EXE%"=="" set "PYTHON_EXE=C:\workspace\cm3-batch-automations\.venv\Scripts\python.exe"

if not exist "%PYTHON_EXE%" (
  echo ERROR: Python executable not found at "%PYTHON_EXE%"
  echo Tip: pass explicit PYTHON_EXE path as 2nd argument.
  exit /b 1
)

set "ORACLE_SCRIPT=%ROOT%\tools\e2e_cm3int_oracle.py"
set "ARTIFACT_SCRIPT=%ROOT%\tools\e2e_cm3int_to_wave_artifacts.py"

if not exist "%ORACLE_SCRIPT%" (
  echo ERROR: Missing script "%ORACLE_SCRIPT%"
  exit /b 1
)
if not exist "%ARTIFACT_SCRIPT%" (
  echo ERROR: Missing script "%ARTIFACT_SCRIPT%"
  exit /b 1
)

echo [1/2] Running CM3INT Oracle E2E demo...
"%PYTHON_EXE%" "%ORACLE_SCRIPT%"
if errorlevel 1 exit /b %errorlevel%

echo [2/2] Generating Wave artifacts from E2E output...
"%PYTHON_EXE%" "%ARTIFACT_SCRIPT%"
if errorlevel 1 exit /b %errorlevel%

echo Done. Key outputs:
echo - %ROOT%\generated\e2e_cm3int\summary.txt
echo - %ROOT%\generated\e2e_cm3int\wave_artifacts\summary-report.json
echo - %ROOT%\generated\e2e_cm3int\wave_artifacts\telemetry.json
echo - %ROOT%\generated\e2e_cm3int\wave_artifacts\promotion-evidence.json

exit /b 0
