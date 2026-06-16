"""S13.5-2 (#415) — audit coverage for auth failures + config mutations.

These tests assert that:

* an API-key auth failure (api/auth.py) emits an ``auth_failure`` audit
  record with ``outcome="failure"`` and never the attempted key value;
* a mapping upload (the operator-facing mutation entry point) emits a
  ``config_mutation`` record carrying the actor, resource and action;
* the new events chain correctly (``verify_audit_log`` still passes over a
  log that contains them).

They drive the audit log to an isolated ``tmp_path`` file via the
``AUDIT_LOG_PATH`` env var and reset the module-level singleton so each
test sees only its own records.
"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import pytest

import src.utils.audit_logger as audit_mod
from src.utils.audit_logger import (
    EVENT_TYPES,
    audit_mutation,
    get_audit_logger,
    verify_audit_log,
)


@pytest.fixture()
def isolated_audit(monkeypatch, tmp_path: Path):
    """Point the audit logger at a fresh tmp file and reset the singleton."""
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("AUDIT_LOG_PATH", str(log_path))
    monkeypatch.delenv("VALDO_AUDIT_HMAC_KEY", raising=False)
    # Reset the module-level singleton so it re-reads AUDIT_LOG_PATH.
    monkeypatch.setattr(audit_mod, "_default_logger", None, raising=False)
    yield log_path


def _read_records(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    return [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ---------------------------------------------------------------------------
# Event-type registration
# ---------------------------------------------------------------------------


def test_new_event_types_registered():
    """The S13.5-2 event types are recognised (no 'unknown type' warning)."""
    assert "auth_failure" in EVENT_TYPES
    assert "config_mutation" in EVENT_TYPES


# ---------------------------------------------------------------------------
# Auth failure → audit record with outcome=failure
# ---------------------------------------------------------------------------


def test_api_key_auth_failure_emits_audit_failure(isolated_audit, monkeypatch):
    """An invalid X-API-Key produces a 403 AND an auth_failure audit record."""
    monkeypatch.setenv("API_KEYS", "good-key:admin")
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get(
        "/api/v1/system/info",
        headers={"X-API-Key": "super-secret-wrong-key"},
    )
    # The original auth error still reaches the client (not masked).
    assert resp.status_code == 403

    records = _read_records(isolated_audit)
    auth_failures = [r for r in records if r["event"] == "auth_failure"]
    assert auth_failures, "expected an auth_failure audit record"
    rec = auth_failures[-1]
    assert rec["outcome"] == "failure"
    assert rec["auth_kind"] == "api_key"
    # The secret/token value must NEVER appear in the payload.
    assert "super-secret-wrong-key" not in json.dumps(rec)


def test_missing_api_key_emits_audit_failure(isolated_audit, monkeypatch):
    """A missing X-API-Key (401) is also audited as outcome=failure."""
    monkeypatch.setenv("API_KEYS", "good-key:admin")
    from fastapi.testclient import TestClient

    from src.api.main import app

    client = TestClient(app)
    resp = client.get("/api/v1/system/info")
    assert resp.status_code == 401

    records = _read_records(isolated_audit)
    auth_failures = [r for r in records if r["event"] == "auth_failure"]
    assert auth_failures
    assert auth_failures[-1]["reason"] == "missing_api_key"


# ---------------------------------------------------------------------------
# Mutation → audit record with actor + resource + action
# ---------------------------------------------------------------------------


def test_mapping_upload_emits_config_mutation(isolated_audit, monkeypatch):
    """A mapping upload emits a config_mutation record (actor/resource/action)."""
    monkeypatch.setenv("API_KEYS", "owner-key:mapping_owner")
    from fastapi.testclient import TestClient

    from src.api.main import app

    class DummyConverter:
        def from_csv(self, path, mapping_name=None, file_format=None):
            return {"mapping_name": mapping_name or "dummy_map"}

        def save(self, output_path):
            Path(output_path).write_text(
                '{"mapping_name":"dummy_map","source":{"format":"pipe_delimited"},"fields":[]}',
                encoding="utf-8",
            )

    monkeypatch.setattr(
        "src.api.routers.mappings.TemplateConverter", DummyConverter
    )

    client = TestClient(app)
    files = {"file": ("template.csv", BytesIO(b"a,b\n1,2\n"), "text/csv")}
    resp = client.post(
        "/api/v1/mappings/upload?mapping_name=dummy_map&file_format=pipe_delimited",
        files=files,
        headers={"X-API-Key": "owner-key"},
    )
    assert resp.status_code == 200

    out = Path("config/mappings/dummy_map.json")
    if out.exists():
        out.unlink()

    records = _read_records(isolated_audit)
    muts = [r for r in records if r["event"] == "config_mutation"]
    assert muts, "expected a config_mutation audit record"
    rec = muts[-1]
    assert rec["resource_type"] == "mapping"
    assert rec["resource_id"] == "dummy_map"
    assert rec["action"] == "create"
    assert rec["outcome"] == "success"
    assert rec["actor"]  # actor identity present


def test_audit_mutation_helper_shapes_record(isolated_audit):
    """The audit_mutation helper writes a well-formed config_mutation event."""
    audit_mutation(
        resource_type="rules",
        resource_id="my_rules",
        action="create",
        actor="apikey:abc123",
        triggered_by="api",
        correlation_id="corr-1",
    )
    records = _read_records(isolated_audit)
    rec = records[-1]
    assert rec["event"] == "config_mutation"
    assert rec["resource_type"] == "rules"
    assert rec["resource_id"] == "my_rules"
    assert rec["action"] == "create"
    assert rec["actor"] == "apikey:abc123"
    assert rec["correlation_id"] == "corr-1"
    assert rec["outcome"] == "success"


# ---------------------------------------------------------------------------
# Chain integrity over the new events
# ---------------------------------------------------------------------------


def test_new_events_chain_verifies(isolated_audit):
    """A log of mixed new events still passes verify_audit_log."""
    logger = get_audit_logger()
    logger.emit("auth_failure", outcome="failure", auth_kind="api_key",
                reason="invalid_api_key", client_ip="10.0.0.1")
    audit_mutation(
        resource_type="masking",
        resource_id="ssn_mask",
        action="update",
        actor="ldap-user",
    )
    logger.emit("auth_failure", outcome="failure", auth_kind="mcp_api_key",
                reason="invalid_api_key", client_ip="10.0.0.2")

    result = verify_audit_log(isolated_audit)
    assert result.ok, result.error
    assert result.records_checked >= 3
