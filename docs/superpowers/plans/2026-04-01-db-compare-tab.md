# DB Compare Tab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fifth "DB Compare" tab to the Valdo Web UI that lets users compare Oracle DB data against an uploaded batch file (or vice versa) through a visual split-panel interface.

**Architecture:** Backend gets two additions — `compare_db_to_file()` gains an optional `connection_override` dict (Oracle path only), and a new `POST /api/v1/system/db-ping` endpoint for testing DB credentials. The frontend is pure HTML + CSS + JS split across the three existing static files (`ui.html`, `ui.css`, `ui.js`); no new files are created. Direction state, connection form, run handler, results, and CSV generation all live in `ui.js`.

**Tech Stack:** FastAPI (Form params), oracledb thin mode, vanilla JS (no framework), existing CSS vars for theme.

---

## File Map

| File | What changes |
|------|-------------|
| `src/services/db_file_compare_service.py` | Add `connection_override` param to `compare_db_to_file()` |
| `src/api/routers/files.py` | Extend `POST /db-compare` with connection override + `apply_transforms` Form params |
| `src/api/routers/system.py` | Add `POST /db-ping` endpoint |
| `src/reports/static/ui.html` | Add 5th tab button + `#panel-dbcompare` full HTML |
| `src/reports/static/ui.css` | Add `.dbc-*` CSS classes |
| `src/reports/static/ui.js` | Add `'dbcompare'` to `switchTab()` array + all DB Compare JS |
| `tests/unit/test_api_db_compare_ui.py` | New: unit tests for extended endpoint + db-ping |
| `tests/e2e/test_e2e_db_compare.py` | New: Playwright E2E tests for the tab |
| `docs/USAGE_AND_OPERATIONS_GUIDE.md` | Document DB Compare tab |

---

## Task 1: Service layer — connection_override for Oracle (Issue #280)

**Files:**
- Modify: `src/services/db_file_compare_service.py:101-108`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_db_compare_connection_override.py`:

```python
"""Tests for compare_db_to_file() connection_override parameter."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


def test_uses_env_connection_when_no_override():
    """Without override, OracleConnection.from_env() is called."""
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.from_env.assert_called_once()


def test_uses_override_connection_when_provided():
    """With oracle override, OracleConnection is built from override values."""
    override = {
        "db_host": "myhost:1521/FREE",
        "db_user": "myuser",
        "db_password": "secret",
        "db_schema": "MYSCHEMA",
        "db_adapter": "oracle",
    }
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_conn.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
                connection_override=override,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.assert_called_once_with(
            username="myuser",
            password="secret",
            dsn="myhost:1521/FREE",
        )
        mock_conn.from_env.assert_not_called()


def test_non_oracle_override_falls_back_to_env():
    """Non-oracle adapter in override still uses from_env()."""
    override = {
        "db_host": "myhost",
        "db_user": "u",
        "db_password": "p",
        "db_adapter": "postgresql",
    }
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
                connection_override=override,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.from_env.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
cd /Users/buddy/claude-code/automations/valdo
python3 -m pytest tests/unit/test_db_compare_connection_override.py -v
```

Expected: FAIL — `compare_db_to_file() got an unexpected keyword argument 'connection_override'`

- [ ] **Step 3: Implement `connection_override` in service**

In `src/services/db_file_compare_service.py`, update the `compare_db_to_file` signature (line 101):

```python
def compare_db_to_file(
    query_or_table: str,
    mapping_config: dict[str, Any],
    actual_file: str,
    output_format: str = "json",
    key_columns: list[str] | str | None = None,
    apply_transforms: bool = False,
    connection_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
```

Add to the docstring Args section:

```
        connection_override: Optional dict with keys ``db_host``, ``db_user``,
            ``db_password``, ``db_schema``, ``db_adapter``.  When provided and
            ``db_adapter`` is ``"oracle"``, builds a direct Oracle connection
            from these values instead of reading from env vars.  Non-Oracle
            adapters fall back to :meth:`~OracleConnection.from_env`.
```

Replace the `# --- DB Extraction ---` block (lines 166–168) with:

```python
    # --- DB Extraction -------------------------------------------------------
    _adapter = (connection_override or {}).get("db_adapter", "oracle")
    if connection_override and _adapter == "oracle":
        connection = OracleConnection(
            username=connection_override["db_user"],
            password=connection_override["db_password"],
            dsn=connection_override["db_host"],
        )
    else:
        connection = OracleConnection.from_env()
    extractor = DataExtractor(connection)
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python3 -m pytest tests/unit/test_db_compare_connection_override.py -v
```

Expected: 3 tests PASS

- [ ] **Step 5: Run full unit suite to confirm no regressions**

```bash
python3 -m pytest tests/unit/ -q
```

Expected: all pass, coverage >=80%

- [ ] **Step 6: Commit**

```bash
git add src/services/db_file_compare_service.py tests/unit/test_db_compare_connection_override.py
git commit -m "feat(service): add connection_override param to compare_db_to_file() (#280)"
```

---

## Task 2: API — Extend POST /db-compare endpoint (Issue #281)

**Files:**
- Modify: `src/api/routers/files.py:562-643`
- Create: `tests/unit/test_api_db_compare_ui.py`

- [ ] **Step 1: Write failing tests**

Create `tests/unit/test_api_db_compare_ui.py`:

```python
"""Unit tests for extended POST /api/v1/files/db-compare endpoint (UI connection override)."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-API-Key": "test-key"}

_MOCK_RESULT = {
    "workflow": {
        "status": "passed",
        "db_rows_extracted": 1,
        "query_or_table": "SELECT 1 FROM DUAL",
    },
    "compare": {
        "structure_compatible": True,
        "total_rows_file1": 1,
        "total_rows_file2": 1,
        "matching_rows": 1,
        "only_in_file1": 0,
        "only_in_file2": 0,
        "differences": 0,
    },
}


def _make_app():
    from src.api.main import app
    return app


