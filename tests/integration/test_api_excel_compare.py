"""Integration tests for POST /api/v1/files/excel-compare (S24-3, Sprint 24).

Written BEFORE implementation (TDD). This is an API route, so it lives under
``tests/integration/`` and runs in the integration gate, separate from the unit
gate (combined runs leak module-scoped auth state).

The endpoint mirrors ``/api/v1/files/db-compare`` exactly: same auth dependency
(``verify_session_or_api_key`` via ``require_api_key``), same ``_safe_upload_path``
upload handling, same HTML-report-as-``report_url`` wiring. It delegates all
compare logic to :func:`src.services.excel_db_compare_service.compare_excel_to_db`
(S24-2) — the router contains no comparison logic.

Everything runs against a **real on-disk SQLite database** seeded under pytest's
``tmp_path`` (``DB_ADAPTER=sqlite`` + ``DB_PATH``) and a **generated ``.xlsx``**
upload, so the suite needs no Oracle and no mocks on the DB side.

Auth fixture: the module-scoped override of the shared base dependency
``verify_session_or_api_key`` (mirroring ``test_api_files.py`` /
``test_api_system.py``) authenticates every request as an admin AuthContext so
the tests reach the real handler. One test (``test_auth_required_*``) explicitly
removes the override to prove the endpoint fails closed (401/403).
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from src.api.main import app
from src.api.auth import AuthContext, verify_session_or_api_key

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Auth — module-scoped admin override (mirrors test_api_files.py)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True, scope="module")
def _authenticate_api():
    """Authenticate every request in this module as an admin AuthContext.

    Overriding the shared base dependency ``verify_session_or_api_key`` makes
    ``require_api_key`` resolve to this test context so the tests reach the real
    handler. The override is installed only for this module and removed on
    teardown; product auth is unchanged.
    """
    app.dependency_overrides[verify_session_or_api_key] = (
        lambda: AuthContext(key_id="uat-int-test", role="admin")
    )
    yield
    app.dependency_overrides.pop(verify_session_or_api_key, None)


# ---------------------------------------------------------------------------
# Fixtures — a real SQLite DB + matching/mismatching generated .xlsx uploads
# ---------------------------------------------------------------------------

@pytest.fixture()
def seeded_db(tmp_path: Path) -> str:
    """Create and seed a real on-disk SQLite database under ``tmp_path``."""
    db_path = tmp_path / "excel_compare_proof.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE ACCOUNTS (ID INTEGER, NAME TEXT, BALANCE TEXT)")
    conn.executemany(
        "INSERT INTO ACCOUNTS VALUES (?, ?, ?)",
        [
            (1, "alice", "100"),
            (2, "bob", "200"),
            (3, "carol", "300"),
        ],
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _xlsx_bytes(rows: list[list], *, sheet_name: str = "Sheet1") -> bytes:
    """Build an in-memory ``.xlsx`` from raw row lists (first row = header)."""
    wb = Workbook()
    default = wb.active
    wb.remove(default)
    ws = wb.create_sheet(title=sheet_name)
    for row in rows:
        ws.append(row)
    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    return buffer.read()


_MATCHING_ROWS = [
    ["ID", "NAME", "BALANCE"],
    [1, "alice", "100"],
    [2, "bob", "200"],
    [3, "carol", "300"],
]

_MISMATCH_ROWS = [
    ["ID", "NAME", "BALANCE"],
    [1, "alice", "100"],
    [2, "bob", "999"],  # balance differs from DB (200)
    [3, "carol", "300"],
]


def _sqlite_env(monkeypatch, db_path: str) -> None:
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", db_path)


# ---------------------------------------------------------------------------
# Happy path — matching Excel vs DB returns 200 + passed
# ---------------------------------------------------------------------------

def test_endpoint_exists() -> None:
    """The excel-compare endpoint must be registered (not 404)."""
    resp = client.post("/api/v1/files/excel-compare")
    # A missing route is 404; a registered route with a bad payload is 422.
    assert resp.status_code != 404


def test_matching_excel_vs_db_returns_200_passed(seeded_db, monkeypatch) -> None:
    """Excel that matches the DB extract -> 200 with workflow_status 'passed'."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "db-source",
            "output_format": "json",
        },
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workflow_status"] == "passed"
    assert body["db_rows_extracted"] == 3
    assert body["excel_rows_read"] == 3
    assert body["matching_rows"] == 3
    assert body["differences"] == 0
    assert body["direction"] == "db-source"


