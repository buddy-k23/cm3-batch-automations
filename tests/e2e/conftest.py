"""Shared fixtures for E2E Playwright tests."""

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Base URL for the running server. Defaults to https since tls.enabled=true is
# the new baseline; override with APP_E2E_BASE_URL for plain-HTTP environments.
BASE_URL = os.getenv("APP_E2E_BASE_URL", "https://127.0.0.1:8000")

# Repository root and config path used by the auth-enabled-server fixture
PROJECT_ROOT = Path(__file__).parent.parent.parent
UI_YML = PROJECT_ROOT / "config" / "ui.yml"


def _free_port() -> int:
    """Pick a free TCP port on 127.0.0.1 by binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def browser_type_launch_args(browser_type_launch_args):
    """Configure Playwright to use system Edge browser."""
    return {
        **browser_type_launch_args,
        "channel": "msedge",  # Use system Edge browser
    }


@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Tolerate self-signed certs so tls.strategy: self_signed works in tests."""
    return {
        **browser_context_args,
        "ignore_https_errors": True,
    }


@pytest.fixture(scope="session")
def base_url():
    return BASE_URL


@pytest.fixture
def ui_page(page, base_url):
    """Navigate to UI and wait for it to load."""
    page.goto(f"{base_url}/ui")
    page.wait_for_load_state("networkidle")
    return page


@pytest.fixture
def sample_pipe_file(tmp_path):
    """Create a sample pipe-delimited file."""
    f = tmp_path / "sample.txt"
    f.write_text("1|Alice|100\n2|Bob|200\n3|Charlie|300\n")
    return f


@pytest.fixture
def sample_pipe_file_b(tmp_path):
    """Create a second pipe-delimited file with differences."""
    f = tmp_path / "sample_b.txt"
    f.write_text("1|Alice|100\n2|Bob|999\n3|Charlie|300\n")
    return f


@pytest.fixture
def sample_csv_mapping(tmp_path):
    """Create a simple CSV mapping template."""
    f = tmp_path / "template.csv"
    f.write_text("field_name,data_type,position,length\nid,string,1,10\nname,string,11,20\nvalue,string,31,10\n")
    return f


# ---------------------------------------------------------------------------
# Auth-enabled server fixture (used by tests/e2e/test_e2e_login.py)
#
# Boots a dedicated uvicorn subprocess with config/ui.yml temporarily flipped
# to auth.enabled=true and cookie_secure=false, plus VALDO_E2E_STUB=1 so the
# guarded stub in src/api/routers/auth.py replaces ldap_authenticate with an
# in-memory fake. The original config/ui.yml is restored at teardown.
#
# This fixture launches a fresh process because src/api/main.py reads the
# auth config at module import time — flipping env vars inside an already-
# running pytest process will not flip the live FastAPI app.
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def auth_enabled_server(tmp_path_factory):
    """Boot uvicorn with auth.enabled=true and an in-memory LDAP stub.

    Yields:
        Base URL string of the running auth-enabled server, e.g.
        ``http://127.0.0.1:54321``. The fixture guarantees the server is
        already responding to /api/v1/system/health before yielding.
    """
    import httpx  # local import — only needed by this fixture

    if not UI_YML.exists():
        pytest.skip(f"config/ui.yml not found at {UI_YML}; cannot boot auth-enabled server")

    # 1) Backup config/ui.yml
    backup_dir = tmp_path_factory.mktemp("ui_yml_backup")
    backup_path = backup_dir / "ui.yml.bak"
    shutil.copy(UI_YML, backup_path)
    original_text = UI_YML.read_text(encoding="utf-8")

    # 2) Patch ui.yml in place: flip auth.enabled and cookie_secure to safe
    #    values for plain-HTTP E2E. The string-replace targets the EXACT
    #    indentation present in the checked-in file (two spaces under
    #    `auth:` and four spaces under `auth.session:`).
    patched = original_text.replace(
        "auth:\n  enabled: false",
        "auth:\n  enabled: true",
    )
    patched = patched.replace(
        "    cookie_secure: true",
        "    cookie_secure: false",
    )
    if patched == original_text:
        # If neither replacement matched, the file shape changed and the
        # fixture cannot proceed safely without altering YAML structure.
        pytest.skip(
            "config/ui.yml shape changed; auth_enabled_server fixture's "
            "string-replace targets did not match. Update conftest.py."
        )
    UI_YML.write_text(patched, encoding="utf-8")

    proc: subprocess.Popen | None = None
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    try:
        # 3) Build env for the uvicorn subprocess. We only set the keys that
        #    auth.enabled=true requires; everything else inherits from the
        #    parent process so existing API/DB config still works.
        audit_log = backup_dir / "audit.log"
        env = {
            **os.environ,
            "API_KEYS": "dev-key:admin",
            "VALDO_SESSION_SIGNING_KEY": "0123456789abcdef" * 4,  # 64 hex chars
            "LDAP_SERVICE_DN": "CN=svc,DC=test,DC=local",
            "LDAP_SERVICE_PASSWORD": "stub",
            "VALDO_E2E_STUB": "1",
            "ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
            "AUDIT_LOG_PATH": str(audit_log),
        }

        # 4) Launch uvicorn
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "src.api.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=str(PROJECT_ROOT),
            env=env,
        )

        # 5) Wait for /api/v1/system/health (no auth required)
        ready = False
        for _ in range(60):
            if proc.poll() is not None:
                # Process exited early — capture diagnostic info
                raise RuntimeError(
                    f"uvicorn subprocess exited with code {proc.returncode} "
                    "before becoming ready (auth-enabled server)."
                )
            try:
                r = httpx.get(f"{base}/api/v1/system/health", timeout=2.0)
                if r.is_success:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ready:
            raise RuntimeError(
                f"Auth-enabled server did not become healthy at {base} within timeout"
            )

        yield base
    finally:
        # 6) Tear down server
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        # 7) Restore config/ui.yml — best-effort, never raise from teardown
        try:
            UI_YML.write_text(original_text, encoding="utf-8")
        except Exception:
            pass