class TestDbCompareExtendedParams:
    """Tests for connection override + apply_transforms params."""

    def test_invalid_db_adapter_returns_400(self, tmp_path: Path) -> None:
        """db_adapter must be oracle|postgresql|sqlite; anything else returns 400."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT),
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "db_adapter": "mysql",  # invalid
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 400
        assert "Invalid db_adapter" in resp.json()["detail"]

    def test_valid_connection_override_forwarded_to_service(self, tmp_path: Path) -> None:
        """Connection override fields must be forwarded to compare_db_to_file()."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "db_host": "myhost:1521/FREE",
                    "db_user": "myuser",
                    "db_password": "secret",
                    "db_schema": "SCH",
                    "db_adapter": "oracle",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        call_kwargs = mock_svc.call_args.kwargs
        override = call_kwargs.get("connection_override")
        assert override is not None
        assert override["db_host"] == "myhost:1521/FREE"
        assert override["db_user"] == "myuser"
        assert override["db_password"] == "secret"

    def test_apply_transforms_forwarded_to_service(self, tmp_path: Path) -> None:
        """apply_transforms=True must be forwarded to service."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "apply_transforms": "true",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        assert mock_svc.call_args.kwargs.get("apply_transforms") is True

    def test_no_override_fields_still_works(self, tmp_path: Path) -> None:
        """Existing callers without override fields still get 200."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={"query_or_table": "SELECT 1 FROM DUAL", "mapping_id": "m"},
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        assert mock_svc.call_args.kwargs.get("connection_override") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/unit/test_api_db_compare_ui.py::TestDbCompareExtendedParams -v
```

Expected: FAIL (missing Form params)

- [ ] **Step 3: Extend the db-compare endpoint**

In `src/api/routers/files.py`, replace the `db_compare` function signature (currently lines 562–568) and update the function body:

```python
@router.post("/db-compare", response_model=DbCompareResult)
async def db_compare(
    actual_file: UploadFile = File(...),
    query_or_table: str = Form(...),
    mapping_id: str = Form(...),
    key_columns: str = Form(""),
    output_format: str = Form("json"),
    apply_transforms: bool = Form(False),
    db_host: str = Form(None),
    db_user: str = Form(None),
    db_password: str = Form(None),
    db_schema: str = Form(None),
    db_adapter: str = Form(None),
    _: str = Depends(require_api_key),
):
    """Extract data from Oracle and compare against an uploaded actual batch file.

    Args:
        actual_file: The actual batch file to compare against.
        query_or_table: SQL SELECT statement or bare Oracle table name.
        mapping_id: ID of the JSON mapping config (must exist in MAPPINGS_DIR).
        key_columns: Comma-separated key column names for row matching.
        output_format: Desired output format (``"json"`` or ``"html"``).
        apply_transforms: When True, apply mapping field transforms to DB rows
            before comparison. Defaults to False.
        db_host: Optional DB host/DSN override (falls back to env vars).
        db_user: Optional DB username override.
        db_password: Optional DB password override. Never stored server-side.
        db_schema: Optional schema override.
        db_adapter: Optional adapter override (``oracle``, ``postgresql``,
            ``sqlite``). Returns 400 if provided but not in the allowed set.
        _: API key dependency.

    Returns:
        DbCompareResult with workflow status, row counts, and diff statistics.

    Raises:
        HTTPException: 400 if db_adapter is invalid.
        HTTPException: 404 if the mapping is not found.
        HTTPException: 500 if DB extraction or comparison fails.
    """
    _ALLOWED_ADAPTERS = {"oracle", "postgresql", "sqlite"}
    if db_adapter is not None and db_adapter not in _ALLOWED_ADAPTERS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid db_adapter '{db_adapter}'. Must be one of: {', '.join(sorted(_ALLOWED_ADAPTERS))}",
        )
```

Then inside the function body, after the mapping load, add before the `try:` block:

```python
    # Build connection override dict when any override field is provided
    connection_override = None
    if db_host or db_user or db_password:
        connection_override = {
            "db_host": db_host or "",
            "db_user": db_user or "",
            "db_password": db_password or "",
            "db_schema": db_schema or "",
            "db_adapter": db_adapter or "oracle",
        }
```

In the `compare_db_to_file(...)` call (line ~603), add the two new kwargs:

```python
        result = compare_db_to_file(
            query_or_table=query_or_table,
            mapping_config=mapping_config,
            actual_file=str(upload_path),
            output_format=output_format,
            key_columns=key_columns_list or None,
            apply_transforms=apply_transforms,
            connection_override=connection_override,
        )
