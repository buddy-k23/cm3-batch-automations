# DB Profiles Dropdown Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow operators to define named DB connections in `config/db_connections.yaml`; the DB Compare tab shows a dropdown to pick one — password never leaves the server.

**Architecture:** A new `db_profiles_service.py` loads the YAML and resolves passwords from env vars. A new `GET /api/v1/system/db-profiles` endpoint serves profile names + non-sensitive fields. The `db-ping` and `db-compare` endpoints each gain an optional `profile_name` form field; when set, credentials are resolved server-side. The UI adds a profile `<select>` above the existing manual form fields; selecting a named profile hides those fields and sends only `profile_name` to the API.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, PyYAML, Vanilla JS (ES5-compatible), pytest

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `config/db_connections.yaml` | Create | Sample named DB profiles config |
| `src/api/models/db_profile.py` | Create | `DbProfile` Pydantic model (no password) |
| `src/services/db_profiles_service.py` | Create | `load_profiles()`, `resolve_profile()` |
| `src/api/routers/system.py` | Modify | Add `GET /db-profiles`; update `db-ping` |
| `src/api/routers/files.py` | Modify | Update `db-compare` to accept `profile_name` |
| `src/reports/static/ui.html` | Modify | Add profile dropdown row to connection form |
| `src/reports/static/ui.js` | Modify | Fetch profiles, populate dropdown, toggle fields, update run handler |
| `tests/unit/test_db_profiles_service.py` | Create | Unit tests for service |
| `tests/unit/test_api_db_profiles.py` | Create | Unit tests for new/modified endpoints |
| `tests/e2e/test_db_compare_profiles.py` | Create | E2E tests for profile dropdown UI |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | Modify | Document `config/db_connections.yaml` |

---

## Task 1: `DbProfile` model + `db_profiles_service` (TDD)

**Files:**
- Create: `src/api/models/db_profile.py`
- Create: `src/services/db_profiles_service.py`
- Create: `tests/unit/test_db_profiles_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_db_profiles_service.py
"""Unit tests for db_profiles_service — written before implementation (TDD)."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest


class TestLoadProfiles:
    def test_returns_empty_list_when_file_absent(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        result = load_profiles(tmp_path / "nonexistent.yaml")
        assert result == []

    def test_returns_empty_list_on_yaml_parse_error(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        bad = tmp_path / "bad.yaml"
        bad.write_text(": : invalid yaml :::", encoding="utf-8")
        result = load_profiles(bad)
        assert result == []

    def test_returns_empty_list_when_connections_key_missing(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text("other_key: []\n", encoding="utf-8")
        result = load_profiles(f)
        assert result == []

    def test_loads_single_profile(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Local Dev"
                adapter: oracle
                host: "localhost:1521/FREEPDB1"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "ORACLE_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.name == "Local Dev"
        assert p.adapter == "oracle"
        assert p.host == "localhost:1521/FREEPDB1"
        assert p.user == "APP_INT"
        assert p.schema == "APP_INT"
        assert p.password_env == "ORACLE_PASSWORD"

    def test_password_env_set_true_when_env_var_present(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import load_profiles
        monkeypatch.setenv("ORACLE_PASSWORD", "secret")
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Local Dev"
                adapter: oracle
                host: "localhost:1521/FREEPDB1"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "ORACLE_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert profiles[0].password_env_set is True

    def test_password_env_set_false_when_env_var_absent(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import load_profiles
        monkeypatch.delenv("DB_MISSING_PASSWORD", raising=False)
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Prod"
                adapter: oracle
                host: "prod:1521/PROD"
                user: "APP_INT"
                schema: "APP_INT"
                password_env: "DB_MISSING_PASSWORD"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert profiles[0].password_env_set is False

    def test_loads_multiple_profiles(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import load_profiles
        f = tmp_path / "cfg.yaml"
        f.write_text(textwrap.dedent("""\
            connections:
              - name: "Dev"
                adapter: oracle
                host: "dev:1521/DEV"
                user: "U1"
                schema: "S1"
                password_env: "PW1"
              - name: "Prod"
                adapter: oracle
                host: "prod:1521/PROD"
                user: "U2"
                schema: "S2"
                password_env: "PW2"
        """), encoding="utf-8")
        profiles = load_profiles(f)
        assert len(profiles) == 2
        assert profiles[0].name == "Dev"
        assert profiles[1].name == "Prod"


class TestResolveProfile:
    def _write_cfg(self, path: Path, name: str, password_env: str) -> None:
        path.write_text(textwrap.dedent(f"""\
            connections:
              - name: "{name}"
                adapter: oracle
                host: "host:1521/SVC"
                user: "USR"
                schema: "SCH"
                password_env: "{password_env}"
        """), encoding="utf-8")

    def test_resolves_profile_returns_db_config(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import resolve_profile
        from src.config.db_config import DbConfig
        monkeypatch.setenv("MY_PW", "hunter2")
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MY_PW")
        cfg = resolve_profile("Dev", f)
        assert isinstance(cfg, DbConfig)
        assert cfg.user == "USR"
        assert cfg.password == "hunter2"
        assert cfg.dsn == "host:1521/SVC"
        assert cfg.schema == "SCH"
        assert cfg.db_adapter == "oracle"

    def test_raises_key_error_for_unknown_profile(self, tmp_path: Path) -> None:
        from src.services.db_profiles_service import resolve_profile
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MY_PW")
        with pytest.raises(KeyError, match="Profile not found"):
            resolve_profile("Nonexistent", f)

    def test_raises_runtime_error_when_env_var_missing(self, tmp_path: Path, monkeypatch) -> None:
        from src.services.db_profiles_service import resolve_profile
        monkeypatch.delenv("MISSING_PW", raising=False)
        f = tmp_path / "cfg.yaml"
        self._write_cfg(f, "Dev", "MISSING_PW")
        with pytest.raises(RuntimeError, match="MISSING_PW"):
            resolve_profile("Dev", f)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/unit/test_db_profiles_service.py -v 2>&1 | head -30
```
Expected: `ImportError` or `ModuleNotFoundError` — service doesn't exist yet.

