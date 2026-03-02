# CM3 Batch Automations — Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) — install from internal mirror if no internet |
| Git | any | Optional — only needed if cloning from source |
| Oracle DB access | — | Thin mode only; **no Oracle Instant Client required** |

> **Financial institution note**: All commands below work offline. Dependencies must be available via your internal PyPI mirror. Set `PIP_INDEX_URL` or `pip.conf` to point to your mirror before running `pip install`.

---

## Quick Start (3 steps)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 2. Install the package
pip install -e .

# 3. Verify
cm3-batch --help
```

---

## Windows Installation

### Step 1 — Copy the project folder

Place the project folder (e.g. `cm3-batch-automations`) somewhere on your machine such as `C:\Projects\cm3-batch-automations`. No admin rights are needed.

### Step 2 — Open a terminal in the project folder

- Press `Win + R`, type `cmd`, press Enter.
- Navigate to the project: `cd C:\Projects\cm3-batch-automations`

Or right-click the folder in Explorer and choose **"Open in Terminal"** / **"Open PowerShell window here"**.

### Step 3 — Create a virtual environment

```cmd
python -m venv .venv
```

If `python` is not found, check that Python 3.10+ is installed and on your PATH. Run `python --version` to confirm.

### Step 4 — Activate the virtual environment

```cmd
.venv\Scripts\activate
```

Your prompt will change to show `(.venv)` at the start.

### Step 5 — Install dependencies

```cmd
pip install -e .
```

This installs all dependencies from `requirements.txt` and registers the `cm3-batch` command. If your organisation uses an internal PyPI mirror:

```cmd
pip install -e . --index-url http://your-internal-pypi/simple/
```

### Step 6 — Configure environment variables

```cmd
copy .env.example .env
```

Open `.env` in Notepad and fill in your Oracle credentials (see the [Configuration](#configuration) section).

### Step 7 — Verify the installation

```cmd
cm3-batch --help
```

### Step 8 — Start the API server (optional)

```cmd
uvicorn src.api.main:app --host 0.0.0.0 --port 8000
```

Open a browser and go to `http://localhost:8000/docs` to see the interactive API docs.

> **Tip**: Use `setup-windows.bat` to automate steps 3–6 above.

---

## Linux Installation

### Step 1 — Copy or clone the project

```bash
# If using Git:
git clone <repo-url> cm3-batch-automations
cd cm3-batch-automations

# Or copy the folder and cd into it
cd /opt/cm3-batch-automations
```

### Step 2 — Create and activate a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### Step 3 — Install dependencies

```bash
pip install -e .
```

### Step 4 — Configure environment variables

```bash
cp .env.example .env
nano .env   # or vi .env
```

Set `ORACLE_DSN` in the format `host:port/service_name` (e.g. `db-server.corp:1521/ORCL`).

### Step 5 — Verify

```bash
cm3-batch --help
```

### Running as a systemd service (optional)

Create `/etc/systemd/system/cm3-batch-api.service`:

```ini
[Unit]
Description=CM3 Batch Automations API
After=network.target

[Service]
Type=simple
User=appuser
WorkingDirectory=/opt/cm3-batch-automations
EnvironmentFile=/opt/cm3-batch-automations/.env
ExecStart=/opt/cm3-batch-automations/.venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable cm3-batch-api
sudo systemctl start cm3-batch-api
sudo systemctl status cm3-batch-api
```

> **Tip**: Use `setup-linux.sh` to automate steps 2–4.

---

## VSCode Setup

### Recommended Extensions

- **Python** (Microsoft)
- **Pylance** (Microsoft)
- **Python Debugger** (Microsoft)
- **Even Better TOML** (tamasfe) — for config files
- **GitLens** (GitKraken) — optional

Install extensions via the Extensions panel (`Ctrl+Shift+X`). Search by the names above.

### Workspace Settings

A `.vscode/settings.json` file is included in the project. It configures:

- The Python interpreter to use `.venv` automatically
- pytest to run from `tests/unit/`
- Auto-format on save (using black)
- Hides generated files (`__pycache__`, `.venv`, `uploads`) from the Explorer

### Launch Configurations

Two debug configurations are included in `.vscode/launch.json`:

| Configuration | What it does |
|---|---|
| **Debug API Server** | Starts uvicorn with `--reload`; attach breakpoints in `src/api/` |
| **Debug CLI: run-tests** | Runs `src/main.py run-tests`; prompts for `--suite` path |

Press `F5` or open the **Run and Debug** panel (`Ctrl+Shift+D`) and select a configuration.

### Tasks

Common tasks are available in `.vscode/tasks.json` via **Terminal > Run Task**:

| Task | Description |
|---|---|
| Start API Server | Starts the uvicorn dev server |
| Run Unit Tests | Runs pytest on `tests/unit/` |
| Run Test Suite (example) | Dry-run of the example test suite |
| Install/Update Dependencies | Runs `pip install -e .` |

---

## Configuration

All configuration is loaded from the `.env` file in the project root. Copy `.env.example` to `.env` to get started.

| Variable | Example | Description |
|---|---|---|
| `ORACLE_USER` | `batch_user` | Oracle database username |
| `ORACLE_PASSWORD` | `s3cr3t` | Oracle database password |
| `ORACLE_DSN` | `db-host:1521/ORCL` | Connection string — format is `host:port/service_name`. **No Instant Client needed.** |
| `ENVIRONMENT` | `dev` | One of `dev`, `staging`, `prod`. Controls log verbosity and safety checks. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`. Use `DEBUG` when troubleshooting. |
| `FILE_RETENTION_HOURS` | `24` | Uploaded/temp files older than this many hours are removed on startup. Default: `24`. |

> **Oracle DSN format**: `hostname:port/service_name`. Example: `oracle-db.corp.local:1521/PRODDB`. This uses oracledb **thin mode** — no Oracle Instant Client libraries are required on the machine.

---

## Verifying the Installation

Run these commands after installation. All should succeed.

```bash
# CLI is reachable
cm3-batch --help

# Validate sub-command is available
cm3-batch validate --help

# Unit tests pass (200+ tests expected)
python3 -m pytest tests/unit/ -q
```

---

## Troubleshooting

**`ModuleNotFoundError: No module named 'src'`**

The package has not been installed in editable mode. Run:
```bash
pip install -e .
```

**`DPI-1047: Cannot locate a 64-bit Oracle Client library`**

This error appears when oracledb falls back to thick mode. The project uses **thin mode** — you do not need Oracle Instant Client. Ensure you are not setting `ORACLE_HOME` or `LD_LIBRARY_PATH` to an invalid path, and that no code calls `oracledb.init_oracle_client()`.

**Port 8000 is already in use**

Start the API on a different port:
```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8001
```

**`Permission denied` on `uploads/`**

```bash
mkdir -p uploads
chmod 755 uploads
```

On Windows, right-click the `uploads` folder > Properties > Security and grant your user write access.

**`pip` cannot find packages (no internet)**

Configure pip to use your internal mirror:
```bash
pip install -e . --index-url http://your-pypi-mirror.corp/simple/
```
Or add this to `%APPDATA%\pip\pip.ini` (Windows) / `~/.config/pip/pip.ini` (Linux):
```ini
[global]
index-url = http://your-pypi-mirror.corp/simple/
```

---

## Upgrading

When a new version of the project is available:

```bash
# Pull latest changes (if using Git)
git pull

# Re-install in editable mode (picks up any new dependencies)
pip install -e .

# Run tests to confirm nothing is broken
python3 -m pytest tests/unit/ -q
```
