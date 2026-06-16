# Valdo — Installation Guide

## Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | [python.org](https://www.python.org/downloads/) — install from internal mirror if no internet |
| Git | any | Optional — only needed if cloning from source |
| Oracle DB access | — | Thin mode only; **no Oracle Instant Client required** |

> **Financial institution note**: All commands below work offline. Dependencies must be available via your internal PyPI mirror. Set `PIP_INDEX_URL` or `pip.conf` to point to your mirror before running `pip install`.

---

## Quick Start — one command (recommended, local)

On macOS, Linux, RHEL, or WSL/Git Bash, a single command goes from a fresh
clone to a running Valdo on **SQLite with zero external infrastructure** (no
Oracle needed):

```bash
bash scripts/valdo-setup.sh
```

It detects your OS, finds Python 3.11+, creates/refreshes `.venv`, installs the
project + API dependencies, writes a local `.env` (SQLite defaults — **never**
overwriting an existing `.env`), creates the working directories, runs
`alembic upgrade head` against local SQLite, and smoke-checks `valdo info`. The
script is **idempotent** and safe to re-run.

```bash
source .venv/bin/activate        # .venv/Scripts/activate on Git Bash
valdo serve                      # Web UI at http://localhost:8000/ui
```

- `--env local` (default) is the only implemented target.
- `--env int` and `--env full-stack` are seams reserved for a future sprint and
  exit cleanly with a "deferred" message (see `docs/sprints/SPRINT_10_KICKOFF.md`).
- Native Windows (no WSL): use `scripts/setup_windows.ps1` or `setup-windows.bat`.

