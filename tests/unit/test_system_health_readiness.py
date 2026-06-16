"""Tests for /health (liveness) vs /ready (readiness) and /info DB check (S16-1, #425).

Semantics under test:
- ``/health`` is a LIVENESS probe: process up, fast, NEVER touches the DB, so a
  transient DB outage does not flap the load balancer / take the app out of
  rotation. It must stay 200 + status="healthy" regardless of DB state.
- ``/ready`` is a READINESS probe: it performs a cheap, bounded DB connectivity
  check and reports whether the app can serve DB-backed traffic.
- ``/info`` no longer hardcodes ``database_connected=False`` — it reflects the
  real (bounded) probe result.
"""

from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}

# Patch where it is *used* (router namespace), not where it is defined — the
# router imports the symbol by name at module load.
_PROBE = "src.api.routers.system.check_db_connectivity"


def _ensure_dev_key(monkeypatch):
    monkeypatch.setenv("API_KEYS", "dev-key:admin")


def test_health_is_liveness_and_stays_up_when_db_down(monkeypatch):
    """/health must stay healthy (200) even when the DB is unreachable."""
    _ensure_dev_key(monkeypatch)
    with patch(_PROBE, return_value=False):
        r = client.get("/api/v1/system/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_ready_reports_connected_when_db_reachable(monkeypatch):
    """/ready returns ready=True + database_connected=True when DB is reachable."""
    _ensure_dev_key(monkeypatch)
    with patch(_PROBE, return_value=True):
        r = client.get("/api/v1/system/ready")
    assert r.status_code == 200
    payload = r.json()
    assert payload["ready"] is True
    assert payload["database_connected"] is True


def test_ready_reports_not_ready_when_db_unreachable(monkeypatch):
    """/ready returns 503 + ready=False when the DB cannot be reached.

    The LIVENESS probe (/health) must stay up — only readiness flips.
    """
    _ensure_dev_key(monkeypatch)
    with patch(_PROBE, return_value=False):
        r = client.get("/api/v1/system/ready")
        # liveness unaffected
        h = client.get("/api/v1/system/health")
    assert r.status_code == 503
    payload = r.json()
    assert payload["ready"] is False
    assert payload["database_connected"] is False
    assert h.status_code == 200


def test_info_reflects_real_db_state_connected(monkeypatch):
    """/info database_connected reflects the real probe (True) — not hardcoded False."""
    _ensure_dev_key(monkeypatch)
    with patch(_PROBE, return_value=True):
        r = client.get("/api/v1/system/info", headers=API_HEADERS)
    assert r.status_code == 200
    assert r.json()["database_connected"] is True


def test_info_reflects_real_db_state_disconnected(monkeypatch):
    """/info database_connected reflects the real probe (False) when DB down."""
    _ensure_dev_key(monkeypatch)
    with patch(_PROBE, return_value=False):
        r = client.get("/api/v1/system/info", headers=API_HEADERS)
    assert r.status_code == 200
    assert r.json()["database_connected"] is False