- [ ] **Step 3: Create `DbProfile` model**

```python
# src/api/models/db_profile.py
"""Pydantic model for a named database connection profile."""
from __future__ import annotations

import os

from pydantic import BaseModel, computed_field


class DbProfile(BaseModel):
    """A named database connection profile (no password).

    Attributes:
        name: Human-readable display name shown in the UI dropdown.
        adapter: Database adapter: ``"oracle"``, ``"postgresql"``, or
            ``"sqlite"``.
        host: Host/DSN string (e.g. ``"localhost:1521/FREEPDB1"``).
        user: Database username.
        schema: Schema qualifier for SQL table references.
        password_env: Name of the environment variable that holds the password.
        password_env_set: Computed — ``True`` when the env var is non-empty.
    """

    name: str
    adapter: str
    host: str
    user: str
    schema: str
    password_env: str

    @computed_field  # type: ignore[misc]
    @property
    def password_env_set(self) -> bool:
        """Return True when the password environment variable is non-empty."""
        return bool(os.environ.get(self.password_env))
```

- [ ] **Step 4: Create `db_profiles_service.py`**

```python
# src/services/db_profiles_service.py
"""Service for loading and resolving named database connection profiles.

Named profiles are defined in ``config/db_connections.yaml``.  Passwords are
**never** stored in the file — each profile names an environment variable
(``password_env``) that holds the secret at runtime.

The default config path is resolved relative to the project root using
:data:`_DEFAULT_CONFIG_PATH`.  All public functions accept an explicit
``path`` argument for testability.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from src.api.models.db_profile import DbProfile
from src.config.db_config import DbConfig

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "db_connections.yaml"


def load_profiles(path: Path | None = None) -> list[DbProfile]:
    """Load named DB profiles from a YAML config file.

    Returns an empty list — without raising — when the file is absent,
    empty, or malformed.  This keeps the API endpoint stable even when
    the config file has not been created yet.

    Args:
        path: Path to the YAML config file.  Defaults to
            ``config/db_connections.yaml`` relative to the project root.

    Returns:
        List of :class:`DbProfile` instances.  Empty list on any error.
    """
    if path is None:
        path = _DEFAULT_CONFIG_PATH

    if not path.exists():
        return []

    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        logger.warning("Failed to parse db_connections.yaml: %s", exc)
        return []

    if not isinstance(raw, dict):
        return []

    connections = raw.get("connections") or []
    profiles: list[DbProfile] = []
    for entry in connections:
        try:
            profiles.append(DbProfile(**entry))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skipping invalid db_connections entry %r: %s", entry, exc)
    return profiles


def resolve_profile(name: str, path: Path | None = None) -> DbConfig:
    """Resolve a named profile to a full :class:`DbConfig` with password.

    Looks up the profile by name, then reads the password from the
    environment variable named in ``profile.password_env``.

    Args:
        name: Profile name as it appears in ``db_connections.yaml``.
        path: Path to the YAML config file.  Defaults to
            ``config/db_connections.yaml`` relative to the project root.

    Returns:
        A fully populated :class:`DbConfig` ready for use.

    Raises:
        KeyError: If no profile with ``name`` exists in the config.
        RuntimeError: If the profile's ``password_env`` is not set in the
            environment.
    """
    profiles = load_profiles(path)
    profile = next((p for p in profiles if p.name == name), None)
    if profile is None:
        raise KeyError(f"Profile not found: {name!r}")

    password = os.environ.get(profile.password_env)
    if not password:
        raise RuntimeError(
            f"Password env var {profile.password_env!r} is not set on the server "
            f"(required by profile {name!r})"
        )

    return DbConfig(
        user=profile.user,
        password=password,
        dsn=profile.host,
        schema=profile.schema,
        db_adapter=profile.adapter,
    )
```

- [ ] **Step 5: Run tests — all should pass**

```bash
python3 -m pytest tests/unit/test_db_profiles_service.py -v
```
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/api/models/db_profile.py src/services/db_profiles_service.py tests/unit/test_db_profiles_service.py
git commit -m "feat(db-profiles): add DbProfile model and db_profiles_service"
```

---

## Task 2: `GET /api/v1/system/db-profiles` endpoint (TDD)

**Files:**
- Modify: `src/api/routers/system.py`
- Create: `tests/unit/test_api_db_profiles.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_api_db_profiles.py
"""Unit tests for GET /api/v1/system/db-profiles endpoint (TDD)."""
from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient


