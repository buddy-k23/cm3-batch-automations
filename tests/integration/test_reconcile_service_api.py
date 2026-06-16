"""Reconcile service + REST endpoint integration tests (#407).

Exercises the adapter-agnostic reconcile path end-to-end against a REAL
SQLite fixture database created under pytest's ``tmp_path`` (never tracked).
A table is created, a mapping is pointed at it via the ``table`` override,
and the verdict is asserted for three cases the #407 AC names:

1. A clean reconcile (all mapped columns present and type-compatible).
2. A seeded type mismatch — a column declared ``integer`` in the mapping but
   ``VARCHAR`` in the table — flagged in the verdict's ``mismatches`` list.
3. A boolean / non-native-carrier advisory — a column declared ``boolean``
   stored as ``INTEGER`` (SQLite has no native boolean) surfaced in the
   verdict's ``advisories`` list, NOT as an error.

The service is tested directly (the canonical seam every surface calls) and
the REST endpoint is tested via ``TestClient`` with ``DB_ADAPTER=sqlite`` and
``DB_PATH`` pointed at the fixture DB so the same verdict flows through HTTP.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_fixture_db(db_path: Path) -> None:
    """Create a SQLite DB with one CUSTOMER table for reconciliation.

    The schema is deliberately shaped to exercise all three #407 verdict
    cases against ``_write_mapping``'s field declarations:

    * ``CUSTOMER_ID`` TEXT  — mapping declares ``string`` -> clean match.
    * ``FIRST_NAME``  TEXT  — mapping declares ``string`` -> clean match.
    * ``AGE``         TEXT  — mapping declares ``integer`` -> TYPE MISMATCH.
    * ``IS_ACTIVE``   INTEGER — mapping declares ``boolean`` -> ADVISORY
      (SQLite has no native boolean; integer carrier is compatible-but-noted).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMER ("
            "CUSTOMER_ID TEXT NOT NULL, "
            "FIRST_NAME TEXT, "
            "AGE VARCHAR(10), "
            "IS_ACTIVE INTEGER"
            ")"
        )
        conn.commit()
    finally:
        conn.close()


def _write_mapping(mapping_path: Path, *, with_mismatch: bool, with_advisory: bool) -> None:
    """Write a mapping JSON whose target columns match the fixture table.

    Args:
        mapping_path: Where to write the mapping JSON.
        with_mismatch: When True, declare ``AGE`` as ``integer`` (it is
            VARCHAR in the DB) to force a type mismatch.
        with_advisory: When True, declare ``IS_ACTIVE`` as ``boolean`` (it is
            INTEGER in the DB) to force a non-native-boolean advisory.
    """
    mappings = [
        {
            "source_column": "customer_id",
            "target_column": "CUSTOMER_ID",
            "data_type": "string",
            "required": True,
            "transformations": [],
            "validation_rules": [],
        },
        {
            "source_column": "first_name",
            "target_column": "FIRST_NAME",
            "data_type": "string",
            "required": False,
            "transformations": [],
            "validation_rules": [],
        },
    ]
    if with_mismatch:
        mappings.append(
            {
                "source_column": "age",
                "target_column": "AGE",
                "data_type": "integer",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            }
        )
    if with_advisory:
        mappings.append(
            {
                "source_column": "is_active",
                "target_column": "IS_ACTIVE",
                "data_type": "boolean",
                "required": False,
                "transformations": [],
                "validation_rules": [],
            }
        )

    doc = {
        "mapping_name": "reconcile_fixture",
        "version": "1.0.0",
        "description": "Fixture mapping for #407 reconcile tests",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": mappings,
        "key_columns": ["customer_id"],
        "metadata": {},
    }
    mapping_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Service-layer tests
# ---------------------------------------------------------------------------


def test_service_clean_reconcile(tmp_path, monkeypatch):
    """A mapping whose columns all match -> status 'clean', valid True."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "clean.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=False)

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))

    from src.services.reconcile_service import reconcile_mapping_service

    verdict = reconcile_mapping_service(str(mapping_path))

    assert verdict["status"] == "clean", verdict
    assert verdict["valid"] is True
    assert verdict["table"] == "CUSTOMER"
    assert verdict["summary"]["error_count"] == 0
    assert verdict["summary"]["mismatch_count"] == 0
    assert verdict["mismatches"] == []


def test_service_flags_type_mismatch(tmp_path, monkeypatch):
    """A mapping declaring integer for a VARCHAR column -> mismatch flagged."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "mismatch.json"
    _write_mapping(mapping_path, with_mismatch=True, with_advisory=False)

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))

    from src.services.reconcile_service import reconcile_mapping_service

    verdict = reconcile_mapping_service(str(mapping_path))

    assert verdict["status"] == "mismatch", verdict
    assert verdict["summary"]["mismatch_count"] == 1
    assert any("AGE" in m for m in verdict["mismatches"]), verdict["mismatches"]