```

Keep the rest of the function (DbCompareResult construction + exception handling) unchanged.

- [ ] **Step 4: Run tests — expect PASS**

```bash
python3 -m pytest tests/unit/test_api_db_compare_ui.py::TestDbCompareExtendedParams -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/unit/ -q
```

- [ ] **Step 6: Commit**

```bash
git add src/api/routers/files.py tests/unit/test_api_db_compare_ui.py
git commit -m "feat(api): extend POST /db-compare with connection override + apply_transforms (#281)"
```

---

## Task 3: API — POST /api/v1/system/db-ping (Issue #282)

**Files:**
- Modify: `src/api/routers/system.py`
- Modify: `tests/unit/test_api_db_compare_ui.py` (add class)

- [ ] **Step 1: Write failing tests**

Append to `tests/unit/test_api_db_compare_ui.py`:

```python
class TestDbPingEndpoint:
    """Tests for POST /api/v1/system/db-ping."""

    def test_endpoint_requires_api_key(self) -> None:
        """Endpoint returns 401 without API key."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/system/db-ping",
            data={"db_host": "h", "db_user": "u", "db_password": "p"},
        )
        assert resp.status_code == 401

    def test_returns_ok_false_on_bad_credentials(self) -> None:
        """Bad credentials returns ok=false with error message."""
        client = TestClient(_make_app())
        with patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.side_effect = Exception("invalid credentials")
            resp = client.post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"db_host": "bad:1521/FREE", "db_user": "u", "db_password": "bad"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body

    def test_returns_ok_true_on_success(self) -> None:
        """Successful connection returns ok=true."""
        client = TestClient(_make_app())
        with patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = MagicMock()
            resp = client.post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"db_host": "h:1521/F", "db_user": "u", "db_password": "p"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_non_oracle_adapter_returns_not_implemented(self) -> None:
        """Non-oracle adapter returns ok=false with descriptive message."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/system/db-ping",
            headers=AUTH,
            data={"db_host": "h", "db_user": "u", "db_password": "p", "db_adapter": "postgresql"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "oracle" in body["error"].lower()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
python3 -m pytest tests/unit/test_api_db_compare_ui.py::TestDbPingEndpoint -v
```

Expected: FAIL (404 — endpoint not found)

- [ ] **Step 3: Add db-ping endpoint to system router**

In `src/api/routers/system.py`, add the following imports at the top:

```python
from fastapi import Form
from src.database.connection import OracleConnection
```

Then add at the bottom of the file:

```python
@router.post("/db-ping")
async def db_ping(
    db_host: str = Form(...),
    db_user: str = Form(...),
    db_password: str = Form(...),
    db_schema: str = Form(""),
    db_adapter: str = Form("oracle"),
    _key=Depends(require_api_key),
):
    """Test a database connection with the provided credentials.

    Oracle-only in the initial scope.  Non-Oracle adapters return
    ``{"ok": false, "error": "..."}`` without attempting a connection.

    Args:
        db_host: Host/DSN string (e.g. ``localhost:1521/FREEPDB1``).
        db_user: Database username.
        db_password: Database password.
        db_schema: Schema name (informational; not used for ping).
        db_adapter: Database adapter (``oracle``, ``postgresql``, ``sqlite``).
            Only ``oracle`` is supported; others return an error.
        _key: API key dependency.

    Returns:
        ``{"ok": True}`` on success, or ``{"ok": False, "error": "<message>"}``
        on failure.
    """
    if db_adapter != "oracle":
        return {
            "ok": False,
            "error": f"Connection test only supported for oracle adapter (got '{db_adapter}')",
        }
    try:
        conn = OracleConnection(username=db_user, password=db_password, dsn=db_host)
        conn.connect()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
```

- [ ] **Step 4: Run tests — expect PASS**

```bash
python3 -m pytest tests/unit/test_api_db_compare_ui.py::TestDbPingEndpoint -v
```

Expected: 4 tests PASS

- [ ] **Step 5: Run full suite**

```bash
python3 -m pytest tests/unit/ -q
```

- [ ] **Step 6: Commit**

```bash
git add src/api/routers/system.py tests/unit/test_api_db_compare_ui.py
git commit -m "feat(api): add POST /api/v1/system/db-ping endpoint (#282)"
```

---

## Task 4: HTML — 5th tab + full panel structure (Issue #283)

**Files:**
- Modify: `src/reports/static/ui.html:93-94` (tab bar) and before line 723 `</main>`

- [ ] **Step 1: Add DB Compare tab button to tab bar**

In `src/reports/static/ui.html`, after line 94 (the API Tester `<button>` closing tag), insert:

```html
    <button id="tab-dbcompare" role="tab" aria-selected="false"
            aria-controls="panel-dbcompare" onclick="switchTab('dbcompare')">DB Compare</button>
```

- [ ] **Step 2: Add the full panel HTML before `</main>`**

In `src/reports/static/ui.html`, insert before the `</main>` tag (line 723):

```html
  <!-- DB COMPARE PANEL -->
  <div id="panel-dbcompare" class="panel panel-hidden" role="tabpanel" aria-labelledby="tab-dbcompare">
    <h2>DB Compare</h2>

    <!-- Direction Bar -->
    <div class="dbc-direction-bar">
      <span class="dbc-side-label dbc-side-db" id="dbcDbLabel">&#x1F5C4;&#xFE0F; Database</span>
      <button class="btn btn-secondary dbc-swap-btn" id="dbcSwapBtn"
              aria-label="Swap comparison direction">&#x21C4; swap</button>
      <span class="dbc-side-label dbc-side-file" id="dbcFileLabel">&#x1F4C4; File</span>
      <span class="dbc-direction-label" id="dbcDirectionLabel">DB is source &middot; File is actual</span>
    </div>

    <!-- Split Panel -->
    <div class="dbc-split">

      <!-- DB Panel (left) -->
      <div class="dbc-panel dbc-panel--source" id="dbcDbPanel">
        <div class="dbc-panel-header" id="dbcDbPanelHeader">DATABASE SOURCE</div>
        <div class="dbc-panel-body">

          <!-- Connection chip (collapsed) -->
          <div class="dbc-conn-chip" id="dbcConnChip" tabindex="0"
               role="button" aria-expanded="false" aria-controls="dbcConnForm"
               aria-label="DB connection settings">
            <span id="dbcConnChipText">&#x1F50C; <span class="dbc-chip-host">not configured</span></span>
            <span class="dbc-chip-edit">&#x25B8; edit</span>
          </div>

          <!-- HTTPS warning (shown when not on HTTPS) -->
          <div class="dbc-https-warning" id="dbcHttpsWarning" style="display:none">
            &#x26A0;&#xFE0F; Connection credentials will be sent over an unencrypted connection.
          </div>

          <!-- Connection form (expanded) -->
          <div class="dbc-conn-form" id="dbcConnForm" style="display:none">
            <div class="dbc-conn-grid">
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
            </div>
            <div class="dbc-conn-result" id="dbcConnResult" style="display:none"></div>
          </div>

          <!-- SQL Editor -->
          <div class="dbc-field-label">SQL QUERY</div>
          <textarea id="dbcSqlEditor" class="dbc-sql-editor"
                    placeholder="SELECT column1, column2 FROM SCHEMA.TABLE"
                    aria-label="SQL query for DB extraction"></textarea>

          <!-- Key columns -->
          <div class="dbc-field-label">
            <label for="dbcKeyColumns">KEY COLUMNS <span class="dbc-muted">(for row matching)</span></label>
          </div>
          <input type="text" id="dbcKeyColumns" class="dbc-key-input"
                 placeholder="COL1, COL2"
                 aria-label="Key columns for row matching (comma-separated)">

        </div>
      </div>

      <!-- File Panel (right) -->
      <div class="dbc-panel dbc-panel--actual" id="dbcFilePanel">
        <div class="dbc-panel-header dbc-panel-header--actual" id="dbcFilePanelHeader">FILE (ACTUAL)</div>
        <div class="dbc-panel-body">

          <!-- Drop zone -->
          <div class="drop-zone dbc-drop-zone" id="dbcDropZone" tabindex="0"
               role="button" aria-label="Click or drag a file for DB comparison">
            <svg class="dz-icon" aria-hidden="true" focusable="false" width="32" height="32"
                 fill="none" stroke="currentColor" stroke-width="1.4" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round"
                d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5m-13.5-9L12 3m0 0l4.5 4.5M12 3v13.5"/>
            </svg>
            <span class="dz-label">Drag &amp; drop or click to browse</span>
            <span class="dz-sub">Fixed-width, CSV, TSV, pipe-delimited</span>
          </div>
          <input type="file" id="dbcFileInput" style="display:none"
                 accept=".txt,.csv,.tsv,.dat,.pipe"
                 aria-label="Batch file for DB comparison">

          <!-- Mapping select -->
          <div class="dbc-field-label">
            <label for="dbcMappingSelect">MAPPING</label>
          </div>
          <select id="dbcMappingSelect" aria-label="Mapping for DB comparison">
            <option value="">&#x2014; select mapping &#x2014;</option>
          </select>

          <!-- Key columns info note -->
          <div class="dbc-info-note">
            &#x2139;&#xFE0F; Key columns are shared &mdash; set in the DB panel
          </div>

          <!-- Options -->
          <div class="dbc-field-label">OPTIONS</div>
          <div class="dbc-options">
            <label class="dbc-option-row">
              <input type="checkbox" id="dbcApplyTransforms" checked>
              Apply transforms from mapping
            </label>
            <label class="dbc-option-row">
              <input type="checkbox" id="dbcDownloadCsv" checked>
              Download diff as CSV
            </label>
          </div>

        </div>
      </div>

    </div><!-- /.dbc-split -->

    <!-- Run button -->
    <div class="dbc-run-row">
      <button class="btn btn-primary btn-lg" id="dbcRunBtn" disabled
              data-tooltip="Run DB to file comparison">&#x25B6; Run DB Compare</button>
    </div>

    <!-- Results -->
    <div id="dbcResults" aria-live="polite" style="display:none">
      <div id="dbcStatusBanner" class="dbc-status-banner"></div>
      <div class="dbc-metrics" id="dbcMetrics"></div>
      <div class="dbc-download-row" id="dbcDownloadRow" style="display:none">
        <button class="btn btn-secondary" id="dbcDownloadDiffBtn" type="button">
          &#x2B07; Download Diff CSV
        </button>
      </div>
    </div>

  </div><!-- /#panel-dbcompare -->
```

- [ ] **Step 3: Verify HTML parses cleanly**

```bash
python3 -c "
from html.parser import HTMLParser
class V(HTMLParser): pass
V().feed(open('src/reports/static/ui.html').read())
print('HTML parsed OK')
"
```

Expected: `HTML parsed OK`

- [ ] **Step 4: Commit**

```bash
git add src/reports/static/ui.html
git commit -m "feat(ui): add DB Compare tab HTML structure (#283)"
```

---

## Task 5: CSS — DB Compare styles (Issue #284)

**Files:**
- Modify: `src/reports/static/ui.css` (append at end)

- [ ] **Step 1: Append CSS to `src/reports/static/ui.css`**

```css
/* ── DB Compare Tab ──────────────────────────────────────────────────────── */

.dbc-direction-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin-bottom: 14px;
  flex-wrap: wrap;
}
.dbc-side-label { font-weight: 600; font-size: 14px; }
.dbc-side-db    { color: var(--accent); }
.dbc-side-file  { color: var(--text-secondary); }
.dbc-swap-btn   { border-radius: 20px; padding: 4px 14px; font-size: 13px; }
.dbc-direction-label {
  font-size: 11px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 3px 8px;
  opacity: 0.8;
}