def _make_client():
    from src.api.main import app
    return TestClient(app)


class TestGetDbProfiles:
    def test_endpoint_returns_200(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.status_code == 200

    def test_no_auth_required(self) -> None:
        """Profiles list is non-sensitive — no API key needed."""
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.status_code == 200

    def test_returns_empty_list_when_no_profiles(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.json() == {"profiles": []}

    def test_returns_profile_fields(self, monkeypatch) -> None:
        from src.api.models.db_profile import DbProfile
        monkeypatch.setenv("ORACLE_PASSWORD", "pw")
        client = _make_client()
        profile = DbProfile(
            name="Local Dev",
            adapter="oracle",
            host="localhost:1521/FREEPDB1",
            user="APP_INT",
            schema="APP_INT",
            password_env="ORACLE_PASSWORD",
        )
        with patch("src.api.routers.system.load_profiles", return_value=[profile]):
            resp = client.get("/api/v1/system/db-profiles")
        data = resp.json()
        assert len(data["profiles"]) == 1
        p = data["profiles"][0]
        assert p["name"] == "Local Dev"
        assert p["adapter"] == "oracle"
        assert p["host"] == "localhost:1521/FREEPDB1"
        assert p["user"] == "APP_INT"
        assert p["schema"] == "APP_INT"
        assert p["password_env"] == "ORACLE_PASSWORD"
        assert p["password_env_set"] is True
        assert "password" not in p

    def test_password_env_set_false_when_var_missing(self, monkeypatch) -> None:
        from src.api.models.db_profile import DbProfile
        monkeypatch.delenv("DB_NO_PW", raising=False)
        client = _make_client()
        profile = DbProfile(
            name="Prod",
            adapter="oracle",
            host="prod:1521/PROD",
            user="APP_INT",
            schema="APP_INT",
            password_env="DB_NO_PW",
        )
        with patch("src.api.routers.system.load_profiles", return_value=[profile]):
            resp = client.get("/api/v1/system/db-profiles")
        p = resp.json()["profiles"][0]
        assert p["password_env_set"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/unit/test_api_db_profiles.py -v 2>&1 | head -20
```
Expected: `FAILED` — endpoint doesn't exist yet.

- [ ] **Step 3: Add endpoint to `system.py`**

Add these imports at the top of `src/api/routers/system.py`:
```python
from src.api.models.db_profile import DbProfile
from src.services.db_profiles_service import load_profiles
```

Add this endpoint after the existing `slo-alerts` route:
```python
@router.get("/db-profiles")
async def get_db_profiles():
    """Return the list of named database connection profiles.

    Profiles are loaded from ``config/db_connections.yaml``.  Returns an
    empty list when the file does not exist.  Passwords are never included
    in the response.

    Returns:
        Dict with ``profiles`` key containing a list of
        :class:`~src.api.models.db_profile.DbProfile` dicts.
    """
    profiles = load_profiles()
    return {"profiles": [p.model_dump() for p in profiles]}
```

- [ ] **Step 4: Run tests — all should pass**

```bash
python3 -m pytest tests/unit/test_api_db_profiles.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/api/routers/system.py tests/unit/test_api_db_profiles.py
git commit -m "feat(db-profiles): add GET /api/v1/system/db-profiles endpoint"
```

---

## Task 3: Update `db-ping` to accept `profile_name` (TDD)

**Files:**
- Modify: `src/api/routers/system.py`
- Modify: `tests/unit/test_api_db_profiles.py` (add new test class)

- [ ] **Step 1: Add failing tests**

Append this class to `tests/unit/test_api_db_profiles.py`:

```python
class TestDbPingWithProfile:
    def test_db_ping_accepts_profile_name(self) -> None:
        """profile_name form field must be accepted without 422."""
        client = _make_client()
        with patch("src.api.routers.system.resolve_profile") as mock_resolve, \
             patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_resolve.return_value = type("C", (), {
                "user": "U", "password": "P", "dsn": "H", "db_adapter": "oracle"
            })()
            mock_conn.return_value.connect.return_value = None
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Local Dev"},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 200

    def test_db_ping_profile_not_found_returns_error(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.resolve_profile", side_effect=KeyError("Profile not found: 'Bad'")):
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Bad"},
                headers={"X-API-Key": "test-key"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is False
        assert "Profile not found" in data["error"]

    def test_db_ping_missing_password_env_returns_error(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.resolve_profile",
                   side_effect=RuntimeError("DB_PROD_PASSWORD is not set")):
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Prod"},
                headers={"X-API-Key": "test-key"},
            )
        data = resp.json()
        assert data["ok"] is False
        assert "DB_PROD_PASSWORD" in data["error"]

    def test_db_ping_profile_success(self) -> None:
        client = _make_client()
        mock_cfg = type("C", (), {
            "user": "APP_INT", "password": "secret",
            "dsn": "localhost:1521/FREEPDB1", "db_adapter": "oracle"
        })()
        with patch("src.api.routers.system.resolve_profile", return_value=mock_cfg), \
             patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = None
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Local Dev"},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.json() == {"ok": True}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/unit/test_api_db_profiles.py::TestDbPingWithProfile -v 2>&1 | head -20
```
Expected: `FAILED` or `422` responses.

- [ ] **Step 3: Update `db_ping` in `system.py`**

Add `resolve_profile` to the imports already added in Task 2:
```python
from src.services.db_profiles_service import load_profiles, resolve_profile
```

Replace the existing `db_ping` function body with:
```python
@router.post("/db-ping")
async def db_ping(
    profile_name: str = Form(None),
    db_host: str = Form(None),
    db_user: str = Form(None),
    db_password: str = Form(None),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    _key=Depends(require_api_key),
):
    """Test a database connection using either a named profile or ad-hoc credentials.

    When ``profile_name`` is provided the connection parameters (including
    password) are resolved server-side from ``config/db_connections.yaml``
    and the matching environment variable.  Ad-hoc fields are ignored.

    When ``profile_name`` is absent, falls back to the supplied ``db_host``,
    ``db_user``, ``db_password`` fields (existing Custom behaviour).

    Args:
        profile_name: Named profile from ``config/db_connections.yaml``.
        db_host: Host/DSN (ad-hoc path only).
        db_user: Username (ad-hoc path only).
        db_password: Password (ad-hoc path only).
        db_schema: Schema (informational; not used for ping).
        db_adapter: Adapter (ad-hoc path only).
        _key: API key dependency.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": "..."}`` on
        failure.
    """
    if profile_name:
        try:
            cfg = resolve_profile(profile_name)
        except (KeyError, RuntimeError) as exc:
            return {"ok": False, "error": str(exc)}
        resolved_adapter = cfg.db_adapter
        resolved_host = cfg.dsn
        resolved_user = cfg.user
        resolved_password = cfg.password
    else:
        resolved_adapter = db_adapter
        resolved_host = db_host or ""
        resolved_user = db_user or ""
        resolved_password = db_password or ""

    if resolved_adapter != "oracle":
        return {
            "ok": False,
            "error": f"Connection test only supported for oracle adapter (got '{resolved_adapter}')",
        }
    try:
        conn = OracleConnection(
            username=resolved_user,
            password=resolved_password,
            dsn=resolved_host,
        )
        conn.connect()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: Run tests — all should pass**

```bash
python3 -m pytest tests/unit/test_api_db_profiles.py -v
```
Expected: all green.

- [ ] **Step 5: Commit**

```bash
git add src/api/routers/system.py tests/unit/test_api_db_profiles.py
git commit -m "feat(db-profiles): update db-ping to accept profile_name"
```

---

## Task 4: Update `db-compare` to accept `profile_name` (TDD)

**Files:**
- Modify: `src/api/routers/files.py`
- Modify: `tests/unit/test_api_db_compare.py` (add new test class)

- [ ] **Step 1: Add failing tests**

Append to `tests/unit/test_api_db_compare.py`:

```python
class TestDbCompareWithProfile:
    def test_accepts_profile_name_field(self, tmp_path: Path) -> None:
        """profile_name form field must be accepted without 422."""
        from src.api.main import app
        from unittest.mock import patch

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps(mapping_cfg))

        mock_result = {
            "workflow": {"status": "passed", "db_rows_extracted": 0, "query_or_table": "T"},
            "compare": {
                "structure_compatible": True, "total_rows_file1": 0, "total_rows_file2": 0,
                "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0, "differences": 0,
            },
        }

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.compare_db_to_file", return_value=mock_result), \
             patch("src.api.routers.files.resolve_profile") as mock_rp:
            from src.config.db_config import DbConfig
            mock_rp.return_value = DbConfig(
                user="U", password="P", dsn="H:1/S", schema="SCH", db_adapter="oracle"
            )
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Local Dev",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_profile_credentials_used_in_connection_override(self, tmp_path: Path) -> None:
        """When profile_name set, connection_override must use profile credentials."""
        from src.api.main import app
        from unittest.mock import patch, call
        import json as _json

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(_json.dumps(mapping_cfg))

        mock_result = {
            "workflow": {"status": "passed", "db_rows_extracted": 0, "query_or_table": "T"},
            "compare": {
                "structure_compatible": True, "total_rows_file1": 0, "total_rows_file2": 0,
                "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0, "differences": 0,
            },
        }
        from src.config.db_config import DbConfig
        profile_cfg = DbConfig(
            user="PROFUSER", password="PROFPW", dsn="profhost:1521/SVC",
            schema="PROFSCH", db_adapter="oracle"
        )

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.compare_db_to_file", return_value=mock_result) as mock_cmp, \
             patch("src.api.routers.files.resolve_profile", return_value=profile_cfg):
            client = TestClient(app)
            client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Local Dev",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )
        _, kwargs = mock_cmp.call_args
        override = kwargs.get("connection_override") or mock_cmp.call_args[0][5] if mock_cmp.call_args[0] else None
        override = mock_cmp.call_args.kwargs.get("connection_override")
        assert override["db_user"] == "PROFUSER"
        assert override["db_password"] == "PROFPW"
        assert override["db_host"] == "profhost:1521/SVC"
        assert override["db_adapter"] == "oracle"

    def test_profile_not_found_returns_500(self, tmp_path: Path) -> None:
        from src.api.main import app
        from unittest.mock import patch

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        (tmp_path / "m.json").write_text(json.dumps(mapping_cfg))

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.resolve_profile",
                   side_effect=KeyError("Profile not found: 'Bad'")):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Bad",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )
        assert resp.status_code == 500
        assert "Profile not found" in resp.json()["detail"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 -m pytest tests/unit/test_api_db_compare.py::TestDbCompareWithProfile -v 2>&1 | head -20
```
Expected: `FAILED` — `profile_name` not accepted yet.

- [ ] **Step 3: Update `db_compare` in `files.py`**

Add import near the top of `src/api/routers/files.py` (with other service imports):
```python
from src.services.db_profiles_service import resolve_profile
```

Add `profile_name` parameter to `db_compare` signature (after `db_adapter`):
```python
    db_adapter: str = Form(None),
    profile_name: str = Form(None),
    _: str = Depends(require_api_key),
```

Replace the `connection_override` block (lines ~630-642) with:
```python
    connection_override: dict | None = None
    if profile_name:
        try:
            prof_cfg = resolve_profile(profile_name)
        except (KeyError, RuntimeError) as exc:
            raise HTTPException(status_code=500, detail=str(exc))
        connection_override = {
            "db_host": prof_cfg.dsn,
            "db_user": prof_cfg.user,
            "db_password": prof_cfg.password,
            "db_schema": prof_cfg.schema,
            "db_adapter": prof_cfg.db_adapter,
        }
    elif db_host or db_user or db_password or db_adapter:
        connection_override = {
            k: v
            for k, v in {
                "db_host": db_host,
                "db_user": db_user,
                "db_password": db_password,
                "db_schema": db_schema,
                "db_adapter": db_adapter,
            }.items()
            if v is not None
        }
```

- [ ] **Step 4: Run tests — all should pass**

```bash
python3 -m pytest tests/unit/test_api_db_compare.py -v
```
Expected: all green.

- [ ] **Step 5: Run full unit suite**

```bash
python3 -m pytest tests/unit/ -q 2>&1 | tail -10
```
Expected: pass, ≥80% coverage.

- [ ] **Step 6: Commit**

```bash
git add src/api/routers/files.py tests/unit/test_api_db_compare.py
git commit -m "feat(db-profiles): update db-compare to accept profile_name"
```

---

## Task 5: Create sample config file

**Files:**
- Create: `config/db_connections.yaml`

- [ ] **Step 1: Create the file**

```yaml
# config/db_connections.yaml
#
# Named database connection profiles for the DB Compare tab.
# Each profile names an environment variable (password_env) that holds
# the password — the password value is NEVER stored in this file.
#
# The UI dropdown is populated from GET /api/v1/system/db-profiles.
# Add or remove entries here; restart the server to pick up changes.

connections:
  - name: "Local Dev"
    adapter: oracle
    host: "localhost:1521/FREEPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "ORACLE_PASSWORD"

  # Uncomment and configure additional environments as needed:
  # - name: "Staging"
  #   adapter: oracle
  #   host: "staging-db:1521/STAGPDB1"
  #   user: "APP_INT"
  #   schema: "APP_INT"
  #   password_env: "DB_STAGING_PASSWORD"
  #
  # - name: "Production"
  #   adapter: oracle
  #   host: "prod-db:1521/PRODPDB1"
  #   user: "APP_INT"
  #   schema: "APP_INT"
  #   password_env: "DB_PROD_PASSWORD"
```

- [ ] **Step 2: Commit**

```bash
git add config/db_connections.yaml
git commit -m "feat(db-profiles): add sample config/db_connections.yaml"
```

---

## Task 6: UI — profile dropdown in `ui.html`

**Files:**
- Modify: `src/reports/static/ui.html`

- [ ] **Step 1: Add profile dropdown row to connection form**

In `src/reports/static/ui.html`, locate the connection form (around line 760):
```html
          <!-- Connection form (expanded) -->
          <div class="dbc-conn-form" id="dbcConnForm" style="display:none">
            <div class="dbc-conn-grid">
              <div class="dbc-field">
                <label for="dbcAdapter">DB Adapter</label>
```

Insert the profile dropdown row as the **first** child of `dbc-conn-grid`, before `dbcAdapter`:
```html
              <div class="dbc-field dbc-field--full">
                <label for="dbcProfileSelect">DB Profile</label>
                <select id="dbcProfileSelect" aria-label="Select a named database profile">
                  <option value="">&#8212; select a profile &#8212;</option>
                  <option value="__custom__">Custom&#8230;</option>
                </select>
              </div>
              <div id="dbcManualFields">
```

And close `dbcManualFields` div just before the closing `</div>` of `dbc-conn-grid` (after the `dbcTestConnBtn` field):
```html
              </div><!-- /dbcManualFields -->
```

The final structure should be:
```html
          <div class="dbc-conn-form" id="dbcConnForm" style="display:none">
            <div class="dbc-conn-grid">
              <div class="dbc-field dbc-field--full">
                <label for="dbcProfileSelect">DB Profile</label>
                <select id="dbcProfileSelect" aria-label="Select a named database profile">
                  <option value="">&#8212; select a profile &#8212;</option>
                  <option value="__custom__">Custom&#8230;</option>
                </select>
              </div>
              <div id="dbcManualFields">
                <div class="dbc-field">
                  <label for="dbcAdapter">DB Adapter</label>
                  <select id="dbcAdapter">
                    <option value="oracle">oracle</option>
                    <option value="postgresql">postgresql</option>
                    <option value="sqlite">sqlite</option>
                  </select>
                </div>
                <div class="dbc-field">
                  <label for="dbcHost">Host / DSN</label>
                  <input type="text" id="dbcHost" placeholder="localhost:1521/FREEPDB1">
                </div>
                <div class="dbc-field">
                  <label for="dbcUser">Username</label>
                  <input type="text" id="dbcUser" autocomplete="username">
                </div>
                <div class="dbc-field">
                  <label for="dbcPassword">Password</label>
                  <input type="password" id="dbcPassword" autocomplete="current-password">
                </div>
                <div class="dbc-field">
                  <label for="dbcSchema">Schema</label>
                  <input type="text" id="dbcSchema">
                </div>
                <div class="dbc-field dbc-field-action">
                  <button class="btn btn-secondary" id="dbcTestConnBtn" type="button">&#x1F517; Test Connection</button>
                </div>
              </div><!-- /dbcManualFields -->
            </div>
            <div class="dbc-conn-result" id="dbcConnResult" style="display:none"></div>
          </div>
```

- [ ] **Step 2: Verify the HTML renders without breaking the page**

Start the server with `uvicorn src.api.main:app --reload` and open the DB Compare tab. The connection chip should still work; expanding it should show the profile dropdown on top, then the manual fields below.

- [ ] **Step 3: Commit**

```bash
git add src/reports/static/ui.html
git commit -m "feat(db-profiles): add profile dropdown row to DB Compare connection form"
```

---

## Task 7: UI — JS fetch, populate, toggle, updated run handler

**Files:**
- Modify: `src/reports/static/ui.js`

- [ ] **Step 1: Add profile fetching + dropdown population**

Locate the section comment `// DB Compare — connection chip expand/collapse + sessionStorage + db-ping` (around line 3318) in `ui.js`.

Add a new IIFE **before** the existing connection chip IIFE to handle profile fetching and dropdown toggle:

```javascript
// ===========================================================================
// DB Compare — profile dropdown (fetch from /api/v1/system/db-profiles)
// ===========================================================================
(function() {
  var _profiles = [];   // cache from last fetch

  /**
   * Fetch named profiles from the server and populate dbcProfileSelect.
   * Existing "— select a profile —" and "Custom…" options are preserved.
   * Called once when the DB Compare tab is first shown.
   */
  function _dbcLoadProfiles() {
    var sel = document.getElementById('dbcProfileSelect');
    if (!sel) return;
    var apiKeyEl = document.getElementById('apiKeyInput');
    var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {};
    fetch('/api/v1/system/db-profiles', { headers: hdrs })
      .then(function(r) { return r.json(); })
      .then(function(data) {
        _profiles = data.profiles || [];
        // Remove any previously injected profile options (keep blank + Custom)
        var toRemove = [];
        for (var i = 0; i < sel.options.length; i++) {
          var v = sel.options[i].value;
          if (v !== '' && v !== '__custom__') toRemove.push(sel.options[i]);
        }
        toRemove.forEach(function(o) { sel.removeChild(o); });

        // Insert named profiles before "Custom…"
        var customOpt = null;
        for (var j = 0; j < sel.options.length; j++) {
          if (sel.options[j].value === '__custom__') { customOpt = sel.options[j]; break; }
        }
        _profiles.forEach(function(p) {
          var opt = document.createElement('option');
          opt.value = p.name;
          opt.textContent = p.password_env_set ? p.name : (p.name + ' \u26A0\uFE0F');
          opt.dataset.passwordEnvSet = p.password_env_set ? '1' : '0';
          sel.insertBefore(opt, customOpt);
        });
      })
      .catch(function() { /* server not running or no config — dropdown shows only Custom */ });
  }

  /**
   * Show or hide the manual credential fields depending on the selected profile.
   * Named profile → hide manual fields. Custom / blank → show manual fields.
   */
  function _dbcApplyProfileSelection() {
    var sel      = document.getElementById('dbcProfileSelect');
    var manual   = document.getElementById('dbcManualFields');
    var result   = document.getElementById('dbcConnResult');
    if (!sel || !manual) return;

    var val = sel.value;
    var isNamed = val && val !== '__custom__';
    manual.style.display = isNamed ? 'none' : '';

    // Hide stale result banner on change
    if (result) result.style.display = 'none';

    _dbcRefreshChipFromProfile();
    _updateDbcRunBtn();
  }

  /**
   * Update the connection chip text.
   * Named profile → shows profile name. Custom/blank → shows host · schema.
   * Inlined to avoid cross-IIFE dependency on the private _dbcRefreshChip.
   */
  window._dbcRefreshChipFromProfile = function() {
    var sel      = document.getElementById('dbcProfileSelect');
    var chipText = document.getElementById('dbcConnChipText');
    if (!chipText) return;

    var val = sel ? sel.value : '';
    chipText.textContent = '';
    chipText.appendChild(document.createTextNode('\uD83D\uDD0C '));
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    if (val && val !== '__custom__') {
      hostSpan.textContent = val;
      chipText.appendChild(hostSpan);
    } else {
      var hostEl   = document.getElementById('dbcHost');
      var schemaEl = document.getElementById('dbcSchema');
      hostSpan.textContent = (hostEl && hostEl.value) ? hostEl.value : 'not configured';
      chipText.appendChild(hostSpan);
      var schema = schemaEl ? schemaEl.value : '';
      if (schema) {
        chipText.appendChild(document.createTextNode(' \u00B7 '));
        var schSpan = document.createElement('span');
        schSpan.textContent = schema;
        chipText.appendChild(schSpan);
      }
    }
  };

  /**
   * Return the active "host" for the run button enable check.
   * Named profile → profile name (truthy). Custom → dbcHost value.
   */
  window._dbcGetHost = function() {
    var sel = document.getElementById('dbcProfileSelect');
    if (sel && sel.value && sel.value !== '__custom__') return sel.value;
    return (document.getElementById('dbcHost') || {}).value || '';
  };

  // Wire up profile select change
  var profileSel = document.getElementById('dbcProfileSelect');
  if (profileSel) {
    profileSel.addEventListener('change', _dbcApplyProfileSelection);
  }

  // Load profiles when DB Compare tab is first activated
  var dbcTab = document.querySelector('[data-tab="db-compare"]') ||
               document.querySelector('.tab-btn[href="#db-compare"]');
  if (dbcTab) {
    dbcTab.addEventListener('click', function() {
      if (_profiles.length === 0) _dbcLoadProfiles();
    });
  }
  // Also load on page ready in case the tab is already active
  document.addEventListener('DOMContentLoaded', function() {
    var activeTab = document.querySelector('.tab-btn.active');
    if (activeTab && (activeTab.dataset.tab === 'db-compare' ||
        activeTab.getAttribute('href') === '#db-compare')) {
      _dbcLoadProfiles();
    }
  });

  // Expose loader for test hooks
  window._dbcLoadProfiles = _dbcLoadProfiles;
  window._dbcApplyProfileSelection = _dbcApplyProfileSelection;
})();
```

- [ ] **Step 2: Update Test Connection handler to send `profile_name`**

In the existing Test Connection handler (inside the connection chip IIFE, around line 3396), replace the `FormData` build:

```javascript
    testBtn.addEventListener('click', async function() {
      var btn    = this;
      var result = document.getElementById('dbcConnResult');
      btn.disabled = true;
      btn.textContent = '\u23F3 Testing\u2026';
      if (result) result.style.display = 'none';
      try {
        var fd = new FormData();
        var profileSel = document.getElementById('dbcProfileSelect');
        var profileVal = profileSel ? profileSel.value : '';
        if (profileVal && profileVal !== '__custom__') {
          // Named profile — check password_env_set first
          var selOpt = profileSel.options[profileSel.selectedIndex];
          if (selOpt && selOpt.dataset.passwordEnvSet === '0') {
            if (result) {
              result.style.display = '';
              result.className = 'dbc-conn-result err';
              result.textContent = '\u274C Password env var for this profile is not set on the server';
            }
            return;
          }
          fd.append('profile_name', profileVal);
        } else {
          fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
          fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
          fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
          fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
          fd.append('db_adapter',  (document.getElementById('dbcAdapter')  || {}).value || 'oracle');
        }
        var apiKeyEl = document.getElementById('apiKeyInput');
        var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {};
        var resp = await fetch('/api/v1/system/db-ping', { method: 'POST', body: fd, headers: hdrs });
        var data = await resp.json();
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result ' + (data.ok ? 'ok' : 'err');
          result.textContent = data.ok ? '\u2705 Connected' : '\u274C ' + (data.error || 'Connection failed');
        }
      } catch (err) {
        if (result) {
          result.style.display = '';
          result.className = 'dbc-conn-result err';
          result.textContent = '\u274C Request failed \u2014 check server is running';
        }
      } finally {
        btn.disabled = false;
        btn.textContent = '\uD83D\uDD17 Test Connection';
      }
    });
```

- [ ] **Step 3: Update run handler to send `profile_name`**

In the run button click handler (around line 3483), replace the `FormData` build block:

```javascript
      var fd = new FormData();
      fd.append('actual_file',      _dbcFile);
      fd.append('query_or_table',   document.getElementById('dbcSqlEditor').value.trim());
      fd.append('mapping_id',       document.getElementById('dbcMappingSelect').value);
      fd.append('key_columns',      (document.getElementById('dbcKeyColumns') || {}).value || '');
      fd.append('output_format',    'json');
      fd.append('apply_transforms', document.getElementById('dbcApplyTransforms').checked ? 'true' : 'false');
      var profileSel2 = document.getElementById('dbcProfileSelect');
      var profileVal2 = profileSel2 ? profileSel2.value : '';
      if (profileVal2 && profileVal2 !== '__custom__') {
        fd.append('profile_name', profileVal2);
      } else {
        fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
        fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
        fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
        fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
        fd.append('db_adapter',  (document.getElementById('dbcAdapter')  || {}).value || 'oracle');
      }
```

- [ ] **Step 4: Remove the old `window._dbcGetHost` definition**

The old definition (around line 3435) is now replaced by the one in the new IIFE from Step 1. Delete the old block:
```javascript
  // Expose host getter for run button enable check
  window._dbcGetHost = function() {
    return (document.getElementById('dbcHost') || {}).value || '';
  };
```

- [ ] **Step 5: Commit**

```bash
git add src/reports/static/ui.js
git commit -m "feat(db-profiles): add profile dropdown JS — fetch, toggle, run handler"
```

---

## Task 8: E2E tests

**Files:**
- Create: `tests/e2e/test_db_compare_profiles.py`

- [ ] **Step 1: Write E2E tests**

```python
# tests/e2e/test_db_compare_profiles.py
"""E2E Playwright tests for DB Compare profile dropdown feature."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("VALDO_BASE_URL", "http://localhost:8000")


def _go_to_db_compare(page: Page) -> None:
    """Navigate to the DB Compare tab."""
    page.goto(f"{BASE_URL}/ui")
    page.click('[data-tab="db-compare"], .tab-btn:has-text("DB Compare")')
    page.wait_for_selector("#dbcProfileSelect", timeout=5000)


class TestDbCompareProfileDropdown:
    def test_profile_select_exists(self, page: Page) -> None:
        """dbcProfileSelect element must exist in the DB Compare tab."""
        _go_to_db_compare(page)
        expect(page.locator("#dbcProfileSelect")).to_be_visible()

    def test_custom_option_always_present(self, page: Page) -> None:
        """'Custom…' option must always appear in the dropdown."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        options = page.locator("#dbcProfileSelect option")
        values = [options.nth(i).get_attribute("value") for i in range(options.count())]
        assert "__custom__" in values

    def test_manual_fields_hidden_when_profile_selected(self, page: Page) -> None:
        """Selecting a named profile must hide the manual credential fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        sel = page.locator("#dbcProfileSelect")
        # Only run if there are named profiles loaded
        options = page.locator("#dbcProfileSelect option")
        named = [
            options.nth(i).get_attribute("value")
            for i in range(options.count())
            if options.nth(i).get_attribute("value") not in ("", "__custom__")
        ]
        if not named:
            pytest.skip("No named profiles configured — skipping profile selection test")
        sel.select_option(named[0])
        expect(page.locator("#dbcManualFields")).to_be_hidden()

    def test_manual_fields_shown_when_custom_selected(self, page: Page) -> None:
        """Selecting 'Custom…' must show the manual credential fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        page.locator("#dbcProfileSelect").select_option("__custom__")
        expect(page.locator("#dbcManualFields")).to_be_visible()

    def test_manual_fields_shown_when_blank_selected(self, page: Page) -> None:
        """Selecting the blank placeholder must show the manual credential fields."""
        _go_to_db_compare(page)
        page.click("#dbcConnChip")
        sel = page.locator("#dbcProfileSelect")
        # First select custom to ensure fields are visible, then select blank
        sel.select_option("__custom__")
        sel.select_option("")
        expect(page.locator("#dbcManualFields")).to_be_visible()
```

- [ ] **Step 2: Run E2E tests (requires running server)**

```bash
python3 -m pytest tests/e2e/test_db_compare_profiles.py -v --browser chromium
```
Expected: all green (or skipped if no named profiles configured).

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_db_compare_profiles.py
git commit -m "test(db-profiles): add E2E tests for profile dropdown"
```

---

## Task 9: Docs update

**Files:**
- Modify: `docs/USAGE_AND_OPERATIONS_GUIDE.md`

- [ ] **Step 1: Add DB profiles section**

Find the "Database Configuration" section in `docs/USAGE_AND_OPERATIONS_GUIDE.md`. After the environment variables table, add:

```markdown
### Named DB Profiles (DB Compare Dropdown)

Create `config/db_connections.yaml` to populate the **DB Profile** dropdown in the DB Compare tab:

```yaml
connections:
  - name: "Local Dev"
    adapter: oracle
    host: "localhost:1521/FREEPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "ORACLE_PASSWORD"   # name of env var — never the password itself

  - name: "Production"
    adapter: oracle
    host: "prod-db:1521/PRODPDB1"
    user: "APP_INT"
    schema: "APP_INT"
    password_env: "DB_PROD_PASSWORD"
```

**Rules:**
- `password_env` is the **name** of an environment variable holding the password — it is never stored in the file.
- The file is optional. If absent, only "Custom…" appears in the dropdown.
- Profiles whose env var is not set show a ⚠️ in the dropdown and return an error on test/run.
- Restart the server to pick up changes to this file.
```

- [ ] **Step 2: Commit**

```bash
git add docs/USAGE_AND_OPERATIONS_GUIDE.md
git commit -m "docs(db-profiles): document config/db_connections.yaml in usage guide"
```

---

## Task 10: Final verification

- [ ] **Step 1: Run full unit suite with coverage**

```bash
python3 -m pytest tests/unit/ -q --cov=src --cov-report=term-missing 2>&1 | tail -15
```
Expected: pass, ≥80% coverage.

- [ ] **Step 2: Run E2E suite**

```bash
python3 -m pytest tests/e2e/ -v --browser chromium 2>&1 | tail -20
```
Expected: all pass.