def test_service_emits_boolean_advisory(tmp_path, monkeypatch):
    """A boolean declared over an INTEGER carrier -> advisory, not error."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "advisory.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=True)

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))

    from src.services.reconcile_service import reconcile_mapping_service

    verdict = reconcile_mapping_service(str(mapping_path))

    assert verdict["status"] == "advisories", verdict
    assert verdict["valid"] is True
    assert verdict["summary"]["advisory_count"] >= 1
    assert verdict["summary"]["mismatch_count"] == 0
    assert any("IS_ACTIVE" in a for a in verdict["advisories"]), verdict["advisories"]


def test_service_table_override(tmp_path, monkeypatch):
    """The ``table`` argument overrides a mapping with no declared table."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "no_table.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=False)
    # Strip the declared table so only the override resolves it.
    doc = json.loads(mapping_path.read_text())
    doc["target"] = {"type": "database"}
    mapping_path.write_text(json.dumps(doc), encoding="utf-8")

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))

    from src.services.reconcile_service import reconcile_mapping_service

    verdict = reconcile_mapping_service(str(mapping_path), table="CUSTOMER")

    assert verdict["table"] == "CUSTOMER"
    assert verdict["valid"] is True


def test_service_missing_table_raises(tmp_path, monkeypatch):
    """No declared table and no override -> ReconcileServiceError."""
    mapping_path = tmp_path / "no_table.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=False)
    doc = json.loads(mapping_path.read_text())
    doc["target"] = {"type": "database"}
    mapping_path.write_text(json.dumps(doc), encoding="utf-8")

    monkeypatch.setenv("DB_ADAPTER", "sqlite")

    from src.services.reconcile_service import (
        ReconcileServiceError,
        reconcile_mapping_service,
    )

    with pytest.raises(ReconcileServiceError):
        reconcile_mapping_service(str(mapping_path))


# ---------------------------------------------------------------------------
# REST endpoint tests
# ---------------------------------------------------------------------------


def _fresh_app():
    """Reload ``src.api.main`` so it picks up monkeypatched env at import."""
    for mod_name in ["src.api.main"]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    return importlib.import_module("src.api.main").app


def test_rest_reconcile_clean(tmp_path, monkeypatch):
    """POST /api/v2/reconcile returns a clean verdict over HTTP (SQLite)."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "clean.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=False)

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEYS", "test-key:admin")
    monkeypatch.setenv("VALDO_SESSION_SIGNING_KEY", "test-only-key-not-for-prod")

    app = _fresh_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/v2/reconcile",
            json={"mapping": str(mapping_path), "table": "CUSTOMER"},
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "clean"
    assert body["valid"] is True
    assert body["table"] == "CUSTOMER"


def test_rest_reconcile_mismatch_and_advisory(tmp_path, monkeypatch):
    """POST surfaces both a mismatch and an advisory in the verdict JSON."""
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    mapping_path = tmp_path / "both.json"
    _write_mapping(mapping_path, with_mismatch=True, with_advisory=True)

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("API_KEYS", "test-key:admin")
    monkeypatch.setenv("VALDO_SESSION_SIGNING_KEY", "test-only-key-not-for-prod")

    app = _fresh_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/v2/reconcile",
            json={"mapping": str(mapping_path)},
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # A genuine mismatch dominates the headline status.
    assert body["status"] == "mismatch", body
    assert body["summary"]["mismatch_count"] == 1
    assert body["summary"]["advisory_count"] >= 1
    assert any("AGE" in m for m in body["mismatches"])
    assert any("IS_ACTIVE" in a for a in body["advisories"])


def test_rest_reconcile_bad_mapping_returns_400(tmp_path, monkeypatch):
    """A non-existent mapping path -> 400 (not a 500)."""
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("API_KEYS", "test-key:admin")
    monkeypatch.setenv("VALDO_SESSION_SIGNING_KEY", "test-only-key-not-for-prod")

    app = _fresh_app()
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            "/api/v2/reconcile",
            json={"mapping": str(tmp_path / "does_not_exist.json")},
            headers={"X-API-Key": "test-key"},
        )

    assert resp.status_code == 400, resp.text