.dbc-split {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 14px;
}
@media (max-width: 700px) { .dbc-split { grid-template-columns: 1fr; } }

.dbc-panel { border-radius: var(--radius); border: 1.5px solid var(--border); overflow: hidden; }
.dbc-panel--source { border-color: var(--accent); }
.dbc-panel--actual { border-color: var(--border); }

.dbc-panel-header {
  background: var(--accent);
  color: #fff;
  padding: 6px 12px;
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
}
.dbc-panel-header--actual { background: var(--bg-tertiary); color: var(--text-secondary); }
.dbc-panel-body { padding: 10px 12px; }

.dbc-conn-chip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 6px 10px;
  margin-bottom: 8px;
  cursor: pointer;
  font-size: 13px;
}
.dbc-conn-chip:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.dbc-chip-edit { font-size: 11px; opacity: 0.5; }

.dbc-https-warning {
  font-size: 12px;
  color: var(--partial);
  background: rgba(243, 156, 18, 0.1);
  border: 1px solid var(--partial);
  border-radius: 4px;
  padding: 5px 8px;
  margin-bottom: 8px;
}

.dbc-conn-form {
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 10px;
  margin-bottom: 8px;
}
.dbc-conn-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
.dbc-field { display: flex; flex-direction: column; gap: 2px; }
.dbc-field label { font-size: 11px; opacity: 0.6; font-weight: 600; }
.dbc-field input, .dbc-field select {
  font-size: 13px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 6px;
  color: var(--text);
}
.dbc-field-action { justify-content: flex-end; }
.dbc-field-action button { width: 100%; }
.dbc-conn-result { margin-top: 6px; font-size: 12px; padding: 4px 6px; border-radius: 4px; }
.dbc-conn-result.ok  { color: var(--pass); }
.dbc-conn-result.err { color: var(--fail); }

.dbc-sql-editor {
  width: 100%;
  min-height: 120px;
  resize: vertical;
  font-family: var(--font-mono);
  font-size: 12px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 6px 8px;
  color: var(--text);
  box-sizing: border-box;
  margin-bottom: 8px;
}

.dbc-field-label {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.06em;
  opacity: 0.6;
  margin-bottom: 4px;
}
.dbc-muted { opacity: 0.5; font-weight: 400; }
.dbc-key-input {
  width: 100%;
  font-size: 13px;
  background: var(--bg-secondary);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 4px 8px;
  color: var(--text);
  box-sizing: border-box;
}

.dbc-drop-zone { margin-bottom: 10px; }
.dbc-info-note {
  font-size: 12px;
  background: var(--bg-secondary);
  border-radius: 4px;
  padding: 5px 8px;
  opacity: 0.75;
  margin: 8px 0;
}
.dbc-options { display: flex; flex-direction: column; gap: 4px; }
.dbc-option-row { display: flex; align-items: center; gap: 6px; font-size: 13px; cursor: pointer; }

.dbc-run-row { text-align: center; margin-bottom: 16px; }

.dbc-status-banner {
  padding: 8px 14px;
  border-radius: var(--radius);
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 10px;
}
.dbc-status-banner.pass { background: rgba(34, 197, 94, 0.12); color: var(--pass); border: 1px solid var(--pass); }
.dbc-status-banner.fail { background: rgba(239, 68, 68, 0.10); color: var(--fail); border: 1px solid var(--fail); }
.dbc-status-banner.warn { background: rgba(243, 156, 18, 0.10); color: var(--partial); border: 1px solid var(--partial); }

.dbc-metrics {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 8px;
  margin-bottom: 10px;
}
@media (max-width: 700px) { .dbc-metrics { grid-template-columns: repeat(3, 1fr); } }
.dbc-metric-card { background: var(--bg-secondary); border-radius: var(--radius); padding: 8px; text-align: center; }
.dbc-metric-value { font-size: 20px; font-weight: 700; color: var(--accent); }
.dbc-metric-value.green { color: var(--pass); }
.dbc-metric-value.amber { color: var(--partial); }
.dbc-metric-value.red   { color: var(--fail); }
.dbc-metric-label { font-size: 10px; opacity: 0.6; margin-top: 2px; }

