@echo off
REM CM3 Batch Automations — Windows Setup Script
REM Run this script from the project root directory.
REM No administrator rights required.

setlocal enabledelayedexpansion

echo.
echo =========================================
echo  CM3 Batch Automations — Windows Setup
echo =========================================
echo.

REM --- Check Python is available ---
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python was not found on your PATH.
    echo.
    echo Please install Python 3.10 or later from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo Found Python %PYVER%

REM --- Create virtual environment if it does not already exist ---
if not exist ".venv\" (
    echo.
    echo Creating virtual environment in .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo Done.
) else (
    echo Virtual environment already exists, skipping creation.
)

REM --- Activate virtual environment ---
echo.
echo Activating virtual environment ...
call .venv\Scripts\activate.bat
if errorlevel 1 (
    echo ERROR: Failed to activate virtual environment.
    pause
    exit /b 1
)

REM --- Install / upgrade pip silently ---
echo Upgrading pip ...
python -m pip install --upgrade pip --quiet

REM --- Install project dependencies ---
echo.
echo Installing dependencies (pip install -e .) ...
pip install -e .
if errorlevel 1 (
    echo.
    echo ERROR: pip install failed.
    echo If you are behind a corporate proxy without internet access, configure your
    echo internal PyPI mirror first:
    echo   pip install -e . --index-url http://your-pypi-mirror.corp/simple/
    pause
    exit /b 1
)

REM --- Copy .env.example to .env if .env does not exist ---
if not exist ".env" (
    if exist ".env.example" (
        echo.
        echo Copying .env.example to .env ...
        copy ".env.example" ".env" >nul
        echo Done. Open .env in a text editor and fill in your Oracle credentials.
    ) else (
        echo WARNING: .env.example not found. Please create a .env file manually.
    )
) else (
    echo .env already exists, skipping copy.
)

REM --- Create uploads directory if missing ---
if not exist "uploads\" (
    mkdir uploads
)

REM --- Print next steps ---
echo.
echo =========================================
echo  Setup complete!
echo =========================================
echo.
echo Next steps:
echo   1. Edit .env and set ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN
echo      (format: host:port/service_name — no Oracle Instant Client needed)
echo.
echo   2. Activate the virtual environment in each new terminal:
echo        .venv\Scripts\activate
echo.
echo   3. Verify the installation:
echo        cm3-batch --help
echo.
echo   4. Start the API server (optional):
echo        uvicorn src.api.main:app --host 0.0.0.0 --port 8000
echo.

endlocal