def test_mismatch_is_reported(seeded_db, monkeypatch) -> None:
    """A row that differs between Excel and DB -> 200 with failed + differences."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MISMATCH_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "db-source",
            "output_format": "json",
        },
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workflow_status"] == "failed"
    assert body["differences"] >= 1
    assert body["matching_rows"] == 2


# ---------------------------------------------------------------------------
# Both directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("direction", ["db-source", "excel-source"])
def test_both_directions_match(seeded_db, monkeypatch, direction) -> None:
    """Both 'db-source' and 'excel-source' directions return 200 + passed."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": direction,
            "output_format": "json",
        },
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["workflow_status"] == "passed"
    assert body["direction"] == direction


def test_invalid_direction_returns_4xx(seeded_db, monkeypatch) -> None:
    """An unrecognised direction must return a clean 4xx, not a 500."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "sideways",
            "output_format": "json",
        },
        files=files,
    )
    assert 400 <= resp.status_code < 500, resp.text


# ---------------------------------------------------------------------------
# HTML report -> report_url (the link S24-5 UI consumes)
# ---------------------------------------------------------------------------

def test_html_output_returns_report_url(seeded_db, monkeypatch) -> None:
    """output_format=html must produce a served report_url under /uploads/."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "db-source",
            "output_format": "html",
        },
        files=files,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["report_url"] is not None
    assert body["report_url"].startswith("/uploads/")
    assert body["report_url"].endswith(".html")


# ---------------------------------------------------------------------------
# Bad sheet / bad column -> clean 4xx (not 500)
# ---------------------------------------------------------------------------

def test_bad_sheet_returns_4xx_not_500(seeded_db, monkeypatch) -> None:
    """A sheet name that does not exist -> clean 4xx, never a 500."""
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "db-source",
            "sheet": "DoesNotExist",
            "output_format": "json",
        },
        files=files,
    )
    assert resp.status_code != 500, resp.text
    assert 400 <= resp.status_code < 500, resp.text


def test_bad_column_returns_4xx_not_500(seeded_db, monkeypatch) -> None:
    """A key column absent from the Excel sheet -> clean 4xx, never a 500.

    The DB side has no NOPE column either; the comparison machinery raises a
    structured error that the router maps to a 4xx rather than leaking a 500.
    """
    _sqlite_env(monkeypatch, seeded_db)
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "NOPE",
            "direction": "db-source",
            "output_format": "json",
        },
        files=files,
    )
    assert resp.status_code != 500, resp.text
    assert 400 <= resp.status_code < 500, resp.text


# ---------------------------------------------------------------------------
# No secrets echoed back
# ---------------------------------------------------------------------------

def test_no_password_in_response_body(seeded_db, monkeypatch) -> None:
    """A db_password override must never be reflected in the response body.

    The connection override pins the sqlite adapter + the seeded DB path so the
    run succeeds (200), then we assert the supplied password is absent from the
    response body and the model carries no connection fields at all — matching
    the db-compare contract that credentials are never echoed back.
    """
    _sqlite_env(monkeypatch, seeded_db)
    secret = "SuperSecretPw123"
    files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
    resp = client.post(
        "/api/v1/files/excel-compare",
        data={
            "query_or_table": "ACCOUNTS",
            "key_columns": "ID",
            "direction": "db-source",
            "output_format": "json",
            # Pin the sqlite adapter so the run succeeds; the password must not
            # leak into the response body regardless.
            "db_adapter": "sqlite",
            "db_password": secret,
        },
        files=files,
    )
    assert resp.status_code == 200, resp.text
    assert secret not in resp.text
    body = resp.json()
    assert "db_password" not in body
    assert "db_user" not in body
    assert "db_host" not in body


# ---------------------------------------------------------------------------
# Auth required — remove the override to prove the endpoint fails closed
# ---------------------------------------------------------------------------

def test_auth_required_without_override(seeded_db, monkeypatch) -> None:
    """Without the auth override and no X-API-Key, the endpoint fails closed.

    Mirrors the auth assertions in the other api integration tests: 401
    (missing key), 403 (invalid key), or 503 (no keys configured) — never a
    200 that would indicate an auth bypass.
    """
    _sqlite_env(monkeypatch, seeded_db)
    # Drop the module-scoped admin override for this one request.
    app.dependency_overrides.pop(verify_session_or_api_key, None)
    try:
        files = {"excel_file": ("accounts.xlsx", io.BytesIO(_xlsx_bytes(_MATCHING_ROWS)), "application/octet-stream")}
        resp = client.post(
            "/api/v1/files/excel-compare",
            data={
                "query_or_table": "ACCOUNTS",
                "key_columns": "ID",
                "direction": "db-source",
                "output_format": "json",
            },
            files=files,
        )
        assert resp.status_code in (401, 403, 503), resp.text
    finally:
        app.dependency_overrides[verify_session_or_api_key] = (
            lambda: AuthContext(key_id="uat-int-test", role="admin")
        )