> **Local default = SQLite; INT/prod = Oracle.** `valdo-setup.sh` configures
> SQLite for zero-infra local dev. For Oracle-backed INT/prod, set the
> `ORACLE_*` / `DB_ADAPTER=oracle` variables in `.env` (see
> [Configuration](#configuration) and [Database Setup](#database-setup) below);
> the `--env int` Oracle-wiring path is deferred to a future sprint.

## Full-stack (docker-compose) — Valdo app + Postgres

> **Why here and not `DEPLOYMENT_OPTIONS.md`?** `docs/DEPLOYMENT_OPTIONS.md` is
> the RHEL **"No Docker"** production guide. This docker-compose stack is a
> **local developer convenience** for testing closer to a production database
> (Postgres) without external infra, so it lives next to the other local
> Quick-Start paths.

When you want to exercise Valdo against a real **PostgreSQL** instead of local
SQLite — closer to a production database, still zero external infra — use the
`docker-compose.yml` at the repo root. It requires only a running Docker daemon.

```bash
docker compose up -d --build      # build images, start the stack
```

### Topology

| Service | Image / build | Role |
|---|---|---|
| `db` | `postgres:16` | PostgreSQL; healthchecked with `pg_isready`; data on the named volume `valdo-pgdata`. |
| `migrate` | built from `Dockerfile` | One-shot. Runs `alembic upgrade head` against `db` (`DB_ADAPTER=postgresql`), then exits 0. |
| `valdo` | built from `Dockerfile` | `valdo serve` on port 8000. |

**Startup ordering (migration-safety gate):** `valdo` declares
`depends_on: migrate: condition: service_completed_successfully`, and `migrate`
declares `depends_on: db: condition: service_healthy`. So the chain is
**db healthy → migrate applies all migrations and exits 0 → valdo serves**.
The app can never come up against an un-migrated (empty) schema.

### Configuration

Every value has a sane inline default — `docker compose up` works with **zero
extra config**. The stack does **not** read your local `.env` (that one targets
SQLite). To override, set any of these in the shell or a root `.env`:

| Variable | Default | Notes |
|---|---|---|
| `DB_USER` / `DB_PASSWORD` / `DB_NAME` | `valdo` / `valdo` / `valdo` | Shared by `db`, `migrate`, `valdo`. |
| `VALDO_SESSION_SIGNING_KEY` | `dev-only-compose-signing-key-change-me` | Dev-only; lets the app boot if `auth.enabled` is on. **Never use outside local compose.** |

### Healthcheck & verification

`valdo` has a container healthcheck that probes `GET /api/v1/system/health`
(stdlib `urllib`, since the slim image has no `curl`). Confirm from the host:

```bash
docker compose ps                                   # valdo shows (healthy)
curl -fsS localhost:8000/api/v1/system/health       # {"status":"healthy",...}
curl -fsS localhost:8000/mcp/health                 # MCP server health
open http://localhost:8000/ui                        # Web UI
```

### Data persistence & teardown

PostgreSQL data lives in the Docker-managed named volume `valdo-pgdata`
(no host bind-mount, nothing written into the repo). It survives
`docker compose down` and is reused on the next `up`.

```bash
docker compose down        # stop + remove containers, KEEP the data volume
docker compose down -v     # also remove valdo-pgdata (fresh DB next time)
```

> **Not yet wired into the setup script.** `bash scripts/valdo-setup.sh
> --env full-stack` to drive this stack is a separate story (S11-2). For now,
> use the `docker compose` commands above directly.

---

## Quick Start — manual (3 steps)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux/macOS

# 2. Install the package
pip install -e .

# 3. Verify
valdo --help
```

---

## Windows Installation

### Step 1 — Copy the project folder

Place the project folder (e.g. `valdo`) somewhere on your machine such as `C:\Projects\valdo`. No admin rights are needed.

### Step 2 — Open a terminal in the project folder

- Press `Win + R`, type `cmd`, press Enter.
- Navigate to the project: `cd C:\Projects\valdo`

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

This installs all dependencies from `requirements.txt` and registers the `valdo` command. If your organisation uses an internal PyPI mirror:

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
valdo --help
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
git clone <repo-url> valdo
cd valdo

# Or copy the folder and cd into it
cd /opt/valdo
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
valdo --help
```

### Running as a systemd service (optional)

Create `/etc/systemd/system/valdo-api.service`:

```ini
[Unit]
Description=Valdo API
After=network.target

[Service]
Type=simple
User=appuser
WorkingDirectory=/opt/valdo
EnvironmentFile=/opt/valdo/.env
ExecStart=/opt/valdo/.venv/bin/uvicorn src.api.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable valdo-api
sudo systemctl start valdo-api
sudo systemctl status valdo-api
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
| `API_KEYS` | `key-tester:tester,key-owner:mapping_owner,key-admin:admin` | Comma-separated API keys with optional `:role` suffix (`tester`, `mapping_owner`, `admin`). |
| `ALLOWED_ORIGINS` | `https://internal-dashboard.bank.com` | Comma-separated CORS allowlist for browser callers. |

> **Oracle DSN format**: `hostname:port/service_name`. Example: `oracle-db.corp.local:1521/PRODDB`. This uses oracledb **thin mode** — no Oracle Instant Client libraries are required on the machine.

---

## Database Setup

### What requires the database

| Feature | Requires Oracle? | Notes |
|---|---|---|
| File validation (`validate`) | No | Runs locally against mapping JSON |
| File comparison (`compare`) | No | Runs locally |
| Oracle vs file tests (`oracle_vs_file` suites) | **Yes** | Queries source tables in Oracle |
| Run history (Recent Runs UI) | No | Stored in `reports/run_history.json` by default |
| Run history in Oracle | Optional | See below — enables multi-user history, BI queries |

### Configuring Oracle credentials

Copy `.env.example` to `.env` and set:

```
ORACLE_USER=APP_INT
ORACLE_PASSWORD=your_password
ORACLE_DSN=localhost:1521/FREEPDB1
```

The tool uses oracledb **thin mode** — no Oracle Instant Client is required.

### API key roles (RBAC)

- `tester`: run tests, view reports, read mappings
- `mapping_owner`: tester permissions + upload/delete mappings and upload rules
- `admin`: all permissions including admin-only system endpoints (`/api/v1/system/metrics`, `/api/v1/system/slo-alerts`)

Pass keys in the `X-API-Key` header.

> **`API_KEYS` is mandatory.** If the variable is unset, empty, or contains
> only whitespace, every protected endpoint returns HTTP `503 Service
> Unavailable` with the body `{"detail": "Server is not configured with API
> keys."}`. There is **no open mode** — the previous behaviour of allowing
> unauthenticated access when no keys were configured has been removed. Set
> `API_KEYS` in `.env` (or inject it from your secrets manager / Kubernetes
> Secret / Vault) **before starting the API server**, otherwise only
> `GET /api/v1/system/health` will be reachable.

### Source tables (SHAW→C360 validation)

The 17 Shaw source tables (`SHAW_SRC_P327`, `SHAW_SRC_ATOCTRAN`, `SHAW_SRC_EAC`, etc.) must already exist in the target Oracle schema. They are created and populated by the Shaw→C360 migration pipeline, not by this tool. Contact the DBA team for access.

To verify connectivity and confirm the expected tables are present:

```bash
valdo db-check
```

### Run history tables (optional)

By default, suite run history is saved to `reports/run_history.json`. This file is local to the machine running the tool.

If your team wants run history to persist across deployments, be visible to multiple users, or feed into a reporting dashboard, create two Oracle tables using the provided DDL script:

```bash
sqlplus APP_INT/<password>@localhost:1521/FREEPDB1 @sql/app_int/setup_app_run_history.sql
```

This creates:

| Table | Purpose |
|---|---|
| `APP_INT.APP_RUN_HISTORY` | One row per suite run — run ID, suite name, environment, status, pass/fail counts, report URL |
| `APP_INT.APP_RUN_TESTS` | One row per individual test within a run — test name, type, status, row count, duration |

> **Enabling DB run history:** Once the tables are created, set `ORACLE_USER`, `ORACLE_PASSWORD`, and `ORACLE_DSN` in your `.env` file. The tool will automatically dual-write every suite run to both `reports/run_history.json` (always) and the Oracle tables (when Oracle is configured). The Recent Runs UI and `GET /api/v1/runs/history` will read from the DB when available, with automatic fallback to JSON if the DB is unreachable.

**Useful queries once populated:**

```sql
-- Last 10 suite runs
SELECT run_id, suite_name, environment, status, pass_count, fail_count, run_timestamp
FROM APP_INT.APP_RUN_HISTORY
ORDER BY run_timestamp DESC
FETCH FIRST 10 ROWS ONLY;

-- Failure rate by suite name over the past 30 days
SELECT suite_name,
       COUNT(*) AS total_runs,
       SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS passed,
       SUM(CASE WHEN status = 'FAIL' THEN 1 ELSE 0 END) AS failed
FROM APP_INT.APP_RUN_HISTORY
WHERE run_timestamp >= SYSTIMESTAMP - INTERVAL '30' DAY
GROUP BY suite_name
ORDER BY suite_name;

-- All tests in a specific run
SELECT test_name, test_type, status, row_count, error_count, duration_secs
FROM APP_INT.APP_RUN_TESTS
WHERE run_id = '<paste-run-id-here>'
ORDER BY test_id;
```

---

## Verifying the Installation

Run these commands after installation. All should succeed.

```bash
# CLI is reachable
valdo --help

# Validate sub-command is available
valdo validate --help

# Unit tests pass (200+ tests expected)
python3 -m pytest tests/unit/ -q
```

> **About the sample files in `data/samples/`:** These are synthetic files generated for unit testing and developer onboarding only. They are not representative of real Shaw batch files and are intentionally incomplete — for example, `customers.txt` is missing a column present in the mapping, and `transactions.txt` uses simplified field widths. Running `validate` against them will produce errors. Use your own batch extract files and the corresponding mapping JSON from `config/mappings/` for real validation work.

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