.dbc-download-row { text-align: center; }
```

- [ ] **Step 2: Check line count**

```bash
wc -l src/reports/static/ui.css
```

Expected: under 2100 lines

- [ ] **Step 3: Commit**

```bash
git add src/reports/static/ui.css
git commit -m "feat(ui): add DB Compare CSS — panels, chip, metric cards, direction variants (#284)"
```

---

## Task 6: JS — switchTab wiring + direction swap (Issue #285)

**Files:**
- Modify: `src/reports/static/ui.js`

- [ ] **Step 1: Add `'dbcompare'` to the switchTab array**

In `src/reports/static/ui.js`, find this line (currently line 23):

```javascript
  ['quick', 'runs', 'mapping', 'tester'].forEach(function(t) {
```

Replace with:

```javascript
  ['quick', 'runs', 'mapping', 'tester', 'dbcompare'].forEach(function(t) {
```

- [ ] **Step 2: Add direction state + swap handler**

Append after the existing `switchTab` function's closing brace (search for the end of the `switchTab` function, roughly around line 60):

```javascript
// ===========================================================================
// DB Compare — direction state + swap
// ===========================================================================
var _dbcDirection = 'db-to-file';

function _dbcUpdateDirection() {
  var isDbToFile = _dbcDirection === 'db-to-file';
  var lbl = document.getElementById('dbcDirectionLabel');
  if (lbl) lbl.textContent = isDbToFile ? 'DB is source \u00B7 File is actual' : 'File is source \u00B7 DB is actual';

  var dbPanel    = document.getElementById('dbcDbPanel');
  var filePanel  = document.getElementById('dbcFilePanel');
  var dbHeader   = document.getElementById('dbcDbPanelHeader');
  var fileHeader = document.getElementById('dbcFilePanelHeader');

  if (dbPanel)   dbPanel.className   = 'dbc-panel ' + (isDbToFile ? 'dbc-panel--source' : 'dbc-panel--actual');
  if (filePanel) filePanel.className = 'dbc-panel ' + (isDbToFile ? 'dbc-panel--actual' : 'dbc-panel--source');
  if (dbHeader)   dbHeader.className   = 'dbc-panel-header' + (isDbToFile ? '' : ' dbc-panel-header--actual');
  if (fileHeader) fileHeader.className = 'dbc-panel-header' + (isDbToFile ? ' dbc-panel-header--actual' : '');

  var sqlEd = document.getElementById('dbcSqlEditor');
  if (sqlEd) {
    sqlEd.placeholder = isDbToFile
      ? 'SELECT column1, column2 FROM SCHEMA.TABLE'
      : 'SELECT t1.col, t2.col FROM TARGET.TABLE1 t1 JOIN TARGET.TABLE2 t2 ON t1.id = t2.fk_id';
  }
}

document.getElementById('dbcSwapBtn').addEventListener('click', function() {
  _dbcDirection = (_dbcDirection === 'db-to-file') ? 'file-to-db' : 'db-to-file';
  _dbcUpdateDirection();
});
```

- [ ] **Step 3: Populate DB Compare mapping select alongside Quick Test**

Find the existing `loadMappings()` function (search for `mappingSelect` in the function that fetches `/api/v1/mappings`). After the loop that appends options to `#mappingSelect`, add:

```javascript
  // Also populate DB Compare mapping select
  var dbcSel = document.getElementById('dbcMappingSelect');
  if (dbcSel) {
    dbcSel.innerHTML = '<option value="">\u2014 select mapping \u2014</option>';
    data.mappings.forEach(function(m) {
      var opt = document.createElement('option');
      opt.value = m.id || m;
      opt.textContent = m.name || m.id || m;
      dbcSel.appendChild(opt);
    });
  }
```

- [ ] **Step 4: Smoke-test in browser**

Start server: `python3 -m uvicorn src.api.main:app --reload --port 8000`
Navigate to `http://localhost:8000/ui`.
Verify: "DB Compare" tab appears, clicking shows panel, swap button works, panel borders change.

- [ ] **Step 5: Commit**

```bash
git add src/reports/static/ui.js
git commit -m "feat(ui): wire DB Compare tab into switchTab + direction swap + mapping select (#285)"
```

---

## Task 7: JS — Connection chip + Test Connection (Issue #286)

**Files:**
- Modify: `src/reports/static/ui.js`

- [ ] **Step 1: Add connection chip JS**

Append to `src/reports/static/ui.js`:

```javascript
// ===========================================================================
// DB Compare — connection chip expand/collapse + sessionStorage + db-ping
// ===========================================================================
(function() {
  var _SS_KEYS = ['dbcHost', 'dbcUser', 'dbcSchema', 'dbcAdapter'];

  function _dbcRestoreSession() {
    _SS_KEYS.forEach(function(id) {
      var el  = document.getElementById(id);
      var val = sessionStorage.getItem('valdo-dbc-' + id);
      if (el && val) el.value = val;
    });
    _dbcRefreshChip();
  }

  function _dbcSaveSession() {
    _SS_KEYS.forEach(function(id) {
      var el = document.getElementById(id);
      if (el) sessionStorage.setItem('valdo-dbc-' + id, el.value);
    });
    // Password (dbcPassword) is intentionally excluded — never persisted
  }

  function _dbcRefreshChip() {
    var hostEl   = document.getElementById('dbcHost');
    var schemaEl = document.getElementById('dbcSchema');
    var chipText = document.getElementById('dbcConnChipText');
    if (!chipText) return;

    var host   = hostEl   ? hostEl.value   : '';
    var schema = schemaEl ? schemaEl.value : '';

    // Use textContent for user-supplied values to avoid XSS
    chipText.textContent = '';
    var icon = document.createTextNode('\uD83D\uDD0C ');
    var hostSpan = document.createElement('span');
    hostSpan.className = 'dbc-chip-host';
    hostSpan.textContent = host || 'not configured';
    chipText.appendChild(icon);
    chipText.appendChild(hostSpan);
    if (schema) {
      chipText.appendChild(document.createTextNode(' \u00B7 '));
      var schemaSpan = document.createElement('span');
      schemaSpan.textContent = schema;
      chipText.appendChild(schemaSpan);
    }
  }

  function _dbcToggleConnForm() {
    var chip = document.getElementById('dbcConnChip');
    var form = document.getElementById('dbcConnForm');
    var warn = document.getElementById('dbcHttpsWarning');
    if (!chip || !form) return;
    var isExpanded = chip.getAttribute('aria-expanded') === 'true';
    if (isExpanded) {
      _dbcSaveSession();
      form.style.display = 'none';
      chip.setAttribute('aria-expanded', 'false');
      if (warn) warn.style.display = 'none';
      _dbcRefreshChip();
    } else {
      form.style.display = '';
      chip.setAttribute('aria-expanded', 'true');
      if (warn && window.location.protocol !== 'https:') warn.style.display = '';
    }
  }

  var chip = document.getElementById('dbcConnChip');
  chip.addEventListener('click', _dbcToggleConnForm);
  chip.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _dbcToggleConnForm(); }
  });

  // Test Connection
  document.getElementById('dbcTestConnBtn').addEventListener('click', async function() {
    var btn    = this;
    var result = document.getElementById('dbcConnResult');
    btn.disabled = true;
    btn.textContent = '\u23F3 Testing\u2026';
    if (result) result.style.display = 'none';
    try {
      var fd = new FormData();
      fd.append('db_host',     (document.getElementById('dbcHost')     || {}).value || '');
      fd.append('db_user',     (document.getElementById('dbcUser')     || {}).value || '');
      fd.append('db_password', (document.getElementById('dbcPassword') || {}).value || '');
      fd.append('db_schema',   (document.getElementById('dbcSchema')   || {}).value || '');
      fd.append('db_adapter',  (document.getElementById('dbcAdapter')  || {}).value || 'oracle');
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

  // Restore session on page load
  _dbcRestoreSession();

  // Expose host getter for run button enable check
  window._dbcGetHost = function() {
    return (document.getElementById('dbcHost') || {}).value || '';
  };
})();
```

- [ ] **Step 2: Smoke test in browser**

- Chip collapses and expands
- After collapsing, chip shows host and schema (via textContent — no XSS)
- Refresh — host/user/schema/adapter restored; password input is blank

- [ ] **Step 3: Commit**

```bash
git add src/reports/static/ui.js
git commit -m "feat(ui): DB Compare connection chip + sessionStorage + Test Connection button (#286)"
```

---

## Task 8: JS — Run handler + results (Issue #287)

**Files:**
- Modify: `src/reports/static/ui.js`

- [ ] **Step 1: Add drop zone, run button, and results renderer**

Append to `src/reports/static/ui.js`:

```javascript
// ===========================================================================
// DB Compare — drop zone, run button enable/disable, run handler, results
// ===========================================================================
var _dbcFile = null;

(function() {
  var dz = document.getElementById('dbcDropZone');
  var fi = document.getElementById('dbcFileInput');
  if (!dz || !fi) return;

  dz.addEventListener('click', function() { fi.click(); });
  dz.addEventListener('keydown', function(e) { if (e.key === 'Enter' || e.key === ' ') fi.click(); });
  dz.addEventListener('dragover', function(e) { e.preventDefault(); dz.classList.add('drag-over'); });
  dz.addEventListener('dragleave', function() { dz.classList.remove('drag-over'); });
  dz.addEventListener('drop', function(e) {
    e.preventDefault();
    dz.classList.remove('drag-over');
    var f = e.dataTransfer.files[0];
    if (f) { _dbcFile = f; dz.querySelector('.dz-label').textContent = f.name; _updateDbcRunBtn(); }
  });
  fi.addEventListener('change', function() {
    if (fi.files[0]) { _dbcFile = fi.files[0]; dz.querySelector('.dz-label').textContent = fi.files[0].name; _updateDbcRunBtn(); }
  });
})();

function _updateDbcRunBtn() {
  var btn = document.getElementById('dbcRunBtn');
  if (!btn) return;
  var hasFile    = !!_dbcFile;
  var hasMapping = !!((document.getElementById('dbcMappingSelect') || {}).value);
  var hasSql     = !!(((document.getElementById('dbcSqlEditor') || {}).value || '').trim());
  var hasHost    = !!(window._dbcGetHost ? window._dbcGetHost() : '');
  btn.disabled   = !(hasFile && hasMapping && hasSql && hasHost);
}

['dbcMappingSelect', 'dbcSqlEditor', 'dbcHost'].forEach(function(id) {
  var el = document.getElementById(id);
  if (el) el.addEventListener('input', _updateDbcRunBtn);
  if (el) el.addEventListener('change', _updateDbcRunBtn);
});

document.getElementById('dbcRunBtn').addEventListener('click', async function() {
  var btn = this;
  btn.disabled = true;
  btn.textContent = '\u23F3 Running\u2026';
  var resultsEl = document.getElementById('dbcResults');
  if (resultsEl) resultsEl.style.display = 'none';

  try {
    var fd = new FormData();
    fd.append('actual_file',      _dbcFile);
    fd.append('query_or_table',   document.getElementById('dbcSqlEditor').value.trim());
    fd.append('mapping_id',       document.getElementById('dbcMappingSelect').value);
    fd.append('key_columns',      (document.getElementById('dbcKeyColumns') || {}).value || '');
    fd.append('output_format',    'json');
    fd.append('apply_transforms', document.getElementById('dbcApplyTransforms').checked ? 'true' : 'false');
    fd.append('db_host',          (document.getElementById('dbcHost')     || {}).value || '');
    fd.append('db_user',          (document.getElementById('dbcUser')     || {}).value || '');
    fd.append('db_password',      (document.getElementById('dbcPassword') || {}).value || '');
    fd.append('db_schema',        (document.getElementById('dbcSchema')   || {}).value || '');
    fd.append('db_adapter',       (document.getElementById('dbcAdapter')  || {}).value || 'oracle');

    var apiKeyEl = document.getElementById('apiKeyInput');
    var hdrs = apiKeyEl && apiKeyEl.value ? { 'X-API-Key': apiKeyEl.value } : {};

    var resp = await fetch('/api/v1/files/db-compare', { method: 'POST', body: fd, headers: hdrs });
    var data = await resp.json();

    if (!resp.ok) {
      var detail = (data && data.detail) ? data.detail : ('HTTP ' + resp.status);
      if (resp.status === 404) {
        _dbcShowResults(null, 'warn', '\u26A0\uFE0F Mapping not found: ' + detail);
      } else {
        _dbcShowResults(null, 'fail', '\u274C Server error \u2014 ' + detail);
      }
      return;
    }

    _dbcShowResults(
      data,
      data.workflow_status === 'passed' ? 'pass' : 'fail',
      data.workflow_status === 'passed'
        ? '\u2705 Compare complete'
        : '\u274C DB extraction failed \u2014 check your query and connection'
    );

    if (document.getElementById('dbcDownloadCsv').checked &&
        data.field_statistics && data.field_statistics.length > 0) {
      _dbcTriggerCsvDownload(data.field_statistics);
    }

  } catch (err) {
    _dbcShowResults(null, 'fail', '\u274C Request failed \u2014 check server is running');
  } finally {
    btn.disabled = false;
    btn.textContent = '\u25B6 Run DB Compare';
    _updateDbcRunBtn();
  }
});

function _dbcShowResults(data, bannerClass, bannerText) {
  var resultsEl = document.getElementById('dbcResults');
  var bannerEl  = document.getElementById('dbcStatusBanner');
  var metricsEl = document.getElementById('dbcMetrics');
  if (!resultsEl) return;

  if (bannerEl) {
    bannerEl.className   = 'dbc-status-banner ' + bannerClass;
    bannerEl.textContent = bannerText;
  }

  if (metricsEl && data) {
    var isDbToFile = _dbcDirection === 'db-to-file';
    var cards = [
      { label: isDbToFile ? 'Source Rows' : 'Actual Rows', value: isDbToFile ? data.db_rows_extracted : data.total_rows_file2, color: '' },
      { label: isDbToFile ? 'Actual Rows' : 'Source Rows', value: isDbToFile ? data.total_rows_file2  : data.db_rows_extracted, color: '' },
      { label: 'Matching',       value: data.matching_rows, color: 'green' },
      { label: 'Differences',    value: data.differences,   color: 'amber' },
      { label: 'Only in Source', value: isDbToFile ? data.only_in_file1 : data.only_in_file2, color: 'red' },
      { label: 'Only in Actual', value: isDbToFile ? data.only_in_file2 : data.only_in_file1, color: 'red' },
    ];
    metricsEl.textContent = '';
    cards.forEach(function(c) {
      var card  = document.createElement('div');
      card.className = 'dbc-metric-card';
      var val   = document.createElement('div');
      val.className  = 'dbc-metric-value' + (c.color ? ' ' + c.color : '');
      val.textContent = c.value != null ? c.value.toLocaleString() : '\u2014';
      var lbl   = document.createElement('div');
      lbl.className  = 'dbc-metric-label';
      lbl.textContent = c.label;
      card.appendChild(val);
      card.appendChild(lbl);
      metricsEl.appendChild(card);
    });
  } else if (metricsEl) {
    metricsEl.textContent = '';
  }

  var dlRow = document.getElementById('dbcDownloadRow');
  var dlBtn = document.getElementById('dbcDownloadDiffBtn');
  if (dlRow && data) {
    var hasDiff = ((data.differences || 0) + (data.only_in_file1 || 0) + (data.only_in_file2 || 0)) > 0;
    dlRow.style.display = hasDiff ? '' : 'none';
    if (dlBtn) {
      if (!data.field_statistics) {
        dlBtn.disabled    = true;
        dlBtn.textContent = '\u26A0\uFE0F Detailed diff unavailable';
      } else {
        dlBtn.disabled    = false;
        dlBtn.textContent = '\u2B07 Download Diff CSV';
        dlBtn._fieldStatistics = data.field_statistics;
      }
    }
  } else if (dlRow) {
    dlRow.style.display = 'none';
  }

  resultsEl.style.display = '';
}
```

- [ ] **Step 2: Smoke test**

Fill all required fields in browser. Confirm Run button enables. Verify results render after submission.

- [ ] **Step 3: Commit**

```bash
git add src/reports/static/ui.js
git commit -m "feat(ui): DB Compare run handler + direction-aware results metric cards (#287)"
```

---

## Task 9: JS — Client-side diff CSV (Issue #288)

**Files:**
- Modify: `src/reports/static/ui.js`

- [ ] **Step 1: Add CSV builder + download handler**

Append to `src/reports/static/ui.js`:

```javascript
// ===========================================================================
// DB Compare — client-side diff CSV
// ===========================================================================
function _dbcBuildDiffCsv(fieldStatistics) {
  var rows = ['row_number,key_columns,field_name,db_value,file_value,difference_type'];
  (fieldStatistics || []).forEach(function(stat) {
    var fieldName = stat.field_name || stat.field || '';
    var diffs     = stat.differences || stat.mismatches || [];
    diffs.forEach(function(d) {
      function esc(v) {
        var s = (v == null ? '' : String(v)).replace(/"/g, '""');
        return (s.indexOf(',') >= 0 || s.indexOf('"') >= 0 || s.indexOf('\n') >= 0) ? '"' + s + '"' : s;
      }
      rows.push([
        esc(d.row_number),
        esc(Array.isArray(d.key_columns) ? d.key_columns.join('|') : (d.key_columns || '')),
        esc(fieldName),
        esc(d.db_value),
        esc(d.file_value),
        esc(d.difference_type || 'mismatch'),
      ].join(','));
    });
  });
  return rows.join('\r\n');
}

function _dbcTriggerCsvDownload(fieldStatistics) {
  var csv  = _dbcBuildDiffCsv(fieldStatistics);
  var blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  var url  = URL.createObjectURL(blob);
  var a    = document.createElement('a');
  a.href     = url;
  a.download = 'db_compare_diff.csv';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(function() { URL.revokeObjectURL(url); }, 10000);
}

document.getElementById('dbcDownloadDiffBtn').addEventListener('click', function() {
  var btn   = this;
  var stats = btn._fieldStatistics;
  if (!stats) return;
  btn.disabled    = true;
  btn.textContent = '\u23F3 Building CSV\u2026';
  setTimeout(function() {
    try { _dbcTriggerCsvDownload(stats); }
    finally {
      btn.disabled    = false;
      btn.textContent = '\u2B07 Download Diff CSV';
    }
  }, 0);
});
```

- [ ] **Step 2: Verify no second server call**

In browser DevTools Network tab after a run: confirm only one `/api/v1/files/db-compare` request when downloading CSV.

- [ ] **Step 3: Commit**

```bash
git add src/reports/static/ui.js
git commit -m "feat(ui): DB Compare client-side diff CSV + auto-download (#288)"
```

---

## Task 10: E2E Tests (Issue #289)

**Files:**
- Create: `tests/e2e/test_e2e_db_compare.py`

- [ ] **Step 1: Write E2E tests**

Create `tests/e2e/test_e2e_db_compare.py`:

```python
"""E2E Playwright tests for the DB Compare tab."""
from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect


class TestDbCompareTabPresence:
    """Tests that the DB Compare tab exists and navigates correctly."""

    def test_db_compare_tab_visible(self, ui_page: Page) -> None:
        """DB Compare tab button is visible in the tab bar."""
        expect(ui_page.locator('#tab-dbcompare')).to_be_visible()

    def test_db_compare_tab_text(self, ui_page: Page) -> None:
        """Tab button text is 'DB Compare'."""
        expect(ui_page.locator('#tab-dbcompare')).to_have_text('DB Compare')

    def test_db_compare_panel_hidden_initially(self, ui_page: Page) -> None:
        """DB Compare panel is hidden when Quick Test is the active tab."""
        expect(ui_page.locator('#panel-dbcompare')).to_be_hidden()

    def test_clicking_tab_shows_panel(self, ui_page: Page) -> None:
        """Clicking DB Compare tab makes the panel visible."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#panel-dbcompare')).to_be_visible()

    def test_switching_away_hides_panel(self, ui_page: Page) -> None:
        """Switching to Quick Test tab hides the DB Compare panel."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#tab-quick')
        expect(ui_page.locator('#panel-dbcompare')).to_be_hidden()


class TestDbCompareDirectionSwap:
    """Tests for the swap direction button."""

    def test_initial_direction_label(self, ui_page: Page) -> None:
        """Initial direction label shows 'DB is source · File is actual'."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('DB is source \u00B7 File is actual')

    def test_swap_changes_label(self, ui_page: Page) -> None:
        """Swap button changes direction label."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcSwapBtn')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('File is source \u00B7 DB is actual')

    def test_swap_again_restores_label(self, ui_page: Page) -> None:
        """Second swap restores original label."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcSwapBtn')
        ui_page.click('#dbcSwapBtn')
        expect(ui_page.locator('#dbcDirectionLabel')).to_have_text('DB is source \u00B7 File is actual')


class TestDbCompareConnectionChip:
    """Tests for the DB connection chip expand/collapse."""

    def test_connection_form_hidden_initially(self, ui_page: Page) -> None:
        """Connection form is hidden on load."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcConnForm')).to_be_hidden()

    def test_clicking_chip_expands_form(self, ui_page: Page) -> None:
        """Clicking the connection chip reveals the form."""
        ui_page.click('#tab-dbcompare')
        ui_page.click('#dbcConnChip')
        expect(ui_page.locator('#dbcConnForm')).to_be_visible()

    def test_https_warning_element_exists(self, ui_page: Page) -> None:
        """HTTPS warning element is present in the DOM."""
        ui_page.click('#tab-dbcompare')
        assert ui_page.locator('#dbcHttpsWarning').count() == 1


class TestDbCompareRunButton:
    """Tests for the Run button state."""

    def test_run_button_disabled_initially(self, ui_page: Page) -> None:
        """Run button is disabled on load."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcRunBtn')).to_be_disabled()

    def test_results_hidden_on_load(self, ui_page: Page) -> None:
        """Results area is hidden before any run."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcResults')).to_be_hidden()

    def test_download_diff_btn_row_hidden_on_load(self, ui_page: Page) -> None:
        """Download Diff CSV row is hidden before any run."""
        ui_page.click('#tab-dbcompare')
        expect(ui_page.locator('#dbcDownloadRow')).to_be_hidden()
```

- [ ] **Step 2: Run E2E tests (server must be running on port 8000)**

```bash
python3 -m pytest tests/e2e/test_e2e_db_compare.py -v --browser chromium
```

Expected: all 11 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_e2e_db_compare.py
git commit -m "test(e2e): Playwright tests for DB Compare tab (#289)"
```

---

## Task 11: Docs (Issue #290)

**Files:**
- Modify: `docs/USAGE_AND_OPERATIONS_GUIDE.md`

- [ ] **Step 1: Add DB Compare section**

Search for `## Web UI` or the API Tester heading in `docs/USAGE_AND_OPERATIONS_GUIDE.md`. Add a new subsection after the existing API Tester content:

```markdown
### DB Compare Tab

The DB Compare tab lets you compare Oracle database data against an uploaded batch file — or vice versa — through a split-panel interface.

#### Comparison Directions

| Direction | Source of Truth | Purpose |
|-----------|----------------|---------|
| **DB → File** (default) | Staging database | Verify the transformation was applied correctly |
| **File → DB** | Uploaded file | Verify the load was successful |

Click **⇄ swap** to toggle. Metric card labels update automatically; form values are preserved.

#### DB Panel — Connection

Click the connection chip (`🔌 host · schema`) to expand the form:

| Field | Notes |
|-------|-------|
| DB Adapter | `oracle` (only oracle supports connection override) |
| Host / DSN | e.g. `localhost:1521/FREEPDB1` |
| Username | DB username |
| Password | Never saved to sessionStorage |
| Schema | Schema prefix (informational) |

Click **🔗 Test Connection** to verify credentials before running.

> When running on a non-HTTPS origin, an inline warning reminds you that credentials will be sent unencrypted.

#### DB Panel — SQL Editor

Write a SELECT query:

- **DB → File**: `SELECT column1, column2 FROM SCHEMA.TABLE`
- **File → DB**: `SELECT t1.col, t2.col FROM TARGET.TABLE1 t1 JOIN TARGET.TABLE2 t2 ON t1.id = t2.fk_id`

#### File Panel

Upload the batch file and select a mapping. **Key Columns** (set in the DB panel) are shared between both sides.

Options:
- **Apply transforms from mapping** — applies field-level transforms to DB rows before comparison
- **Download diff as CSV** — auto-downloads the diff file when results load

#### Results

| Tile | Color | Description |
|------|-------|-------------|
| Source Rows | accent | Row count from the source side |
| Actual Rows | accent | Row count from the actual side |
| Matching | green | Rows that match exactly |
| Differences | amber | Rows with field-level differences |
| Only in Source | red | Rows present only in the source |
| Only in Actual | red | Rows present only in the actual |

The **Download Diff CSV** button generates a diff file client-side (no extra server call). CSV columns: `row_number`, `key_columns`, `field_name`, `db_value`, `file_value`, `difference_type`. The button is hidden when the compare is clean.
```

- [ ] **Step 2: Build Sphinx docs**

```bash
cd /Users/buddy/claude-code/automations/valdo/docs/sphinx && make html 2>&1 | tail -5
```

Expected: `Build succeeded`

- [ ] **Step 3: Commit**

```bash
git add docs/USAGE_AND_OPERATIONS_GUIDE.md
git commit -m "docs: document DB Compare tab in usage guide (#290)"
```

---

## Task 12: Final validation + PR

- [ ] **Step 1: Run full test suite**

```bash
python3 -m pytest tests/unit/ -q --cov=src --cov-report=term-missing 2>&1 | tail -10
```

Expected: all pass, coverage >=80%

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/db-compare-tab
gh pr create \
  --title "feat(ui): DB Compare tab — split panel, connection form, metric cards, diff CSV (#280-#290)" \
  --body "$(cat <<'EOF'
Closes #280 #281 #282 #283 #284 #285 #286 #287 #288 #289 #290

## Summary
- New 5th tab: DB Compare with direction swap (DB→File / File→DB)
- Collapsed connection chip with sessionStorage persistence (password excluded)
- POST /api/v1/system/db-ping — Test Connection button
- Extended POST /api/v1/files/db-compare with connection override + apply_transforms
- Direction-aware metric cards (Source/Actual labels swap with direction toggle)
- Client-side diff CSV from field_statistics — no second server round-trip
- 11 E2E Playwright tests + unit tests for all backend changes

## Test plan
- [ ] Unit tests pass: pytest tests/unit/ -q
- [ ] E2E tests pass: pytest tests/e2e/test_e2e_db_compare.py
- [ ] Coverage >=80%
- [ ] Dark + light theme verified in browser
- [ ] Direction swap updates labels and borders without clearing form values
- [ ] Password is NOT in sessionStorage after chip collapse
EOF
)"
```
